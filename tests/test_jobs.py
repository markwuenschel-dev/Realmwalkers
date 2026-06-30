"""Requeue and jobs API contract-first tests."""
from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import select

from conftest import seed_scene_packet
from dominion.api.routers import jobs as jobs_router
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, RunStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Run
from dominion.workers.draft_queue import reconcile_and_requeue_failed_draft_jobs


async def _setup(s):
    book = Book(title="Requeue")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="X")
    s.add(ch)
    run = Run(
        book_id=book.id, scope_json={}, gate_mode="pause_each",
        token_budget=40_000, status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    sp = await seed_scene_packet(s, chapter=ch, beat=beat)
    return book, ch, beat, run, sp


async def test_requeue_creates_fresh_job_for_failed_draft(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        old = Job(
            run_id=run.id, kind=JobKind.DRAFT, chapter_id=ch.id, beat_id=beat.id,
            scene_packet_id=sp.id, chapter_no=1, scene_no=1,
            status=JobStatus.FAILED, token_budget=40_000, last_error="transient",
        )
        s.add(old)
        await s.flush()
        result = await reconcile_and_requeue_failed_draft_jobs(s, book_id=book.id)
        assert result.queued == 1
        new_jobs = (await s.execute(
            select(Job).where(Job.status == JobStatus.QUEUED)
        )).scalars().all()
        assert len(new_jobs) == 1
        assert new_jobs[0].scene_packet_id == sp.id
        assert new_jobs[0].id != old.id


async def test_requeue_skips_when_scene_packet_not_approved(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        sp.status = "proposed"
        old = Job(
            run_id=run.id, kind=JobKind.DRAFT, chapter_id=ch.id, beat_id=beat.id,
            scene_packet_id=sp.id, chapter_no=1, scene_no=1,
            status=JobStatus.FAILED, token_budget=40_000,
        )
        s.add(old)
        await s.flush()
        result = await reconcile_and_requeue_failed_draft_jobs(s, book_id=book.id)
        assert result.queued == 0
        assert len(result.skipped) >= 1


async def test_retry_failed_api_returns_structured_result(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        s.add(Job(
            run_id=run.id, kind=JobKind.DRAFT, chapter_id=ch.id, beat_id=beat.id,
            scene_packet_id=sp.id, chapter_no=1, scene_no=1,
            status=JobStatus.FAILED, token_budget=40_000, last_error="err",
        ))
        await s.flush()
        out = await jobs_router.retry_failed(
            background=MagicMock(), session=s, book_id=book.id,
        )
        assert out.requested == 1
        assert out.requeued == 1
