"""ADR-0032 W2 — the chapter-locked scene-approval command + adoption reverse-reconciliation.

W2 wires the LIVE reverse (demand-removal) path before any request-bound minter exists (D13, v2.2):

  * `reconcile_adoption_demand_locked` (adoption-owned, D9) — cancels a request_bound awaiting_start/queued
    adoption with zero qualifying active requests; preserves operator_independent, running, and terminal
    work; FAILS CLOSED (raises, rolling the txn back) on an indeterminate read;
  * `accept_scene_approval` (the coordinator, D4) — one chapter-locked atomic transaction: approve + cancel
    the scene's active requests + reverse-reconcile adoption demand, or roll back ALL of them together;
  * lock ordering — the advisory lock is acquired FIRST, so no row lock / mutation happens outside it;
  * response parity — the APPROVE response body is byte-identical to pre-W2.

Reverse cancellation is DORMANT on current data (no request_bound adoption exists before W3's minter); the
`request_bound` rows below are seeded directly to prove the guard fires when W3 lands.
"""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, Response
from sqlalchemy import func, select

from dominion.api.routers import reviews
from dominion.shared import adoption_entry, chapter_lock
from dominion.shared.adoption_entry import (
    IndeterminateAdoptionDemand,
    reconcile_adoption_demand_locked,
)
from dominion.shared.chapter_lock import ChapterWorkflowBusy
from dominion.shared.enums import (
    Decision,
    ImportAdoptionStatus,
    JobStatus,
    LivenessBasis,
    ReconcileDemandOutcome,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Approval,
    Book,
    Chapter,
    ImportAdoption,
    Job,
    RevisionRequest,
    Scene,
)
from dominion.shared.schemas import DecisionIn


async def _seed_chapter(s, *, pov: str = "Marcus") -> tuple[Book, Chapter]:
    book = Book(title="ADR-0032 W2")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov)
    s.add(ch)
    await s.flush()
    return book, ch


async def _imported_scene(s, ch, *, prose: str = "Imported prose.") -> Scene:
    scene = Scene(chapter_id=ch.id, scene_no=1, prose=prose, version=1, status=SceneStatus.PENDING_REVIEW)
    s.add(scene)
    await s.flush()
    return scene


def _adoption(book, ch, *, status: str, basis: str) -> ImportAdoption:
    return ImportAdoption(
        book_id=book.id, chapter_id=ch.id, status=status, liveness_basis=basis, source_fingerprint="w2"
    )


def _active_request(book, ch, scene) -> RevisionRequest:
    return RevisionRequest(
        book_id=book.id,
        chapter_id=ch.id,
        target_scene_id=scene.id,
        scene_no=scene.scene_no,
        target_scene_version=scene.version,
        target_prose_hash="h",
        origin=RevisionRequestOrigin.REVIEW.value,
        status=RevisionRequestStatus.AWAITING_CONTRACT.value,
    )


async def _decide_approve(session, scene_id):
    """Drive the REAL /decision APPROVE route (router dispatch -> accept_scene_approval)."""
    return await reviews.decide(
        scene_id,
        DecisionIn(decision=Decision.APPROVE),
        session,
        BackgroundTasks(),
        Response(),
    )


# --------------------------------------------------------------------------- reconcile: adoption-owned (D9)


async def test_reconcile_no_active_adoption_is_a_clean_no_op(db_factory):
    async with db_factory() as s:
        _, ch = await _seed_chapter(s)
        await s.commit()
        assert await reconcile_adoption_demand_locked(s, ch.id) is ReconcileDemandOutcome.NO_ACTIVE_ADOPTION


async def test_reconcile_preserves_operator_independent(db_factory):
    """An operator_independent adoption is durable demand in its own right — never auto-cancelled (D2/D9)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        s.add(
            _adoption(
                book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.OPERATOR_INDEPENDENT.value
            )
        )
        await s.commit()

        outcome = await reconcile_adoption_demand_locked(s, ch.id)
        assert outcome is ReconcileDemandOutcome.PRESERVED_NON_REQUEST_BOUND
        row = (await s.execute(select(ImportAdoption))).scalar_one()
        assert row.status == ImportAdoptionStatus.QUEUED.value  # untouched


async def test_reconcile_preserves_running(db_factory):
    """A running adoption finishes — interrupting a mid-model-call claim is out of scope (D10)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        s.add(_adoption(book, ch, status=ImportAdoptionStatus.RUNNING.value, basis=LivenessBasis.REQUEST_BOUND.value))
        await s.commit()

        outcome = await reconcile_adoption_demand_locked(s, ch.id)
        assert outcome is ReconcileDemandOutcome.PRESERVED_RUNNING
        row = (await s.execute(select(ImportAdoption))).scalar_one()
        assert row.status == ImportAdoptionStatus.RUNNING.value  # untouched


async def test_reconcile_preserves_when_active_demand_remains(db_factory):
    """A request_bound adoption with a qualifying active request still has demand (count>0 is valid)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        s.add(_adoption(book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.REQUEST_BOUND.value))
        s.add(_active_request(book, ch, scene))
        await s.commit()

        outcome = await reconcile_adoption_demand_locked(s, ch.id)
        assert outcome is ReconcileDemandOutcome.PRESERVED_ACTIVE_DEMAND
        row = (await s.execute(select(ImportAdoption))).scalar_one()
        assert row.status == ImportAdoptionStatus.QUEUED.value  # untouched


@pytest.mark.parametrize("status", [ImportAdoptionStatus.QUEUED.value, ImportAdoptionStatus.AWAITING_START.value])
async def test_reconcile_cancels_request_bound_orphan(db_factory, status):
    """A request_bound awaiting_start/queued adoption with ZERO qualifying active requests is cancelled (D9)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        s.add(_adoption(book, ch, status=status, basis=LivenessBasis.REQUEST_BOUND.value))
        await s.commit()

        outcome = await reconcile_adoption_demand_locked(s, ch.id)
        assert outcome is ReconcileDemandOutcome.CANCELLED
        row = (await s.execute(select(ImportAdoption))).scalar_one()
        assert row.status == ImportAdoptionStatus.CANCELLED.value


async def test_reconcile_fails_closed_on_multiple_active_adoptions(db_factory, monkeypatch):
    """>1 active adoption (a broken partial-unique invariant) is indeterminate — it raises, never guesses."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        fake = [
            _adoption(book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.REQUEST_BOUND.value),
            _adoption(book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.REQUEST_BOUND.value),
        ]

        async def _two(_session, _chapter_id):
            return fake

        monkeypatch.setattr(adoption_entry, "_active_adoptions_for_chapter", _two)
        with pytest.raises(IndeterminateAdoptionDemand):
            await reconcile_adoption_demand_locked(s, ch.id)


# --------------------------------------------------------------------------- the real APPROVE route (D4)


async def test_approve_cancels_request_and_reverse_cancels_adoption_atomically(db_factory):
    """The real APPROVE route removes demand: scene approved, active request cancelled, its queued job
    deleted, and the request_bound adoption reverse-cancelled — all committed together (acceptance #2, #4)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        req = _active_request(book, ch, scene)
        s.add(req)
        await s.flush()
        job = Job(
            kind="revise_full",
            token_budget=1000,
            status=JobStatus.QUEUED.value,
            target_scene_id=scene.id,
            book_id=book.id,
            chapter_id=ch.id,
        )
        s.add(job)
        await s.flush()
        req.job_id = job.id
        s.add(_adoption(book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.REQUEST_BOUND.value))
        await s.commit()
        scene_id, req_id, job_id = scene.id, req.id, job.id

    async with db_factory() as s2:
        body = await _decide_approve(s2, scene_id)
        assert body == {"scene": str(scene_id), "status": SceneStatus.APPROVED.value, "next_job": None}

    async with db_factory() as s3:
        assert (await s3.get(Scene, scene_id)).status == SceneStatus.APPROVED
        assert (await s3.get(RevisionRequest, req_id)).status == RevisionRequestStatus.CANCELLED.value
        assert await s3.get(Job, job_id) is None  # queued (unclaimed) job deleted
        assert (await s3.execute(select(ImportAdoption))).scalar_one().status == ImportAdoptionStatus.CANCELLED.value


async def test_approve_rolls_back_everything_when_reconcile_fails(db_factory, monkeypatch):
    """A failure AFTER request cancellation rolls the WHOLE approval back — scene, request, job, and
    adoption all unchanged. Fail-closed reverse reconciliation never leaves partial state (acceptance #3, #7)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        req = _active_request(book, ch, scene)
        s.add(req)
        s.add(_adoption(book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.REQUEST_BOUND.value))
        await s.commit()
        scene_id, req_id = scene.id, req.id

    async def _boom(_session, _chapter_id):
        raise IndeterminateAdoptionDemand(_chapter_id, "injected failed demand read")

    monkeypatch.setattr(reviews, "reconcile_adoption_demand_locked", _boom)

    async with db_factory() as s2:
        with pytest.raises(IndeterminateAdoptionDemand):
            await _decide_approve(s2, scene_id)

    async with db_factory() as s3:
        assert (await s3.get(Scene, scene_id)).status == SceneStatus.PENDING_REVIEW  # NOT approved
        assert (await s3.get(RevisionRequest, req_id)).status == RevisionRequestStatus.AWAITING_CONTRACT.value
        assert (await s3.execute(select(ImportAdoption))).scalar_one().status == ImportAdoptionStatus.QUEUED.value
        assert (await s3.execute(select(func.count()).select_from(Approval))).scalar_one() == 0  # no Approval landed


async def test_approve_acquires_chapter_lock_before_any_mutation(db_factory, monkeypatch):
    """If the chapter workflow lock cannot be acquired, NOTHING is mutated — proving every row lock and write
    happens INSIDE the lock (the advisory lock precedes them all, acceptance #1)."""
    async with db_factory() as s:
        _, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        await s.commit()
        scene_id, ch_id = scene.id, ch.id

    async def _busy(_session, _chapter_id, **_kw):
        raise ChapterWorkflowBusy(ch_id)

    monkeypatch.setattr(chapter_lock, "acquire_chapter_workflow_lock", _busy)

    async with db_factory() as s2:
        with pytest.raises(ChapterWorkflowBusy):
            await _decide_approve(s2, scene_id)

    async with db_factory() as s3:
        assert (await s3.get(Scene, scene_id)).status == SceneStatus.PENDING_REVIEW  # untouched
        assert (await s3.execute(select(func.count()).select_from(Approval))).scalar_one() == 0


async def test_approve_response_unchanged_and_operator_independent_preserved(db_factory):
    """The APPROVE response is byte-identical to pre-W2 (scene/status/next_job only, no revision keys), and a
    non-request-bound adoption is preserved through a normal approve (acceptance #5, #6, #8)."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        s.add(
            _adoption(
                book, ch, status=ImportAdoptionStatus.QUEUED.value, basis=LivenessBasis.OPERATOR_INDEPENDENT.value
            )
        )
        await s.commit()
        scene_id = scene.id

    async with db_factory() as s2:
        body = await _decide_approve(s2, scene_id)
        assert set(body) == {"scene", "status", "next_job"}  # no revision_request/revision_status/display_phase
        assert body["status"] == SceneStatus.APPROVED.value

    async with db_factory() as s3:
        assert (await s3.get(Scene, scene_id)).status == SceneStatus.APPROVED
        assert (await s3.execute(select(ImportAdoption))).scalar_one().status == ImportAdoptionStatus.QUEUED.value


async def test_reapprove_is_idempotent(db_factory):
    """Re-approving an already-approved scene does not error and does not re-run one-shot effects; with no
    active adoption the reverse reconcile is a clean no-op."""
    async with db_factory() as s:
        _, ch = await _seed_chapter(s)
        scene = await _imported_scene(s, ch)
        await s.commit()
        scene_id = scene.id

    async with db_factory() as s2:
        await _decide_approve(s2, scene_id)
    async with db_factory() as s3:
        body = await _decide_approve(s3, scene_id)  # second approve
        assert body["status"] == SceneStatus.APPROVED.value
