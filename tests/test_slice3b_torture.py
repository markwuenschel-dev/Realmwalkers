"""Torture / no-inert-layer / fail-lock-order oracles for the import-adoption engine (ADR-0028 Slice 3b
— Lane A7). Direct-DB (needs Postgres; skips locally, runs under CI / `just test`). The scene author + QA
agents are mocked exactly as tests/test_import_adoption_resume.py mocks them, so derive/approve run offline.

The build lanes already pin the happy path and each individual fail-closed path. This file hardens the
CROSS-CUTTING invariants those per-path tests do not, and would catch a regression that quietly turns a
load-bearing field inert or inverts the lock order:

  * NO-INERT-LAYER (source_scene_id is READ as the request JOIN KEY, not merely written): batch-approving
    two adoption-derived packets routes EACH packet's resume to its OWN bound scene's waiting request — a
    join that ignored source_scene_id (e.g. keyed on chapter) could not keep the two apart.
  * NO-INERT-LAYER (source_scene_id is a live GATE): an ordinary planning-path packet whose source_scene_id
    is NULL never resumes a waiting request, even once its scene IS contracted — proving the `is None`
    guard is consulted, not decorative.
  * FAIL-LOCK-ORDER (chapter lock precedes the row mutation): approving a scene packet while the per-chapter
    workflow lock is held fails closed with `chapter_workflow_busy` and writes NOTHING (the packet stays
    PROPOSED, the request stays awaiting) — the approval never reached the row because the chapter lock is
    taken first. A source-level guard also pins that all four 3b authority mutations route through the
    wrapper.
  * Q14 end to end: a blocked evidence packet drives the adoption to `failed` with the blocked packet LINKED
    as diagnostic and the immutable evidence shards retained — the worker's blocked->failed publish branch,
    which the per-module tests exercise only through `propose_packet_from_evidence` in isolation.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select

from dominion.api.routers import adoption as adoption_router
from dominion.api.routers import scene_packets as sp_router
from dominion.shared.chapter_lock import acquire_chapter_workflow_lock
from dominion.shared.enums import (
    ImportAdoptionStatus,
    PacketVerdict,
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
    ImportSceneEvidence,
    Job,
    RevisionRequest,
    Scene,
    ScenePacket,
)
from dominion.shared.prose_fingerprint import chapter_source_fingerprint
from dominion.workers import import_adoption
from dominion.workers.import_adoption import run_one_adoption
from dominion.workers.import_evidence import FakeImportEvidenceExtractor
from dominion.workers.packet import author as pkt_author
from dominion.workers.packet import qa as pkt_qa
from dominion.workers.revision import _accept_revision_request_locked, prose_hash
from dominion.workers.scene_packet import author as sp_author
from dominion.workers.scene_packet import author_sections as sp_sections
from dominion.workers.scene_packet import derive as sp_derive
from dominion.workers.scene_packet import qa as sp_qa

# ----------------------------------------- seed helpers ------------------------------------------ #


def _scene_body(scene_no: int = 1) -> dict:
    """A structurally-valid ScenePacket body so derive lands a PROPOSED, approvable packet — mirrors
    tests/test_import_adoption_resume._scene_body (the seed's scene_no is stamped server-side, so a fixed
    body scene_no is normalized per row)."""
    mole = "Serra is the mole"
    return {
        "scene_no": scene_no,
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
    """Fake both scene-author entry points, scene QA, and the prefix primes, so derive runs offline."""

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


async def _seed_imported_chapter(s, *, n_scenes: int = 1, pov: str = "Marcus") -> tuple[Book, Chapter, list[Scene]]:
    """A book/chapter + N imported (uncontracted) scenes with distinct prose."""
    book = Book(title="3b Torture")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov, outline="o")
    s.add(ch)
    await s.flush()
    scenes: list[Scene] = []
    for i in range(1, n_scenes + 1):
        sc = Scene(
            chapter_id=ch.id,
            scene_no=i,
            version=1,
            prose=f"Imported scene {i}. The vault gate stands open on the {i}th turn.",
            status=SceneStatus.PENDING_REVIEW,
        )
        s.add(sc)
        scenes.append(sc)
    await s.flush()
    return book, ch, scenes


async def _current_fingerprint(s, chapter_id: uuid.UUID) -> str:
    rows = (
        await s.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return chapter_source_fingerprint((int(r[0]), r[1], int(r[2]), r[3]) for r in rows)


async def _adoption_chapter_packet(
    s, book: Book, ch: Chapter, scenes: list[Scene], *, bind: bool = True, source_fingerprint: str | None = None
) -> tuple[ChapterPacket, ImportAdoption, list[str]]:
    """An APPROVED ChapterPacket with one seed PER scene + the producing ImportAdoption (finalized to
    `contract_proposed`, linked). `bind` writes each seed->imported-scene binding into seed_bindings.
    Returns (chapter_packet, adoption, seed_ids)."""
    seed_ids = [str(uuid.uuid4()) for _ in scenes]
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={
            "scene_seeds": [
                {"seed_id": seed_ids[i], "scene_no": sc.scene_no, "scene_job": f"Reconstruct beat {sc.scene_no}."}
                for i, sc in enumerate(scenes)
            ],
            "characters_present": ["Marcus", "Serra"],
            "characters_absent": [],
            "canon_locks": [],
        },
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    bindings = (
        {seed_ids[i]: {"scene_no": sc.scene_no, "scene_id": str(sc.id)} for i, sc in enumerate(scenes)} if bind else {}
    )
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
    return cp, adoption, seed_ids


async def _await_request(s, scene: Scene) -> RevisionRequest:
    """Land a durable revise at awaiting_contract for an uncontracted import (Slice 2)."""
    accepted = await _accept_revision_request_locked(
        s,
        scene=scene,
        feedback=f"tighten scene {scene.scene_no}",
        target_pass=None,
        expected_prose_hash=prose_hash(scene.prose),
        origin=RevisionRequestOrigin.REVIEW,
    )
    assert accepted.request.status == RevisionRequestStatus.AWAITING_CONTRACT.value
    assert accepted.request.job_id is None
    return accepted.request


async def _count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# =========================== NO-INERT-LAYER: source_scene_id is READ ============================== #


async def test_resume_routes_each_packet_to_its_own_bound_scene_request(db_factory, monkeypatch):
    """source_scene_id is the live JOIN KEY, per packet. Two imported scenes each carry a waiting
    RevisionRequest; batch-approving the two adoption-derived packets advances EACH request to queued with
    a revise Job pinned to ITS OWN scene. A resume that ignored source_scene_id (e.g. matched the newest
    awaiting request in the chapter) could not route the two packets to two different requests."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scenes = await _seed_imported_chapter(s, n_scenes=2)
        cp, _adoption, _seed_ids = await _adoption_chapter_packet(s, book, ch, scenes, bind=True)
        req1, req2 = await _await_request(s, scenes[0]), await _await_request(s, scenes[1])
        req1_id, req2_id = req1.id, req2.id
        scene1_id, scene2_id = scenes[0].id, scenes[1].id
        chapter_id = ch.id

        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        # both packets bound back to their imported scene (Q9), one per scene
        by_scene = {
            sp.source_scene_id: sp
            for sp in (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars().all()
        }
        assert set(by_scene) == {scene1_id, scene2_id}

        await sp_router.approve_scene_packets(chapter_id, BackgroundTasks(), s)

        r1 = await s.get(RevisionRequest, req1_id)
        r2 = await s.get(RevisionRequest, req2_id)
        assert r1.status == RevisionRequestStatus.QUEUED.value and r1.job_id is not None
        assert r2.status == RevisionRequestStatus.QUEUED.value and r2.job_id is not None
        assert r1.job_id != r2.job_id  # two distinct revise Jobs, not one request resumed twice
        job1, job2 = await s.get(Job, r1.job_id), await s.get(Job, r2.job_id)
        # the load-bearing routing assertion: each request's Job targets the scene its packet was bound to.
        assert job1.target_scene_id == scene1_id
        assert job2.target_scene_id == scene2_id


async def test_ordinary_packet_with_null_source_scene_id_never_resumes(db_factory, monkeypatch):
    """source_scene_id is a live GATE. An ordinary planning-path packet (no producing adoption ->
    source_scene_id NULL) must NOT resume a waiting request even though approving it DOES contract the
    scene — so if the `source_scene_id is None` guard were dropped, the now-contracted scene's request
    would wrongly queue. It staying awaiting_contract proves the guard is consulted."""
    _patch_scene_agents(monkeypatch)
    async with db_factory() as s:
        book, ch, scenes = await _seed_imported_chapter(s, n_scenes=1)
        scene = scenes[0]
        # A plain chapter packet with NO producing ImportAdoption — the planning path, not adoption.
        seed_id = str(uuid.uuid4())
        cp = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status="approved",
            confidence="green",
            body={
                "scene_seeds": [{"seed_id": seed_id, "scene_no": scene.scene_no, "scene_job": "Plan the beat."}],
                "characters_present": ["Marcus", "Serra"],
                "characters_absent": [],
                "canon_locks": [],
            },
            open_questions={"items": []},
        )
        s.add(cp)
        await s.flush()
        request = await _await_request(s, scene)
        request_id = request.id

        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == ch.id))).scalars().one()
        assert sp.source_scene_id is None  # ordinary packet: no adoption binding

        await sp_router.approve_scene_packet(sp.id, BackgroundTasks(), s)

        await s.refresh(sp)
        assert sp.status == ScenePacketStatus.APPROVED  # the scene IS now contracted...
        request = await s.get(RevisionRequest, request_id)
        assert request.status == RevisionRequestStatus.AWAITING_CONTRACT.value  # ...yet the request never queues
        assert request.job_id is None
        assert (await s.execute(select(Job).where(Job.target_scene_id == scene.id))).scalars().first() is None


# ================================= FAIL-LOCK-ORDER: chapter lock first ============================ #


async def test_approve_under_held_chapter_lock_fails_closed_before_approving(db_factory, monkeypatch):
    """The scene-packet approval takes the per-chapter workflow lock BEFORE it mutates the packet row: with
    the lock held by another session, approve fails closed (`409 chapter_workflow_busy`) and writes NOTHING
    — the packet is still PROPOSED and the waiting request is untouched. Taking the row lock first would
    either deadlock or approve; instead it never reaches the row. Then it re-enters cleanly once released."""
    _patch_scene_agents(monkeypatch)
    monkeypatch.setattr(sp_router, "LOCK_TIMEOUT_MS", 250)  # keep the busy wait short
    async with db_factory() as s:
        book, ch, scenes = await _seed_imported_chapter(s, n_scenes=1)
        cp, _adoption, _seed_ids = await _adoption_chapter_packet(s, book, ch, scenes, bind=True)
        request = await _await_request(s, scenes[0])
        request_id, chapter_id = request.id, ch.id
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()
        sp = (await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars().one()
        sp_id = sp.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)  # hold the lock

        async with db_factory() as s:
            with pytest.raises(HTTPException) as ei:
                await sp_router.approve_scene_packet(sp_id, BackgroundTasks(), s)
            assert ei.value.status_code == 409
            assert ei.value.detail["reason"] == "chapter_workflow_busy"

        async with db_factory() as probe:  # nothing was written on the busy path
            unchanged = await probe.get(ScenePacket, sp_id)
            assert unchanged.status == ScenePacketStatus.PROPOSED  # NOT approved — the row was never reached
            req = await probe.get(RevisionRequest, request_id)
            assert req.status == RevisionRequestStatus.AWAITING_CONTRACT.value and req.job_id is None

        await holder.rollback()  # release the advisory lock

    async with db_factory() as s:  # re-enter cleanly once the lock frees
        await sp_router.approve_scene_packet(sp_id, BackgroundTasks(), s)
        approved = await s.get(ScenePacket, sp_id)
        assert approved.status == ScenePacketStatus.APPROVED
        req = await s.get(RevisionRequest, request_id)
        assert req.status == RevisionRequestStatus.QUEUED.value and req.job_id is not None


def test_slice3b_authority_mutations_route_through_the_chapter_workflow_wrapper():
    """Source-level guard: every authority-changing chapter mutation Slice 3b added routes through
    `run_under_chapter_workflow` (which acquires the chapter lock BEFORE running its body — proven in
    tests/test_chapter_workflow_lock). This pins the wrapping so a future edit that approves/starts/
    publishes WITHOUT the wrapper — reintroducing a row lock ahead of the chapter lock — trips CI here.

    ADR-0032 W1: the adoption endpoints now route through the lock TRANSITIVELY via the seam wrapper
    `ensure_import_adoption(...)`, which is itself a thin caller of `run_under_chapter_workflow` — so the
    lock is still acquired first. The `ensure_import_adoption(` token matches only the lock-acquiring
    wrapper, never `ensure_import_adoption_locked(` (which assumes the lock is already held)."""
    lock_routers = ("run_under_chapter_workflow(", "ensure_import_adoption(")
    mutations = (
        adoption_router.start_contract_adoption,
        adoption_router.reauthor_contract_adoption,
        import_adoption.publish_adoption,
        sp_router.approve_scene_packet,
        sp_router.approve_scene_packets,
    )
    for fn in mutations:
        src = inspect.getsource(fn)
        assert any(tok in src for tok in lock_routers), f"{fn.__qualname__} does not route through the chapter lock"


# ==================================== Q14: blocked packet -> failed =============================== #


def _author_thin() -> dict:
    return {"chapter_job": "x", "scene_seeds": [], "claims": []}


def _fixed_retriever(hits):
    async def _retrieve(_query):
        return list(hits)

    return _retrieve


async def test_worker_maps_blocked_packet_to_failed_adoption_with_diagnostic_link(db_factory, monkeypatch):
    """Q14 end to end through the worker: when evidence authoring fails closed to a BLOCKED packet, the
    publish CAS drives the adoption to `failed` with the blocked packet LINKED as diagnostic (never a
    silently dropped block), while the immutable evidence shards survive. seed_bindings /
    author_input_fingerprint are written only on the `contract_proposed` branch, so a failed adoption
    carries neither."""

    async def fake_author(**_kw):
        return _author_thin()

    async def fake_qa(_packet, **_kw):
        return {"verdict": PacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(pkt_author, "author_packet_from_evidence", fake_author)
    monkeypatch.setattr(pkt_qa, "qa_packet", fake_qa)

    async with db_factory() as s:
        book, ch, _scenes = await _seed_imported_chapter(s, n_scenes=1)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                mode="initial",
                status=ImportAdoptionStatus.QUEUED.value,
                source_fingerprint="pending",
                liveness_basis="operator_independent",
            )
        )
        await s.commit()
        chapter_id = ch.id

    assert (
        await run_one_adoption(db_factory, extractor=FakeImportEvidenceExtractor(), retrieve=_fixed_retriever([]))
        is True
    )

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == ImportAdoptionStatus.FAILED.value
        assert adoption.finished_at is not None
        assert adoption.chapter_packet_id is not None  # the blocked packet is linked as diagnostic (Q14)
        assert adoption.seed_bindings is None  # not written on the failed branch
        assert adoption.author_input_fingerprint is None
        linked = await s.get(ChapterPacket, adoption.chapter_packet_id)
        assert linked is not None
        assert str(linked.status) == "blocked"
        assert linked.chapter_id == chapter_id
        # the immutable evidence shard extracted before authoring survives the fail-closed publish.
        assert await _count(s, ImportSceneEvidence) == 1
