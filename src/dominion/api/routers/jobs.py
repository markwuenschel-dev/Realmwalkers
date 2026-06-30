"""Browser-driven drafting (DESIGN §1, §4).

The worker normally runs from a terminal (`dominion-worker --once`). This router lets the review app
drive it instead: POST /jobs/draft-next schedules a single-flight background drain of the queue, so
clicking "approve beats" or "revise" in the Desk is all it takes to get prose drafted — no terminal.

A draft runs ONLY when triggered, so the "nothing runs between approvals" guarantee holds. The lock
keeps at most one drain in flight per process; the worker's atomic claim (FOR UPDATE SKIP LOCKED)
keeps it safe even if a terminal worker drains concurrently.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import func, select

from dominion.api.deps import SessionDep
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job, Run
from dominion.shared.schemas import (
    ActiveScene,
    ClearFailedOut,
    DraftNextOut,
    FailedJobOut,
    JobsStatusOut,
    RetryFailedOut,
)
from dominion.workers import background_work, progress

log = structlog.get_logger()
router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _queue_counts(session: SessionDep, book_id: uuid.UUID | None = None) -> dict[str, int]:
    """Counts grouped by status. Scoped to one book (via its runs) when book_id is given, so the
    Desk's indicator reflects the book you're viewing — not every book's jobs at once."""
    stmt = select(Job.status, func.count())
    if book_id is not None:
        stmt = stmt.join(Run, Job.run_id == Run.id).where(Run.book_id == book_id)
    rows = (await session.execute(stmt.group_by(Job.status))).all()
    return {str(status): int(count) for status, count in rows}


@router.post("/draft-next", response_model=DraftNextOut)
async def draft_next(
    background: BackgroundTasks,
    session: SessionDep,
    book_id: uuid.UUID | None = None,
) -> DraftNextOut:
    """Kick off drafting of the queued scenes (background, single-flight). Returns immediately.

    The drain itself is global (the worker claims the oldest queued job regardless of book); book_id
    only scopes the counts we report back so the caller sees its own book's queue."""
    counts = await _queue_counts(session, book_id)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = background_work.drain_locked()
    if queued and not running:
        background.add_task(background_work.drain_queued_jobs)
        running = True
    return DraftNextOut(scheduled=bool(queued) and running, queued=queued, running=running)


@router.post("/retry-failed", response_model=RetryFailedOut)
async def retry_failed(
    background: BackgroundTasks,
    session: SessionDep,
    book_id: uuid.UUID | None = None,
) -> RetryFailedOut:
    """Re-queue FAILED draft jobs with fresh ScenePacket resolution — never clone null scene_packet_id."""
    from dominion.workers.draft_queue import reconcile_and_requeue_failed_draft_jobs
    from dominion.workers.draft_readiness import blocker_out

    requeue = await reconcile_and_requeue_failed_draft_jobs(session, book_id=book_id)
    await session.commit()

    counts = await _queue_counts(session, book_id)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = background_work.drain_locked()
    if queued and not running:
        background.add_task(background_work.drain_queued_jobs)
        running = True
    log.info(
        "jobs.retry_failed",
        book=str(book_id) if book_id else None,
        requested=requeue.requested,
        requeued=requeue.queued,
        skipped=len(requeue.skipped),
    )
    return RetryFailedOut(
        requested=requeue.requested,
        requeued=requeue.queued,
        scheduled=bool(queued) and running,
        queued=queued,
        running=running,
        skipped=[blocker_out(b) for b in requeue.skipped],
    )


@router.post("/clear-failed", response_model=ClearFailedOut)
async def clear_failed(
    session: SessionDep,
    book_id: uuid.UUID | None = None,
) -> ClearFailedOut:
    """Delete FAILED draft jobs without re-queueing — dismisses the Desk failed banner."""
    from dominion.workers.draft_queue import purge_failed_draft_jobs

    purge = await purge_failed_draft_jobs(session, book_id=book_id)
    await session.commit()

    counts = await _queue_counts(session, book_id)
    log.info(
        "jobs.clear_failed",
        book=str(book_id) if book_id else None,
        purged=purge.purged,
        failed_remaining=counts.get(JobStatus.FAILED, 0),
    )
    return ClearFailedOut(purged=purge.purged, failed=counts.get(JobStatus.FAILED, 0))


@router.get("/status", response_model=JobsStatusOut)
async def status(session: SessionDep, book_id: uuid.UUID | None = None) -> JobsStatusOut:
    """Queue depth + which scene is drafting now, so the Desk shows a live indicator.

    Scoped to book_id when given: `running` then means *this* book has a job in flight, so drafting
    another book never lights up this book's indicator. Unscoped, the global drain lock still counts
    (the terminal-driven path has no book context)."""
    counts = await _queue_counts(session, book_id)
    active_stmt = select(Job.id, Job.chapter_no, Job.scene_no).where(Job.status == JobStatus.RUNNING)
    if book_id is not None:
        active_stmt = active_stmt.join(Run, Job.run_id == Run.id).where(Run.book_id == book_id)
    active = (await session.execute(active_stmt.order_by(Job.claimed_at.desc()).limit(1))).first()
    running = JobStatus.RUNNING in counts
    if book_id is None:
        running = running or background_work.drain_locked()
    active_scene = None
    if active:
        job_id, chapter_no, scene_no = active
        phase, elapsed_s = progress.get(str(job_id))  # live sub-stage from the in-process registry
        cache = progress.get_cache_stats(str(job_id))
        active_scene = ActiveScene(
            chapter_no=chapter_no,
            scene_no=scene_no,
            phase=phase,
            elapsed_s=elapsed_s,
            cache_hit_ratio=cache["cache_hit_ratio"] if cache else None,
            total_cache_read_tokens=cache["total_cache_read_tokens"] if cache else None,
            total_cache_creation_tokens=cache["total_cache_creation_tokens"] if cache else None,
        )
    last = progress.get_last_cache()
    return JobsStatusOut(
        running=running,
        queued=counts.get(JobStatus.QUEUED, 0),
        failed=counts.get(JobStatus.FAILED, 0),
        active_scene=active_scene,
        last_cache_hit_ratio=last["cache_hit_ratio"] if last else None,
        last_cache_read_tokens=last["total_cache_read_tokens"] if last else None,
        last_cache_creation_tokens=last["total_cache_creation_tokens"] if last else None,
        last_cache_tokens_saved=last["cache_tokens_saved"] if last else None,
    )


@router.get("/failed", response_model=list[FailedJobOut])
async def failed(session: SessionDep, book_id: uuid.UUID | None = None) -> list[FailedJobOut]:
    """Every FAILED job with the reason it died — so the Desk can show the actual error (a bad API
    key, depleted credits, a 5xx) instead of a generic 'transient issue', and so a failure is
    diagnosable without server-log access. Scoped to a book when given."""
    stmt = select(Job.id, Job.chapter_no, Job.scene_no, Job.last_error).where(Job.status == JobStatus.FAILED)
    if book_id is not None:
        stmt = stmt.where(Job.run_id.in_(select(Run.id).where(Run.book_id == book_id)))
    rows = (await session.execute(stmt.order_by(Job.chapter_no, Job.scene_no))).all()
    return [FailedJobOut(id=jid, chapter_no=ch, scene_no=sc, last_error=err) for jid, ch, sc, err in rows]
