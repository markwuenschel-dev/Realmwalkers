"""Characterization tests for the single revise-intent seam `accept_revision_request` (ADR 0028,
Slice 2). Direct-DB (needs Postgres; skips locally, runs under `just test` / CI).

These pin the properties the design promised: durable intent instead of a 409 rollback, one active
request per scene (supersede/replay), stale-source rejection, retry after failure, and the reader
lighting up (a minted revise Job carries `revision_request_id`, so feedback resolves through the
request).
"""

from __future__ import annotations

import pytest
from conftest import seed_scene_packet
from fastapi import BackgroundTasks, HTTPException, Response
from sqlalchemy import func, select

from dominion.api.routers import reviews
from dominion.shared.enums import (
    BeatStatus,
    Decision,
    JobStatus,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import Beat, Book, Chapter, Job, RevisionRequest, Scene
from dominion.shared.schemas import DecisionIn
from dominion.workers.context.revision import load_revision_state
from dominion.workers.revision import accept_revision_request, prose_hash
from dominion.workers.worker import claim_one_job


async def _imported_scene(s, *, prose="Imported prologue prose.") -> Scene:
    """A book/chapter + an imported scene with NO beat/packet (the uncontracted case)."""
    book = Book(title="Revision Seam")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, prose=prose, version=1, status=SceneStatus.PENDING_REVIEW)
    s.add(scene)
    await s.flush()
    return scene


async def _contracted_scene(s, *, prose="Drafted scene prose.") -> Scene:
    """A book/chapter + an APPROVED beat + approved ScenePacket + scene (the contracted case)."""
    book = Book(title="Revision Seam")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    scene = Scene(chapter_id=ch.id, scene_no=1, prose=prose, version=1, status=SceneStatus.PENDING_REVIEW)
    s.add(scene)
    await s.flush()
    await seed_scene_packet(s, chapter=ch, beat=beat)
    return scene


async def _active_count(s, scene_id) -> int:
    return (
        await s.execute(
            select(func.count())
            .select_from(RevisionRequest)
            .where(
                RevisionRequest.target_scene_id == scene_id,
                RevisionRequest.status.in_(
                    (
                        RevisionRequestStatus.AWAITING_CONTRACT.value,
                        RevisionRequestStatus.QUEUED.value,
                        RevisionRequestStatus.RUNNING.value,
                    )
                ),
            )
        )
    ).scalar_one()


async def test_imported_scene_lands_durable_awaiting_contract_not_a_409(db_factory):
    """The headline inversion: an uncontracted import no longer 409-rolls-back — it persists durable
    intent at awaiting_contract with no job (nothing advances it until Slice 3's adoption)."""
    async with db_factory() as s:
        scene = await _imported_scene(s)
        result = await accept_revision_request(
            s,
            scene=scene,
            feedback="tighten the open",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        await s.commit()

        assert result.replayed is False
        req = result.request
        assert req.status == RevisionRequestStatus.AWAITING_CONTRACT.value
        assert req.job_id is None
        assert req.target_prose_hash == prose_hash(scene.prose)
        assert req.target_scene_version == scene.version
        assert await _active_count(s, scene.id) == 1


async def test_second_revise_supersedes_the_first_one_active_per_scene(db_factory):
    async with db_factory() as s:
        scene = await _imported_scene(s)
        first = await accept_revision_request(
            s,
            scene=scene,
            feedback="v1",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        second = await accept_revision_request(
            s,
            scene=scene,
            feedback="v2 — different intent",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        await s.commit()

        assert second.replayed is False
        assert (await s.get(RevisionRequest, first.request.id)).status == RevisionRequestStatus.SUPERSEDED.value
        assert await _active_count(s, scene.id) == 1  # the partial unique index invariant


async def test_exact_replay_returns_the_existing_request_200(db_factory):
    async with db_factory() as s:
        scene = await _imported_scene(s)
        first = await accept_revision_request(
            s,
            scene=scene,
            feedback="same",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        replay = await accept_revision_request(
            s,
            scene=scene,
            feedback="same",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        await s.commit()

        assert replay.replayed is True  # -> HTTP 200
        assert replay.request.id == first.request.id
        total = (
            await s.execute(
                select(func.count()).select_from(RevisionRequest).where(RevisionRequest.target_scene_id == scene.id)
            )
        ).scalar_one()
        assert total == 1  # no duplicate row


async def test_stale_source_is_rejected_and_persists_nothing(db_factory):
    async with db_factory() as s:
        scene = await _imported_scene(s)
        with pytest.raises(HTTPException) as exc:
            await accept_revision_request(
                s,
                scene=scene,
                feedback="x",
                target_pass=None,
                expected_prose_hash="not-the-current-hash",
                origin=RevisionRequestOrigin.REVIEW,
            )
        assert exc.value.status_code == 409
        # Roll back the poisoned transaction, then confirm nothing landed.
        await s.rollback()
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0


async def test_missing_expected_hash_is_malformed_422(db_factory):
    async with db_factory() as s:
        scene = await _imported_scene(s)
        with pytest.raises(HTTPException) as exc:
            await accept_revision_request(
                s,
                scene=scene,
                feedback="x",
                target_pass=None,
                expected_prose_hash=None,
                origin=RevisionRequestOrigin.REVIEW,
            )
        assert exc.value.status_code == 422


async def test_retry_after_a_failed_request_creates_a_fresh_one(db_factory):
    async with db_factory() as s:
        scene = await _imported_scene(s)
        first = await accept_revision_request(
            s,
            scene=scene,
            feedback="v1",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        # A terminal (failed) request must NOT block a fresh revise — Retry is a separate action.
        first.request.status = RevisionRequestStatus.FAILED.value
        await s.flush()

        again = await accept_revision_request(
            s,
            scene=scene,
            feedback="v1",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        await s.commit()
        assert again.replayed is False
        assert again.request.id != first.request.id
        assert await _active_count(s, scene.id) == 1


async def test_contracted_scene_mints_a_linked_revise_job_and_lights_up_the_reader(db_factory):
    """The D8 + reader property: a contracted scene advances to queued, the minted job is an explicit
    revise job carrying revision_request_id, and load_revision_state resolves feedback through it."""
    async with db_factory() as s:
        scene = await _contracted_scene(s)
        result = await accept_revision_request(
            s,
            scene=scene,
            feedback="Cut the throat-clearing.",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        await s.commit()

        req = result.request
        assert req.status == RevisionRequestStatus.QUEUED.value
        assert req.job_id is not None
        job = await s.get(Job, req.job_id)
        assert job is not None and job.revision_request_id == req.id and job.target_scene_id == scene.id

        # The dormant reader now fires: feedback comes from the linked request, not the Approval fallback.
        state = await load_revision_state(s, job)
        assert state.revise_feedback == "Cut the throat-clearing."


async def test_running_revision_refuses_a_different_revise_no_second_job(db_factory):
    """revision_in_progress is now REACHABLE (P1 fix): with the request RUNNING — as the worker's claim
    sets it — a different-feedback revise is a 409, not a silent supersede that spawns a second job."""
    async with db_factory() as s:
        scene = await _contracted_scene(s)
        first = await accept_revision_request(
            s,
            scene=scene,
            feedback="v1",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        # Simulate the worker having claimed the job: job + request both RUNNING (committed).
        job = await s.get(Job, first.request.job_id)
        job.status = JobStatus.RUNNING
        first.request.status = RevisionRequestStatus.RUNNING.value
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await accept_revision_request(
                s,
                scene=scene,
                feedback="v2 — a different intent",
                target_pass=None,
                expected_prose_hash=prose_hash(scene.prose),
                origin=RevisionRequestOrigin.REVIEW,
            )
        assert exc.value.status_code == 409  # revision_in_progress — a CONFLICT raised before any write
        job_count = (
            await s.execute(select(func.count()).select_from(Job).where(Job.target_scene_id == scene.id))
        ).scalar_one()
        assert job_count == 1  # no second concurrent revise job


async def test_claiming_a_revise_job_marks_its_request_running(db_factory):
    """The worker's claim mirrors onto the linked request (ADR 0028) — which is exactly what makes the
    RUNNING taxonomy branch reachable in the real flow, not just the simulated one above."""
    async with db_factory() as s:
        scene = await _contracted_scene(s)
        req = (
            await accept_revision_request(
                s,
                scene=scene,
                feedback="v1",
                target_pass=None,
                expected_prose_hash=prose_hash(scene.prose),
                origin=RevisionRequestOrigin.REVIEW,
            )
        ).request
        await s.commit()

        claimed = await claim_one_job(s)
        assert claimed is not None and claimed.id == req.job_id
        await s.refresh(req)  # the mirror is a bare UPDATE; re-read to see it
        assert req.status == RevisionRequestStatus.RUNNING.value


async def test_approve_cancels_an_active_revision_request_and_its_queued_job(db_factory):
    """P1 fix: inbox APPROVE is the interim escape, but it must not leave an orphan active request beside
    APPROVED canon (the partial unique index / Slice-3 poison)."""
    async with db_factory() as s:
        scene = await _contracted_scene(s)
        req = (
            await accept_revision_request(
                s,
                scene=scene,
                feedback="please revise",
                target_pass=None,
                expected_prose_hash=prose_hash(scene.prose),
                origin=RevisionRequestOrigin.REVIEW,
            )
        ).request
        job_id = req.job_id
        await s.commit()

        await reviews.decide(scene.id, DecisionIn(decision=Decision.APPROVE), s, BackgroundTasks(), Response())
        await s.commit()

        assert (await s.get(RevisionRequest, req.id)).status == RevisionRequestStatus.CANCELLED.value
        assert await _active_count(s, scene.id) == 0
        assert await s.get(Job, job_id) is None  # the unclaimed queued job was cancelled
