"""Contract-first draft queue scheduler unit tests."""
from __future__ import annotations

from sqlalchemy import select

from conftest import seed_scene_packet
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, ScenePacketStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Scene, ScenePacket
from dominion.workers.draft_queue import (
    has_active_draft_job_for_scene_packet,
    resolve_approved_scene_packet_for_beat,
    schedule_contract_first_draft_jobs,
)


async def _chapter_with_beat(s):
    book = Book(title="DQ")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    return ch, beat


async def test_resolve_prefers_valid_beat_scene_packet_link(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat)
        assert isinstance(resolved, ScenePacket)
        assert resolved.id == sp.id


async def test_resolve_repairs_null_beat_link_by_chapter_scene(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=None)
        beat.scene_packet_id = None
        await s.flush()
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=True)
        assert isinstance(resolved, ScenePacket)
        assert beat.scene_packet_id == sp.id


async def test_resolve_rejects_unapproved_scene_packet(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.PROPOSED
        await s.flush()
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason == "no_approved_scene_packet"


async def test_resolve_rejects_stale_scene_packet(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.STALE
        sp.stale_reason = "edited"
        await s.flush()
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason in ("scene_packet_stale", "no_approved_scene_packet")


async def test_resolve_rejects_duplicate_approved_scene_packets(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        await seed_scene_packet(s, chapter=ch, beat=None)
        sp2 = await seed_scene_packet(s, chapter=ch, beat=None)
        sp2.scene_no = 1
        await s.flush()
        beat.scene_packet_id = None
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason == "duplicate_approved_scene_packets"


async def test_schedule_stamps_scene_packet_id_on_every_job(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        result = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert len(result.queued_job_ids) == 1
        job = await s.get(Job, result.queued_job_ids[0])
        assert job.scene_packet_id == sp.id
        assert job.beat_id == beat.id


async def test_schedule_skips_missing_scene_packet_with_blocker(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        result = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert result.queued_job_ids == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == "no_approved_scene_packet"


async def test_schedule_dedupes_existing_active_job(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        existing = Job(
            kind=JobKind.DRAFT, chapter_id=ch.id, beat_id=beat.id, scene_packet_id=sp.id,
            chapter_no=1, scene_no=1, status=JobStatus.QUEUED, token_budget=40_000,
        )
        s.add(existing)
        await s.flush()
        assert await has_active_draft_job_for_scene_packet(s, scene_packet_id=sp.id)
        result = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert len(result.queued_job_ids) == 1
        again = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert again.queued_job_ids == result.queued_job_ids


async def test_schedule_does_not_set_chapter_drafting_when_zero_jobs(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        result = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert not result.queued_job_ids
