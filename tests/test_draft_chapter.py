"""draft_chapter: the 'draft' step after a packet is approved — queue a DRAFT job for every approved
beat of the chapter that has no scene yet, skipping ones already drafted or queued. Idempotent."""
from __future__ import annotations

from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.shared.enums import BeatStatus, JobStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Scene


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
        s.add(Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1"))
        s.add(Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2"))
        s.add(Beat(chapter_id=ch.id, scene_no=3, status=BeatStatus.PROPOSED, beat_text="b3"))  # not approved
        # scene 2 is already drafted -> skipped
        s.add(Scene(chapter_id=ch.id, scene_no=2, prose="done", version=1, status=SceneStatus.PENDING_REVIEW))
        await s.flush()

        out = await chapters.draft_chapter(ch.id, s)
        assert out["queued"] == 1  # only scene 1 (2 drafted, 3 not approved)
        jobs = (await s.execute(
            select(Job).where(Job.chapter_no == 1, Job.status == JobStatus.QUEUED)
        )).scalars().all()
        assert [j.scene_no for j in jobs] == [1]

        # idempotent: a second call queues nothing more (scene 1 now has a QUEUED job)
        again = await chapters.draft_chapter(ch.id, s)
        assert again["queued"] == 1  # reports the existing queued job, doesn't duplicate
        all_jobs = (await s.execute(select(Job).where(Job.scene_no == 1))).scalars().all()
        assert len(all_jobs) == 1
