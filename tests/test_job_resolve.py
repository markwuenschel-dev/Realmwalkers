"""DB tests for Job → ResolvedJob resolution (direct IDs + legacy fallbacks)."""

from __future__ import annotations

import pytest

from dominion.shared.enums import BeatStatus, JobKind
from dominion.shared.models import Beat, Book, Chapter, Job
from dominion.workers.context.resolve import resolve_job
from tests.conftest import seed_scene_packet


async def _book_chapter(s):
    book = Book(title="Resolve Test")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline="o")
    s.add(ch)
    await s.flush()
    return book, ch


async def test_resolve_job_direct_ids(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        beat = Beat(
            chapter_id=ch.id,
            scene_no=1,
            status=BeatStatus.APPROVED,
            beat_text="Direct beat.",
            characters_present=["Marcus"],
        )
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        job = Job(
            kind=JobKind.DRAFT,
            book_id=book.id,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
            token_budget=40_000,
        )
        s.add(job)
        await s.flush()

        resolved = await resolve_job(s, job)
        assert resolved.book_id == book.id
        assert resolved.chapter.id == ch.id
        assert resolved.beat.id == beat.id
        assert resolved.scene_no == 1
        assert resolved.scene_packet_id == sp.id


async def test_resolve_job_missing_beat_raises(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        job = Job(
            kind=JobKind.DRAFT,
            book_id=book.id,
            chapter_id=ch.id,
            scene_no=99,
            token_budget=40_000,
        )
        s.add(job)
        await s.flush()

        with pytest.raises(ValueError, match="no beat for ch1 sc99"):
            await resolve_job(s, job)
