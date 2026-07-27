"""Oracles for the import-adoption DERIVE binding + approval RESUME (ADR 0028, Slice 3b — Lane A6).

Direct-DB (needs Postgres; skips locally, runs under CI / `just test`). The scene-packet author + QA
agents are mocked exactly as tests/test_scene_packet.py mocks them, so derive runs without the LLM stack;
the revision Job need only reach `queued` (3b never executes it).

The four required oracles:
  * derive binds ScenePacket.source_scene_id from the adoption's seed_bindings (Q9);
  * an adoption-derived seed with NO binding fails CLOSED (a blocked packet, source_scene_id NULL) —
    never a scene_no fallback;
  * approving an adoption-linked ScenePacket advances its waiting RevisionRequest
    awaiting_contract -> queued with a linked revise Job (Q2/Q18);
  * a prose-hash or adoption source-fingerprint mismatch at approval declines the resume (the packet
    still approves; the request stays awaiting_contract with no job) — the fail-closed revalidation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import scene_packets as sp_router
from dominion.shared.enums import (
    ImportAdoptionStatus,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    ScenePacketStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    ImportAdoption,
    Job,
    RevisionRequest,
    Scene,
    ScenePacket,
)
from dominion.shared.prose_fingerprint import chapter_source_fingerprint
from dominion.workers.revision import _accept_revision_request_locked, prose_hash
from dominion.workers.scene_packet import author as sp_author
from dominion.workers.scene_packet import author_sections as sp_sections
from dominion.workers.scene_packet import derive as sp_derive
from dominion.workers.scene_packet import qa as sp_qa

# ----------------------------------------- seed helpers ------------------------------------------ #


def _scene_body() -> dict:
    """A structurally-valid ScenePacket body (the three load-bearing sections + budget) so derive lands
    a PROPOSED, approvable packet — mirrors tests/test_scene_packet._scene_body."""
    mole = "Serra is the mole"
    return {
        "scene_no": 1,
        "scene_job": "Marcus intercepts.",
        "scene_type": "combat",
        "word_budget": {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        "known_before_scene": {"reader": ["the route"], "pov": ["the route"], "omniscient_author": [mole]},
        "learned_during_scene": {
            "reader_must_learn": ["the cohort is converging"],
            "reader_may_learn": [],
            "reader_may_infer_only": [],
        },
        "must_remain_hidden": {"reader": [mole], "pov": [], "all_surface_prose": []},
        "pov_permissions": {"may_notice": [], "may_infer": [], "must_not_know": [mole], "may_be_wrong_about": []},
        "required_beats": ["land the hit"],
        "forbidden_beats": ["Marcus uses his Aspect"],
        "exit_state": "both wounded",
    }


def _patch_scene_agents(monkeypatch) -> None:
    """Fake BOTH author entry points (sectioned default + monolithic fallback), the QA agent, and the
    prefix primes, so a derive runs entirely offline."""

    async def fake_author(**_kw):
        return _scene_body()

    async def fake_qa(_b, **_kw):
        return {"verdict": "approve", "residual_risks": [], "issues": []}

    async def noop_prime(*_a, **_kw):
        return None

    monkeypatch.setattr(sp_author, "author_scene_packet", fake_author)
    monkeypatch.setattr(sp_sections, "author_scene_packet_sectioned", fake_author)
    monkeypatch.setattr(sp_qa, "qa_scene_packet", fake_qa)
    monkeypatch.setattr(sp_sections, "prime_author_shared_prefix", noop_prime)
    monkeypatch.setattr(sp_qa, "prime_qa_shared_prefix", noop_prime)


async def _seed_imported_chapter(
    s, *, prose: str = "Imported scene one. The vault gate stands open."
) -> tuple[Book, Chapter, Scene]:
    """A book/chapter + one imported (uncontracted) scene."""
    book = Book(title="Adoption Resume")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline="o")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, version=1, prose=prose, status=SceneStatus.PENDING_REVIEW)
    s.add(scene)
    await s.flush()
    return book, ch, scene


async def _current_fingerprint(s, chapter_id: uuid.UUID) -> str:
    rows = (
        await s.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return chapter_source_fingerprint((int(r[0]), r[1], int(r[2]), r[3]) for r in rows)


async def _adoption_scaffold(
    s, book: Book, ch: Chapter, scene: Scene, *, seed_id: str, bind: bool = True, source_fingerprint: str | None = None
) -> tuple[ChapterPacket, ImportAdoption]:
    """An APPROVED ChapterPacket (one scene_seed) + the ImportAdoption that produced it, finalized to
    `contract_proposed` and linked. `bind` writes the seed->imported-scene binding into seed_bindings;
    `source_fingerprint` overrides the captured chapter fingerprint (default: the live one)."""
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={
            "scene_seeds": [{"seed_id": seed_id, "scene_no": scene.scene_no, "scene_job": "Reconstruct beat."}],
            "characters_present": ["Marcus", "Serra"],
            "characters_absent": [],
            "canon_locks": [],
        },
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    bindings = {seed_id: {"scene_no": scene.scene_no, "scene_id": str(scene.id)}} if bind else {}
    adoption = ImportAdoption(
        book_id=book.id,
        chapter_id=ch.id,
        mode="initial",
        status=ImportAdoptionStatus.CONTRACT_PROPOSED.value,
        source_fingerprint=(
            source_fingerprint if source_fingerprint is not None else await _current_fingerprint(s, ch.id)
        ),
        liveness_basis="operator_independent",
        chapter_packet_id=cp.id,
        seed_bindings=bindings or None,
        finished_at=datetime.now(UTC),
    )
    s.add(adoption)
    await s.flush()
    return cp, adoption


async def _one_scene_packet(s, chapter_id: uuid.UUID) -> ScenePacket:
    return (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars().one()


# --------------------------------------------- oracles ------------------------------------------- #


async def test_derive_binds_source_scene_id_from_seed_bindings(db_factory, monkeypatch):
    """Q9: deriving scene packets from an adoption-produced chapter packet copies
    seed_bindings[seed_id].scene_id onto ScenePacket.source_scene_id — the JOIN key the resume uses."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scene = await _seed_imported_chapter(s)
        seed_id = str(uuid.uuid4())
        cp, _adoption = await _adoption_scaffold(s, book, ch, scene, seed_id=seed_id, bind=True)

        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()

        assert counts["created"] == 1 and counts["blocked"] == 0
        sp = await _one_scene_packet(s, ch.id)
        assert sp.status == ScenePacketStatus.PROPOSED
        assert sp.scene_seed_id == uuid.UUID(seed_id)
        assert sp.source_scene_id == scene.id  # bound back to the imported scene (Q9)


async def test_adoption_derived_seed_without_binding_fails_closed(db_factory, monkeypatch):
    """Q9: an adoption-derived packet whose seed has NO binding cannot resolve a source_scene_id, so it
    fails CLOSED — a BLOCKED packet with source_scene_id NULL and a validation-source reason — never a
    scene_no fallback and never a silently-unbound approved packet."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scene = await _seed_imported_chapter(s)
        seed_id = str(uuid.uuid4())
        cp, _adoption = await _adoption_scaffold(s, book, ch, scene, seed_id=seed_id, bind=False)

        counts = await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()

        assert counts["created"] == 1 and counts["blocked"] == 1
        sp = await _one_scene_packet(s, ch.id)
        assert sp.status == ScenePacketStatus.BLOCKED
        assert sp.source_scene_id is None
        warnings = sp.qa_warnings or {}
        assert "source-scene binding" in (warnings.get("blocked_reason") or "")
        assert warnings.get("blocker_source") == "validation"


async def test_approval_of_adoption_linked_packet_queues_the_waiting_request(db_factory, monkeypatch):
    """Q2/Q18: the loop closes. An imported scene's revise sits at awaiting_contract; deriving + approving
    its adoption-derived ScenePacket advances that request awaiting_contract -> queued with a linked revise
    Job (job.revision_request_id + target_scene_id), and links the serving adoption. The Job need not run."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scene = await _seed_imported_chapter(s)
        seed_id = str(uuid.uuid4())
        cp, adoption = await _adoption_scaffold(s, book, ch, scene, seed_id=seed_id, bind=True)

        # A durable revise landed at awaiting_contract for the uncontracted import (Slice 2).
        accepted = await _accept_revision_request_locked(
            s,
            scene=scene,
            feedback="tighten the open",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        assert accepted.request.status == RevisionRequestStatus.AWAITING_CONTRACT.value
        assert accepted.request.job_id is None
        request_id, adoption_id = accepted.request.id, adoption.id

        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = await _one_scene_packet(s, ch.id)
        assert sp.source_scene_id == scene.id

        # Approve through the router endpoint: run_under_chapter_workflow owns the lock + commit + resume.
        await sp_router.approve_scene_packet(sp.id, BackgroundTasks(), s)

        request = await s.get(RevisionRequest, request_id)
        assert request.status == RevisionRequestStatus.QUEUED.value
        assert request.job_id is not None
        assert request.import_adoption_id == adoption_id
        job = await s.get(Job, request.job_id)
        assert job is not None
        assert job.revision_request_id == request_id
        assert job.target_scene_id == scene.id
        # The packet actually approved (the resume rides on a real approval, not instead of it).
        await s.refresh(sp)
        assert sp.status == ScenePacketStatus.APPROVED


async def test_stale_source_fingerprint_at_approval_declines_the_resume(db_factory, monkeypatch):
    """Fail-closed revalidation: if the producing adoption's captured source fingerprint no longer matches
    the chapter, the approval still succeeds but the resume is a NO-OP — the request stays awaiting_contract
    with no job (a drifted source must not silently queue a revision off a stale reconstruction)."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scene = await _seed_imported_chapter(s)
        seed_id = str(uuid.uuid4())
        # The adoption fingerprint is stale (cannot match the live chapter) — prose is otherwise unchanged.
        cp, _adoption = await _adoption_scaffold(
            s, book, ch, scene, seed_id=seed_id, bind=True, source_fingerprint="stale-does-not-match"
        )
        accepted = await _accept_revision_request_locked(
            s,
            scene=scene,
            feedback="x",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        request_id = accepted.request.id
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = await _one_scene_packet(s, ch.id)

        await sp_router.approve_scene_packet(sp.id, BackgroundTasks(), s)

        request = await s.get(RevisionRequest, request_id)
        assert request.status == RevisionRequestStatus.AWAITING_CONTRACT.value  # resume declined
        assert request.job_id is None
        assert (await s.execute(select(Job).where(Job.target_scene_id == scene.id))).scalars().first() is None
        await s.refresh(sp)
        assert sp.status == ScenePacketStatus.APPROVED  # approval itself still succeeded


async def test_prose_edit_after_request_declines_the_resume(db_factory, monkeypatch):
    """Fail-closed revalidation: the request pinned the source prose it was raised against. If the scene
    prose is edited before the contract is approved, the pinned prose-hash no longer matches, so the resume
    declines — the request stays awaiting_contract (its concurrency token is the whole point)."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scene = await _seed_imported_chapter(s)
        seed_id = str(uuid.uuid4())
        cp, _adoption = await _adoption_scaffold(s, book, ch, scene, seed_id=seed_id, bind=True)
        accepted = await _accept_revision_request_locked(
            s,
            scene=scene,
            feedback="x",
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.REVIEW,
        )
        request_id = accepted.request.id
        await sp_derive.derive_scene_packets(s, packet=cp)

        # Edit the source prose AFTER the request pinned its snapshot (the in-place inbox hand-edit path).
        scene.prose = "Imported scene one, now hand-edited to something else entirely."
        await s.commit()
        sp = await _one_scene_packet(s, ch.id)

        await sp_router.approve_scene_packet(sp.id, BackgroundTasks(), s)

        request = await s.get(RevisionRequest, request_id)
        assert request.status == RevisionRequestStatus.AWAITING_CONTRACT.value  # resume declined
        assert request.job_id is None
