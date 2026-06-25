"""Browser-driven drafting (DESIGN §1, §4).

The worker normally runs from a terminal (`dominion-worker --once`). This router lets the review app
drive it instead: POST /jobs/draft-next schedules a single-flight background drain of the queue, so
clicking "approve beats" or "revise" in the Desk is all it takes to get prose drafted — no terminal.

A draft runs ONLY when triggered, so the "nothing runs between approvals" guarantee holds. The lock
keeps at most one drain in flight per process; the worker's atomic claim (FOR UPDATE SKIP LOCKED)
keeps it safe even if a terminal worker drains concurrently.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import func, select, update

from dominion.api.deps import SessionDep
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job, Run
from dominion.shared.schemas import ActiveScene, DraftNextOut, JobsStatusOut, RetryFailedOut
from dominion.workers import progress

log = structlog.get_logger()
router = APIRouter(prefix="/jobs", tags=["jobs"])

# At most one drain loop per process. FastAPI background tasks share the API event loop, so an
# asyncio.Lock is the right primitive (no threads involved).
_drain_lock = asyncio.Lock()


async def _drain() -> None:
    """Draft queued jobs one at a time until the queue empties.

    run_once already persists a failed job as FAILED and logs it; we swallow + keep draining so one
    bad scene doesn't strand the rest of the chapter. Imported lazily so the API process only loads
    the LLM stack when it actually drafts (mirrors workers.enqueue)."""
    if _drain_lock.locked():
        return
    async with _drain_lock:
        from dominion.workers.worker import run_once

        while True:
            try:
                did = await run_once()
            except Exception as exc:  # noqa: BLE001 — already marked FAILED + logged in run_once
                log.error("draft.drain_error", error=str(exc))
                did = True  # a FAILED job is no longer QUEUED, so the loop advances
            if not did:
                break


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
    background: BackgroundTasks, session: SessionDep, book_id: uuid.UUID | None = None,
) -> DraftNextOut:
    """Kick off drafting of the queued scenes (background, single-flight). Returns immediately.

    The drain itself is global (the worker claims the oldest queued job regardless of book); book_id
    only scopes the counts we report back so the caller sees its own book's queue."""
    counts = await _queue_counts(session, book_id)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = _drain_lock.locked()
    if queued and not running:
        background.add_task(_drain)
        running = True
    return DraftNextOut(scheduled=bool(queued) and running, queued=queued, running=running)


@router.post("/retry-failed", response_model=RetryFailedOut)
async def retry_failed(
    background: BackgroundTasks, session: SessionDep, book_id: uuid.UUID | None = None,
) -> RetryFailedOut:
    """Re-queue every FAILED job (scoped to a book when given), then kick off drafting.

    A FAILED job is terminal — draft-next only drains QUEUED — so a scene that died on a transient
    cause (API outage, depleted credits, a one-off 5xx) never redrafts on its own. This flips those
    rows back to QUEUED (clearing the stale claim) and schedules the same single-flight drain, so the
    Desk can offer a 'retry failed' affordance without a terminal or a DB round-trip."""
    # Collect the FAILED job ids first (scoped to the book when given) so we can report an exact
    # count without leaning on CursorResult.rowcount, which isn't on the async Result type.
    failed_q = select(Job.id).where(Job.status == JobStatus.FAILED)
    if book_id is not None:
        failed_q = failed_q.where(Job.run_id.in_(select(Run.id).where(Run.book_id == book_id)))
    failed_ids = (await session.execute(failed_q)).scalars().all()
    requeued = len(failed_ids)
    if failed_ids:
        await session.execute(
            update(Job)
            .where(Job.id.in_(failed_ids))
            .values(status=JobStatus.QUEUED, claimed_by=None, claimed_at=None)
        )
    await session.commit()

    counts = await _queue_counts(session, book_id)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = _drain_lock.locked()
    if queued and not running:
        background.add_task(_drain)
        running = True
    log.info("jobs.retry_failed", book=str(book_id) if book_id else None, requeued=requeued)
    return RetryFailedOut(
        requeued=requeued, scheduled=bool(queued) and running, queued=queued, running=running,
    )


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
    active = (await session.execute(
        active_stmt.order_by(Job.claimed_at.desc()).limit(1)
    )).first()
    running = JobStatus.RUNNING in counts
    if book_id is None:
        running = running or _drain_lock.locked()
    active_scene = None
    if active:
        job_id, chapter_no, scene_no = active
        phase, elapsed_s = progress.get(str(job_id))  # live sub-stage from the in-process registry
        active_scene = ActiveScene(
            chapter_no=chapter_no, scene_no=scene_no, phase=phase, elapsed_s=elapsed_s,
        )
    return JobsStatusOut(
        running=running,
        queued=counts.get(JobStatus.QUEUED, 0),
        failed=counts.get(JobStatus.FAILED, 0),
        active_scene=active_scene,
    )
