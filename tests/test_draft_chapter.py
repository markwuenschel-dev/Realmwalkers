"""draft_chapter: contract-first draft queueing after ScenePacket approval."""

from __future__ import annotations

import pytest
from conftest import seed_scene_packet
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.shared.enums import BeatStatus, JobStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Scene
from dominion.workers.draft_readiness import compute_draft_readiness


async def _book_chapter(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return ch


async def test_draft_chapter_queues_only_undrafted_approved_beats(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        b2 = Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2")
        b3 = Beat(chapter_id=ch.id, scene_no=3, status=BeatStatus.PROPOSED, beat_text="b3")
        s.add_all([b1, b2, b3])
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        await seed_scene_packet(s, chapter=ch, beat=b2)
        s.add(Scene(chapter_id=ch.id, scene_no=2, prose="done", version=1, status=SceneStatus.PENDING_REVIEW))
        await s.flush()

        out = await chapters.draft_chapter(ch.id, s)
        assert out.queued == 1
        jobs = (await s.execute(select(Job).where(Job.chapter_no == 1, Job.status == JobStatus.QUEUED))).scalars().all()
        assert [j.scene_no for j in jobs] == [1]
        assert all(j.scene_packet_id is not None for j in jobs)


async def test_draft_chapter_409_when_no_approved_scene_packets(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        s.add(Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1"))
        await s.flush()
        with pytest.raises(HTTPException) as exc:
            await chapters.draft_chapter(ch.id, s)
        assert exc.value.status_code == 409
        jobs = (await s.execute(select(Job))).scalars().all()
        assert jobs == []


async def test_readiness_not_draftable_when_all_scenes_already_drafted(db_factory):
    """A fully-drafted chapter reports draftable=False so the Desk's "Draft chapter" button is disabled
    instead of enabled-then-409. Mirrors schedule_undrafted_beats(skip_drafted=True): a beat whose scene
    already has prose is not queueable (redraft is the path for those) — and it is NOT a blocker, just
    nothing left to draft."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(b1)
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="already drafted", version=1, status=SceneStatus.APPROVED))
        await s.flush()

        readiness = await compute_draft_readiness(s, ch.id)
        assert readiness.draftable is False
        assert readiness.blockers == []  # already-drafted is not a blocker — there's simply nothing to queue


async def test_readiness_draftable_when_an_approved_beat_is_still_undrafted(db_factory):
    """Partial-draft chapters stay draftable: scene 1 is drafted but approved beat 2 has no scene yet,
    so draftable=True and clicking queues only the remaining beat."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        b2 = Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2")
        s.add_all([b1, b2])
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        await seed_scene_packet(s, chapter=ch, beat=b2)
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="done", version=1, status=SceneStatus.APPROVED))
        await s.flush()

        readiness = await compute_draft_readiness(s, ch.id)
        assert readiness.draftable is True
