"""Repair draft queue audit tests."""

from __future__ import annotations

from conftest import seed_scene_packet

from dominion.shared.enums import BeatStatus
from dominion.shared.models import Beat, Book, Chapter
from dominion.tools.draft_audit import audit_chapter


async def _chapter(s):
    book = Book(title="Repair")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    return ch


async def test_audit_reports_unlinked_beats(db_factory):
    async with db_factory() as s:
        ch = await _chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
        s.add(beat)
        await s.flush()
        report = await audit_chapter(s, ch.id)
        assert len(report.unlinked_beats) == 1


async def test_audit_finds_repairable_beat(db_factory):
    async with db_factory() as s:
        ch = await _chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=None)
        beat.scene_packet_id = None
        await s.flush()
        report = await audit_chapter(s, ch.id)
        assert len(report.repairable_beats) == 1
        assert report.repairable_beats[0]["scene_packet_id"] == str(sp.id)
