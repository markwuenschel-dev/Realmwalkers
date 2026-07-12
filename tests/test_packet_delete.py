"""Chapter/scene packet delete endpoints and FK cleanup."""

from __future__ import annotations

import uuid

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.api.routers import packets as packets_router
from dominion.api.routers import scene_packets as sp_router
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, RunStatus, ScenePacketStatus
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    DraftAttempt,
    Job,
    Run,
    Scene,
    ScenePacket,
)
from dominion.workers.scene_packet import derive as sp_derive


def _seed(seed_id: str, scene_no: int = 1) -> dict:
    return {"seed_id": seed_id, "scene_no": scene_no, "scene_job": "do the thing"}


async def _approved_chapter_packet(s, book, ch, seeds: list[dict]) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": seeds},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    return cp


async def test_delete_chapter_packet_cascades_scene_packets(db_factory, monkeypatch):
    async def fake_derive(session, *, packet):
        session.add(
            ScenePacket(
                book_id=packet.book_id,
                chapter_id=packet.chapter_id,
                chapter_packet_id=packet.id,
                scene_seed_id=uuid.UUID(str(packet.body["scene_seeds"][0]["seed_id"])),
                scene_no=1,
                status=ScenePacketStatus.PROPOSED,
                body={"scene_no": 1},
                source_hash="x",
            )
        )
        await session.flush()
        return {"created": 1, "updated": 0, "blocked": 0, "stale": 0}

    monkeypatch.setattr(sp_derive, "derive_scene_packets", fake_derive)
    async with db_factory() as s:
        book = Book(title="Pkt")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        await _approved_chapter_packet(s, book, ch, [_seed(str(uuid.uuid4()))])
        cp = (await s.execute(select(ChapterPacket))).scalar_one()
        await sp_derive.derive_scene_packets(s, packet=cp)
        await s.commit()

        out = await packets_router.delete_packet(ch.id, s)
        assert out.deleted_chapter_packets == 1
        assert out.deleted_scene_packets == 1
        assert (await s.execute(select(ChapterPacket))).scalar_one_or_none() is None
        assert (await s.execute(select(ScenePacket))).scalar_one_or_none() is None


async def test_delete_scene_packet_detaches_refs_and_purges_jobs(db_factory):
    async with db_factory() as s:
        book = Book(title="SP")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        scene = Scene(chapter_id=ch.id, scene_no=1, prose="p", version=1)
        s.add(scene)
        await s.flush()
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        scene.scene_packet_id = sp.id
        run = Run(book_id=book.id, scope_json={}, gate_mode="pause_each", token_budget=40_000, status=RunStatus.ACTIVE)
        s.add(run)
        await s.flush()
        job = Job(
            run_id=run.id,
            book_id=book.id,
            kind=JobKind.DRAFT,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
            chapter_no=1,
            scene_no=1,
            status=JobStatus.QUEUED,
            token_budget=40_000,
        )
        s.add(job)
        s.add(DraftAttempt(scene_id=scene.id, scene_packet_id=sp.id, stage="raw", prose="x"))
        await s.commit()

        out = await sp_router.delete_scene_packet(sp.id, s)
        assert str(out.deleted) == str(sp.id)
        assert out.jobs_purged == 1
        assert (await s.get(ScenePacket, sp.id)) is None
        assert (await s.get(Beat, beat.id)).scene_packet_id is None
        assert (await s.get(Scene, scene.id)).scene_packet_id is None
        assert (await s.execute(select(Job))).scalar_one_or_none() is None
        da = (await s.execute(select(DraftAttempt))).scalar_one()
        assert da.scene_packet_id is None
