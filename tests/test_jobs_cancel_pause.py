"""Queue control (Desk Control Round P1): per-job cancel + the human pause switch.

DB-backed (skips if Postgres unreachable). Router functions called directly, mirroring
tests/test_jobs_recent.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from dominion.api.routers import jobs as jobs_router
from dominion.shared.enums import JobStatus
from dominion.shared.models import Book, DraftAttempt, Job, ModelOverride
from dominion.workers import background_work


def _job(**kw: object) -> Job:
    defaults = dict(kind="draft", token_budget=1000, status=JobStatus.QUEUED)
    defaults.update(kw)
    return Job(**defaults)  # type: ignore[arg-type]


async def _book(s) -> Book:
    """Every job must belong to a book (ADR 0027); seed one to attach jobs to."""
    book = Book(title="Queue Control")
    s.add(book)
    await s.flush()
    return book


@pytest.fixture(autouse=True)
def _unpause():
    """Each test starts unpaused; the module global is process-wide state."""
    background_work._queue_paused = False
    yield
    background_work._queue_paused = False


async def test_cancel_deletes_queued_and_unlinks_attempts(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        job = _job(book_id=book.id, chapter_no=1, scene_no=2)
        s.add(job)
        await s.flush()
        s.add(DraftAttempt(job_id=job.id, stage="draft", model="test", prose="draft text"))
        await s.commit()

        out = await jobs_router.cancel(job.id, s)
        assert out.scene_no == 2
        assert await s.get(Job, job.id) is None
        attempt = (await s.execute(select(DraftAttempt))).scalars().first()
        assert attempt is not None and attempt.job_id is None


async def test_cancel_refuses_running_and_missing(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        running = _job(book_id=book.id, chapter_no=1, scene_no=3, status=JobStatus.RUNNING)
        s.add(running)
        await s.commit()

        with pytest.raises(HTTPException) as e409:
            await jobs_router.cancel(running.id, s)
        assert e409.value.status_code == 409

        with pytest.raises(HTTPException) as e404:
            await jobs_router.cancel(uuid.uuid4(), s)
        assert e404.value.status_code == 404
        # The running job is untouched.
        assert (await s.get(Job, running.id)).status == JobStatus.RUNNING


async def test_pause_persists_and_blocks_worker_claim(db_factory):
    from dominion.workers.worker import run_once

    async with db_factory() as s:
        book = await _book(s)
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=1))
        await s.commit()
        await background_work.set_queue_paused(s, True)
        await s.commit()
        row = await s.get(ModelOverride, background_work.QUEUE_PAUSED_KEY)
        assert row is not None and row.model == "1"

    # Paused: run_once must return False WITHOUT claiming (the job stays QUEUED).
    assert await run_once(session_factory=db_factory) is False
    async with db_factory() as s:
        job = (await s.execute(select(Job))).scalars().first()
        assert job is not None and job.status == JobStatus.QUEUED

    # load_queue_paused refreshes the process global from the persisted row.
    background_work._queue_paused = False
    async with db_factory() as s:
        assert await background_work.load_queue_paused(s) is True


async def test_pause_endpoint_gates_draft_next_and_resume_schedules(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=1))
        await s.commit()

        bg = BackgroundTasks()
        out = await jobs_router.pause(jobs_router.QueuePauseIn(paused=True), bg, s)
        assert out.queue_paused is True and out.queued == 1 and out.scheduled is False

        # draft-next refuses to schedule while paused.
        dn = await jobs_router.draft_next(BackgroundTasks(), s)
        assert dn.scheduled is False

        # status carries the flag.
        st = await jobs_router.status(s)
        assert st.queue_paused is True

        # Resume with jobs waiting schedules a drain.
        bg2 = BackgroundTasks()
        out2 = await jobs_router.pause(jobs_router.QueuePauseIn(paused=False), bg2, s)
        assert out2.queue_paused is False and out2.scheduled is True
        assert len(bg2.tasks) == 1
