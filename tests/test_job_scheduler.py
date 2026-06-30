"""Direct DB tests for job scheduling after human approval (contract-first)."""

from __future__ import annotations

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.shared.enums import BeatStatus, GateMode, JobKind, JobStatus, RunStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Run, Scene
from dominion.workers.draft_queue import has_active_draft_job_for_scene_packet
from dominion.workers.job_scheduler import (
    schedule_beats_on_gate1_approval,
    schedule_next_after_approval,
    schedule_revision,
    schedule_undrafted_beats,
)


async def _book_chapter(s):
    book = Book(title="Scheduler Test")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return book, ch


async def test_schedule_next_after_approval_pause_each(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
            status=RunStatus.ACTIVE,
        )
        s.add(run)
        b2 = Beat(chapter_id=ch.id, scene_no=2, beat_text="two", status=BeatStatus.APPROVED)
        s.add(Beat(chapter_id=ch.id, scene_no=1, beat_text="one", status=BeatStatus.APPROVED))
        s.add(b2)
        sc1 = Scene(chapter_id=ch.id, scene_no=1, prose="done", version=1, status=SceneStatus.APPROVED)
        s.add(sc1)
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b2)

        job_id = await schedule_next_after_approval(s, sc1)
        assert job_id is not None
        job = await s.get(Job, job_id)
        assert job is not None and job.scene_no == 2 and job.kind == JobKind.DRAFT
        assert job.scene_packet_id is not None


async def test_schedule_next_after_approval_not_pause_each(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1},
            gate_mode=GateMode.DRAFT_AHEAD,
            token_budget=40_000,
            status=RunStatus.ACTIVE,
        )
        s.add(run)
        s.add(Beat(chapter_id=ch.id, scene_no=2, beat_text="two", status=BeatStatus.APPROVED))
        sc1 = Scene(chapter_id=ch.id, scene_no=1, prose="done", version=1, status=SceneStatus.APPROVED)
        s.add(sc1)
        await s.flush()

        assert await schedule_next_after_approval(s, sc1) is None


async def test_schedule_next_after_approval_idempotent(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
            status=RunStatus.ACTIVE,
        )
        s.add(run)
        b2 = Beat(chapter_id=ch.id, scene_no=2, beat_text="two", status=BeatStatus.APPROVED)
        s.add(b2)
        sc1 = Scene(chapter_id=ch.id, scene_no=1, prose="done", version=1, status=SceneStatus.APPROVED)
        s.add(sc1)
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b2)

        first = await schedule_next_after_approval(s, sc1)
        second = await schedule_next_after_approval(s, sc1)
        assert first == second
        jobs = (await s.execute(select(Job).where(Job.scene_no == 2, Job.status == JobStatus.QUEUED))).scalars().all()
        assert len(jobs) == 1


async def test_schedule_revision_creates_revise_job(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
            status=RunStatus.ACTIVE,
        )
        s.add(run)
        sc = Scene(chapter_id=ch.id, scene_no=1, prose="draft", version=1, status=SceneStatus.PENDING_REVIEW)
        s.add(sc)
        await s.flush()

        job_id = await schedule_revision(s, sc, target_pass=None)
        assert job_id is not None
        job = await s.get(Job, job_id)
        assert job is not None and job.kind == JobKind.REVISE_FULL and job.target_scene_id == sc.id


async def test_schedule_beats_on_gate1_idempotent(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
            status=RunStatus.ACTIVE,
        )
        s.add(run)
        b1 = Beat(chapter_id=ch.id, scene_no=1, beat_text="one", status=BeatStatus.APPROVED)
        b2 = Beat(chapter_id=ch.id, scene_no=2, beat_text="two", status=BeatStatus.APPROVED)
        s.add_all([b1, b2])
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        await seed_scene_packet(s, chapter=ch, beat=b2)

        r1 = await schedule_beats_on_gate1_approval(s, ch, [b1, b2], run)
        r2 = await schedule_beats_on_gate1_approval(s, ch, [b1, b2], run)
        assert r1.queued_job_ids == r2.queued_job_ids
        jobs = (await s.execute(select(Job).where(Job.status == JobStatus.QUEUED))).scalars().all()
        assert sorted(j.scene_no for j in jobs) == [1, 2]
        assert all(j.scene_packet_id is not None for j in jobs)


async def test_schedule_undrafted_beats_skips_drafted_and_proposed(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(b1)
        s.add(Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2"))
        s.add(Beat(chapter_id=ch.id, scene_no=3, status=BeatStatus.PROPOSED, beat_text="b3"))
        s.add(Scene(chapter_id=ch.id, scene_no=2, prose="done", version=1, status=SceneStatus.PENDING_REVIEW))
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)

        result = await schedule_undrafted_beats(s, ch, None)
        assert len(result.queued_job_ids) == 1
        job = await s.get(Job, result.queued_job_ids[0])
        assert job is not None and job.scene_no == 1
        assert job.scene_packet_id is not None

        again = await schedule_undrafted_beats(s, ch, None)
        assert len(again.queued_job_ids) == 1
        assert again.queued_job_ids[0] == result.queued_job_ids[0]


async def test_has_active_draft_job_for_scene_packet(db_factory):
    async with db_factory() as s:
        book, ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(b1)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=b1)
        job = Job(
            kind=JobKind.DRAFT,
            chapter_id=ch.id,
            beat_id=b1.id,
            scene_packet_id=sp.id,
            chapter_no=1,
            scene_no=1,
            status=JobStatus.QUEUED,
            token_budget=40_000,
        )
        s.add(job)
        await s.flush()
        assert await has_active_draft_job_for_scene_packet(s, scene_packet_id=sp.id)
