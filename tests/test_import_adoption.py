"""Oracles for the import-adoption worker (ADR 0028, Slice 3b — Lane A4). Direct-DB (needs Postgres;
skips locally, runs under CI / `just test`). The author + QA agents are mocked exactly as
test_packet_evidence mocks them, and the evidence extractor + canon retriever are injected, so the whole
claim -> evidence -> author -> publish pipeline runs without the LLM stack.

The four required oracles:
  * claim -> propose -> publish reaches `contract_proposed` with the packet linked, seed_bindings +
    author_input_fingerprint written, and the evidence manifest filled;
  * tiered-idempotency REUSE: a second identical adoption reuses the existing proposed packet with NO
    second author call and NO re-extraction;
  * fingerprint-drift at publish INVALIDATES the pass and deletes the packet, while the evidence shards
    survive;
  * a busy per-chapter workflow lock raises ChapterWorkflowBusy, writes nothing, and re-enters cleanly.
Plus amendment mode's boundary condition: a chapter with no approved contract cannot be amended (#261 W2a
replaced the old blanket "amendment is not implemented" refusal with the real copy-on-write author pass).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from dominion.shared.chapter_lock import ChapterWorkflowBusy, acquire_chapter_workflow_lock
from dominion.shared.enums import PacketVerdict, SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, ImportAdoption, ImportSceneEvidence, Scene
from dominion.shared.prose_fingerprint import chapter_source_fingerprint
from dominion.workers import import_adoption
from dominion.workers.evidence_store import ensure_scene_evidence
from dominion.workers.import_adoption import publish_adoption, run_one_adoption
from dominion.workers.import_evidence import FakeImportEvidenceExtractor
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod

# ----------------------------------------- seed helpers ------------------------------------------ #


async def _seed(s, *, n_scenes: int = 2, pov: str = "Marcus") -> tuple[Book, Chapter, list[Scene]]:
    book = Book(title="Adoption Work")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov, outline=None)
    s.add(ch)
    await s.flush()
    scenes = []
    for i in range(1, n_scenes + 1):
        sc = Scene(
            chapter_id=ch.id,
            scene_no=i,
            version=1,
            prose=f"Imported prose for scene {i}. The gate at the vault stands open.",
            status=SceneStatus.PENDING_REVIEW,
        )
        s.add(sc)
        scenes.append(sc)
    await s.flush()
    return book, ch, scenes


def _adoption(book: Book, ch: Chapter, **kw) -> ImportAdoption:
    return ImportAdoption(
        book_id=book.id,
        chapter_id=ch.id,
        mode=kw.pop("mode", "initial"),
        status=kw.pop("status", "queued"),
        source_fingerprint=kw.pop("source_fingerprint", "pending"),
        liveness_basis=kw.pop("liveness_basis", "operator_independent"),
        **kw,
    )


def _author_packet(n_scenes: int = 2) -> dict:
    return {
        "confidence": "green",
        "chapter_job": "Reconstruct the chapter contract from the imported prose",
        "exit_state": "the gate stands open",
        "scene_seeds": [
            {"scene_no": i, "scene_job": f"Establish beat {i} at the vault gate."} for i in range(1, n_scenes + 1)
        ],
        "claims": [
            {"claim": "the gate exists", "source_strength": "LOCKED_CANON", "source_id": "C1"},
            {"claim": "the gate was breached", "source_strength": "DERIVED_FROM_MANUSCRIPT", "source_id": "M1"},
        ],
        "open_questions": [],
    }


def _qa() -> dict:
    return {"verdict": PacketVerdict.APPROVE, "residual_risks": ["keep the antagonist unnamed"], "issues": []}


def _fixed_retriever(hits):
    async def _retrieve(_query):
        return list(hits)

    return _retrieve


def _patch_author(monkeypatch, *, packet: dict, counter: dict | None = None) -> None:
    async def fake_author(**kwargs):
        if counter is not None:
            counter["n"] += 1
        return packet

    async def fake_qa(_packet, **kwargs):
        return _qa()

    monkeypatch.setattr(author_mod, "author_packet_from_evidence", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


async def _count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------- oracles ------------------------------------------- #


async def test_claim_propose_publish_reaches_contract_proposed(db_factory, monkeypatch):
    """The end-to-end happy path: a queued INITIAL adoption is claimed, per-scene evidence is checkpointed,
    a proposed ChapterPacket is authored, and the publish CAS finalizes the adoption to `contract_proposed`
    with the packet linked, the manifest filled, and seed_bindings + author_input_fingerprint written."""
    _patch_author(monkeypatch, packet=_author_packet(2))
    async with db_factory() as s:
        book, ch, scenes = await _seed(s, n_scenes=2)
        s.add(_adoption(book, ch))
        await s.commit()
        chapter_id = ch.id
        scene_by_no = {sc.scene_no: sc.id for sc in scenes}

    did = await run_one_adoption(db_factory, extractor=FakeImportEvidenceExtractor(), retrieve=_fixed_retriever([]))
    assert did is True

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == "contract_proposed"
        assert adoption.chapter_packet_id is not None
        assert adoption.finished_at is not None
        assert adoption.author_input_fingerprint  # tier-B key written at publish
        shards = (adoption.evidence_manifest or {}).get("shards", [])
        assert len(shards) == 2  # manifest filled, one per imported scene

        # seed_bindings maps each authored seed back to the imported Scene of that scene_no (Q8).
        bindings = adoption.seed_bindings or {}
        assert len(bindings) == 2
        assert {b["scene_no"] for b in bindings.values()} == {1, 2}
        for b in bindings.values():
            assert b["scene_id"] == str(scene_by_no[b["scene_no"]])

        packet = (await s.execute(select(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id))).scalar_one()
        assert packet.id == adoption.chapter_packet_id
        assert packet.status == "proposed"
        assert await _count(s, ImportSceneEvidence) == 2


async def test_idempotent_reuse_skips_second_author_and_extraction(db_factory, monkeypatch):
    """A second adoption over the same unchanged chapter REUSES the first pass's proposed packet: no second
    author call (tier B) and no re-extraction (evidence identity reuse). It still finalizes to
    `contract_proposed`, linked to the SAME packet."""
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_author_packet(2), counter=counter)
    extractor = FakeImportEvidenceExtractor()

    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        s.add(_adoption(book, ch))
        await s.commit()
        book_id, ch_id = book.id, ch.id

    assert await run_one_adoption(db_factory, extractor=extractor, retrieve=_fixed_retriever([])) is True
    assert counter["n"] == 1  # authored once
    assert len(extractor.calls) == 2  # extracted both scenes once

    async with db_factory() as s:
        first = (await s.execute(select(ImportAdoption))).scalar_one()
        first_packet_id = first.chapter_packet_id
        # A fresh, identical adoption for the same chapter.
        s.add(
            ImportAdoption(
                book_id=book_id,
                chapter_id=ch_id,
                mode="initial",
                status="queued",
                source_fingerprint="pending",
                liveness_basis="operator_independent",
            )
        )
        await s.commit()

    assert await run_one_adoption(db_factory, extractor=extractor, retrieve=_fixed_retriever([])) is True
    assert counter["n"] == 1  # REUSE — no second author call
    assert len(extractor.calls) == 2  # REUSE — no re-extraction

    async with db_factory() as s:
        adoptions = (await s.execute(select(ImportAdoption).order_by(ImportAdoption.created_at))).scalars().all()
        assert len(adoptions) == 2
        assert all(a.status == "contract_proposed" for a in adoptions)
        assert adoptions[1].chapter_packet_id == first_packet_id  # linked to the SAME packet
        assert await _count(s, ChapterPacket) == 1  # no duplicate packet


async def test_fingerprint_drift_invalidates_and_retains_evidence(db_factory, monkeypatch):
    """Q13: if the chapter's source fingerprint drifts between claim and publish, the publish CAS fails —
    the author pass is INVALIDATED and its proposed packet is deleted, but the immutable evidence shards
    survive."""
    async with db_factory() as s:
        book, ch, scenes = await _seed(s, n_scenes=1)
        # A real evidence shard exists (must survive the invalidation).
        evidence = await ensure_scene_evidence(s, scene=scenes[0], extractor=FakeImportEvidenceExtractor())
        packet = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status="proposed",
            body={"scene_seeds": [{"seed_id": str(uuid.uuid4()), "scene_no": 1}]},
            open_questions={"items": []},
        )
        s.add(packet)
        await s.flush()
        # Claimed with a STALE fingerprint that cannot match the live chapter -> forced drift.
        adoption = _adoption(
            book,
            ch,
            status="running",
            source_fingerprint="stale-does-not-match",
            claimed_by="w",
            claimed_at=datetime.now(UTC),
        )
        s.add(adoption)
        await s.flush()
        await s.commit()
        adoption_id, chapter_id, packet_id, evidence_id = adoption.id, ch.id, packet.id, evidence.id

    outcome = await publish_adoption(
        db_factory,
        adoption_id=adoption_id,
        chapter_id=chapter_id,
        packet_id=packet_id,
        packet_status="proposed",
        packet_body={"scene_seeds": []},
        manifest_entries=[],
        author_input_fingerprint="x",
        created_packet=True,
    )
    assert outcome == "invalidated"

    async with db_factory() as s:
        adoption = await s.get(ImportAdoption, adoption_id)
        assert adoption.status == "invalidated"
        assert "drift" in (adoption.error or "")
        assert adoption.finished_at is not None
        assert await s.get(ChapterPacket, packet_id) is None  # the pass's packet is discarded
        assert await s.get(ImportSceneEvidence, evidence_id) is not None  # the shard survives


async def test_chapter_workflow_busy_writes_nothing_and_reenters(db_factory):
    """Q16: when the per-chapter workflow lock is held, the publish CAS raises ChapterWorkflowBusy, writes
    nothing, and re-enters cleanly once the lock frees."""
    seed_id = str(uuid.uuid4())
    async with db_factory() as s:
        book, ch, scenes = await _seed(s, n_scenes=1)
        packet = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status="proposed",
            body={"scene_seeds": [{"seed_id": seed_id, "scene_no": 1}]},
            open_questions={"items": []},
        )
        s.add(packet)
        await s.flush()
        rows = await import_adoption._chapter_scene_rows(s, ch.id)
        live_fp = chapter_source_fingerprint(rows)  # no drift — the fingerprint matches the live chapter
        adoption = _adoption(
            book, ch, status="running", source_fingerprint=live_fp, claimed_by="w", claimed_at=datetime.now(UTC)
        )
        s.add(adoption)
        await s.flush()
        await s.commit()
        adoption_id, chapter_id, packet_id, scene_id = adoption.id, ch.id, packet.id, scenes[0].id

    body = {"scene_seeds": [{"seed_id": seed_id, "scene_no": 1}]}

    async def _publish(timeout_ms: int) -> str:
        return await publish_adoption(
            db_factory,
            adoption_id=adoption_id,
            chapter_id=chapter_id,
            packet_id=packet_id,
            packet_status="proposed",
            packet_body=body,
            manifest_entries=[],
            author_input_fingerprint="ai",
            created_packet=False,
            timeout_ms=timeout_ms,
        )

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)  # hold the lock
        with pytest.raises(ChapterWorkflowBusy):
            await _publish(250)
        async with db_factory() as probe:  # nothing was written on the busy path
            unchanged = await probe.get(ImportAdoption, adoption_id)
            assert unchanged.status == "running"
            assert unchanged.chapter_packet_id is None
        await holder.rollback()  # release the advisory lock

    outcome = await _publish(4000)  # re-enter cleanly
    assert outcome == "contract_proposed"
    async with db_factory() as s:
        adoption = await s.get(ImportAdoption, adoption_id)
        assert adoption.status == "contract_proposed"
        assert adoption.chapter_packet_id == packet_id
        assert adoption.author_input_fingerprint == "ai"
        assert list((adoption.seed_bindings or {}).values())[0]["scene_id"] == str(scene_id)


async def test_amendment_without_an_approved_packet_is_refused_closed(db_factory, monkeypatch):
    """Amendment mode is copy-on-write FROM an approved contract, so a chapter with NO approved packet is
    the INITIAL case and cannot be amended: the worker fails the adoption closed with amendment mode's own
    typed reason and authors nothing (no model call, no ChapterPacket).

    This used to assert the Slice-3b blanket refusal ("amendment mode is not implemented"). #261 W2a built
    the copy-on-write author pass, so the blanket refusal — and the `AmendmentModeUnsupported` name that
    carried it — are gone; what survives is the genuine boundary condition, refused with
    `amendment.AmendmentNotEligible` carrying the `no_approved_packet` verdict token."""
    _patch_author(monkeypatch, packet=_author_packet(1))
    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=1)
        s.add(_adoption(book, ch, mode="amendment"))
        await s.commit()
        chapter_id = ch.id

    assert (
        await run_one_adoption(db_factory, extractor=FakeImportEvidenceExtractor(), retrieve=_fixed_retriever([]))
        is True
    )

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == "failed"
        assert "AmendmentNotEligible" in (adoption.error or "")
        assert "no approved contract" in (adoption.error or "")
        assert (
            await s.execute(
                select(func.count()).select_from(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id)
            )
        ).scalar_one() == 0
