"""Copy-on-write amendment author — seed merge and refusal-without-model-call (#261)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from dominion.shared.enums import PacketStatus, PacketVerdict, SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, Scene
from dominion.workers.packet import amendment, amendment_author
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import evidence as evidence_mod
from dominion.workers.packet import qa as qa_mod


async def _seed_chapter(s, *, n_scenes: int = 2) -> tuple[Book, Chapter, list[Scene]]:
    book = Book(title="Amendment Author")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    scenes: list[Scene] = []
    for i in range(1, n_scenes + 1):
        sc = Scene(
            chapter_id=ch.id,
            scene_no=i,
            version=1,
            prose=f"Imported prose for scene {i}. The vault gate stands open.",
            status=SceneStatus.PENDING_REVIEW,
        )
        s.add(sc)
        scenes.append(sc)
    await s.flush()
    return book, ch, scenes


def _seed(seed_id: uuid.UUID, scene_no: int) -> dict:
    return {"seed_id": str(seed_id), "scene_no": scene_no, "scene_job": f"Scene {scene_no} does its work."}


async def _approved_packet(s, book: Book, ch: Chapter, seeds: list[dict]) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=PacketStatus.APPROVED,
        confidence="green",
        body={"scene_seeds": seeds, "claims": [{"claim": "the gate exists", "source_strength": "LOCKED_CANON"}]},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    return cp


def _evidence(scene: Scene) -> evidence_mod.SceneEvidence:
    return evidence_mod.SceneEvidence(
        scene_id=scene.id,
        scene_no=scene.scene_no,
        scene_version=scene.version,
        prose_hash="abc",
        ledger={"facts": [{"text": f"scene {scene.scene_no} at the vault", "span": [0, 8]}]},
        snapshot_prose_len=len(scene.prose or ""),
        pov="Marcus",
    )


def _authored(n_scenes: int = 2) -> dict:
    return {
        "confidence": "green",
        "chapter_job": "Reconstruct missing seeds",
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


async def _empty_retrieve(_q):
    return []


def _patch_author(monkeypatch, *, packet: dict, counter: dict | None = None) -> None:
    async def fake_author(**kwargs):
        if counter is not None:
            counter["n"] += 1
        return packet

    async def fake_qa(_packet, **kwargs):
        return {"verdict": PacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(author_mod, "author_packet_from_evidence", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


async def _count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_copy_on_write_keeps_existing_seed_ids_and_adds_unseeded(db_factory, monkeypatch):
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_authored(2), counter=counter)
    kept = uuid.uuid4()
    async with db_factory() as s:
        book, ch, scenes = await _seed_chapter(s, n_scenes=2)
        approved = await _approved_packet(s, book, ch, [_seed(kept, 1)])
        await s.commit()
        chapter_id, approved_id, adoption_id = ch.id, approved.id, uuid.uuid4()
        evidence = [_evidence(scenes[0]), _evidence(scenes[1])]

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        approved = await s.get(ChapterPacket, approved_id)
        proposed = await amendment_author.author_amendment_from_evidence(
            s,
            chapter=chapter,
            evidence=evidence,
            approved_packet=approved,
            adoption_id=adoption_id,
            source_fingerprint="fp-source",
            evidence_manifest_fingerprint="fp-evidence",
            retrieve=_empty_retrieve,
        )
        await s.commit()
        proposed_id = proposed.id

    assert counter["n"] == 1
    async with db_factory() as s:
        proposed = await s.get(ChapterPacket, proposed_id)
        predecessor = await s.get(ChapterPacket, approved_id)
        assert predecessor.status == PacketStatus.APPROVED.value
        assert proposed.origin_mode == "amendment"
        assert proposed.status == PacketStatus.PROPOSED.value
        assert proposed.supersedes_packet_id == approved_id
        assert proposed.id != approved_id
        seeds = {int(seed["scene_no"]): seed for seed in proposed.body["scene_seeds"]}
        assert seeds[1]["seed_id"] == str(kept)
        assert seeds[2]["seed_id"]
        assert seeds[2]["seed_id"] != str(kept)
        assert (await _count(s, ChapterPacket)) == 2


async def test_refuses_without_approved_packet_and_does_not_call_author(db_factory, monkeypatch):
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_authored(1), counter=counter)
    async with db_factory() as s:
        book, ch, scenes = await _seed_chapter(s, n_scenes=1)
        ghost = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.PROPOSED,
            body={"scene_seeds": []},
            open_questions={"items": []},
        )
        s.add(ghost)
        await s.commit()
        chapter_id, ghost_id = ch.id, ghost.id
        evidence = [_evidence(scenes[0])]

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        ghost = await s.get(ChapterPacket, ghost_id)
        with pytest.raises(amendment.AmendmentNotEligible) as exc:
            await amendment_author.author_amendment_from_evidence(
                s,
                chapter=chapter,
                evidence=evidence,
                approved_packet=ghost,
                adoption_id=uuid.uuid4(),
                source_fingerprint="fp",
                evidence_manifest_fingerprint="fp",
                retrieve=_empty_retrieve,
            )
        assert exc.value.reason == amendment.REASON_NO_APPROVED_PACKET
    assert counter["n"] == 0


async def test_refuses_already_open_amendment_without_author_call(db_factory, monkeypatch):
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_authored(2), counter=counter)
    kept = uuid.uuid4()
    async with db_factory() as s:
        book, ch, scenes = await _seed_chapter(s, n_scenes=2)
        approved = await _approved_packet(s, book, ch, [_seed(kept, 1)])
        open_row = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.PROPOSED,
            origin_mode="amendment",
            supersedes_packet_id=approved.id,
            body={"scene_seeds": [_seed(kept, 1)]},
            open_questions={"items": []},
        )
        s.add(open_row)
        await s.commit()
        chapter_id, approved_id = ch.id, approved.id
        evidence = [_evidence(scenes[0]), _evidence(scenes[1])]

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        approved = await s.get(ChapterPacket, approved_id)
        with pytest.raises(amendment.AmendmentNotEligible) as exc:
            await amendment_author.author_amendment_from_evidence(
                s,
                chapter=chapter,
                evidence=evidence,
                approved_packet=approved,
                adoption_id=uuid.uuid4(),
                source_fingerprint="fp",
                evidence_manifest_fingerprint="fp",
                retrieve=_empty_retrieve,
            )
        assert exc.value.reason == amendment.REASON_AMENDMENT_ALREADY_OPEN
    assert counter["n"] == 0


async def test_refuses_predecessor_mismatch_without_author_call(db_factory, monkeypatch):
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_authored(2), counter=counter)
    kept = uuid.uuid4()
    async with db_factory() as s:
        book, ch, scenes = await _seed_chapter(s, n_scenes=2)
        await _approved_packet(s, book, ch, [_seed(kept, 1)])
        stale = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.PROPOSED,
            origin_mode="initial",
            body={"scene_seeds": [_seed(kept, 1)]},
            open_questions={"items": []},
        )
        s.add(stale)
        await s.commit()
        chapter_id, stale_id = ch.id, stale.id
        evidence = [_evidence(scenes[0]), _evidence(scenes[1])]

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        stale = await s.get(ChapterPacket, stale_id)
        with pytest.raises(amendment.AmendmentPredecessorMissing):
            await amendment_author.author_amendment_from_evidence(
                s,
                chapter=chapter,
                evidence=evidence,
                approved_packet=stale,
                adoption_id=uuid.uuid4(),
                source_fingerprint="fp",
                evidence_manifest_fingerprint="fp",
                retrieve=_empty_retrieve,
            )
    assert counter["n"] == 0


async def test_no_evidence_for_unseeded_scenes_blocks_without_superseding(db_factory, monkeypatch):
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_authored(2), counter=counter)
    kept = uuid.uuid4()
    async with db_factory() as s:
        book, ch, scenes = await _seed_chapter(s, n_scenes=2)
        approved = await _approved_packet(s, book, ch, [_seed(kept, 1)])
        await s.commit()
        chapter_id, approved_id = ch.id, approved.id
        evidence = [_evidence(scenes[0])]

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        approved = await s.get(ChapterPacket, approved_id)
        blocked = await amendment_author.author_amendment_from_evidence(
            s,
            chapter=chapter,
            evidence=evidence,
            approved_packet=approved,
            adoption_id=uuid.uuid4(),
            source_fingerprint="fp",
            evidence_manifest_fingerprint="fp",
            retrieve=_empty_retrieve,
        )
        await s.commit()
        blocked_id = blocked.id

    async with db_factory() as s:
        blocked = await s.get(ChapterPacket, blocked_id)
        predecessor = await s.get(ChapterPacket, approved_id)
        assert predecessor.status == PacketStatus.APPROVED.value
        assert blocked.status == PacketStatus.BLOCKED.value
        assert blocked.supersedes_packet_id == approved_id
    assert counter["n"] == 0
