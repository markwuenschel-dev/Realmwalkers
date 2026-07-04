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
from sqlalchemy import Select, func, or_, select

from dominion.api.deps import SessionDep
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job, Run, Scene
from dominion.shared.schemas import (
    ActiveScene,
    ClearFailedOut,
    DraftNextOut,
    FailedJobOut,
    JobsStatusOut,
    QueuedJobOut,
    RecentJobOut,
    RecentJobsOut,
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
    chapter_id: uuid.UUID | None = None,
) -> ClearFailedOut:
    """Delete FAILED draft jobs without re-queueing — dismisses the Desk failed banner."""
    from dominion.workers.draft_queue import purge_failed_draft_jobs

    purge = await purge_failed_draft_jobs(session, book_id=book_id, chapter_id=chapter_id)
    await session.commit()

    counts = await _queue_counts(session, book_id)
    log.info(
        "jobs.clear_failed",
        book=str(book_id) if book_id else None,
        chapter=str(chapter_id) if chapter_id else None,
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


def _scope_to_book(stmt: Select, book_id: uuid.UUID | None) -> Select:
    """Book scoping that catches both routing generations: new jobs carry book_id directly;
    legacy jobs are reachable only through their run."""
    if book_id is None:
        return stmt
    return stmt.where(or_(Job.book_id == book_id, Job.run_id.in_(select(Run.id).where(Run.book_id == book_id))))


@router.get("/recent", response_model=RecentJobsOut)
async def recent(session: SessionDep, book_id: uuid.UUID | None = None, limit: int = 15) -> RecentJobsOut:
    """Queue positions + the last N terminal jobs, for the Activity drawer. The LIVE job is not
    here — /jobs/status already carries it with phase/elapsed at the fast poll. Two slim queries."""
    limit = max(1, min(limit, 50))
    queued_rows = (
        await session.execute(
            _scope_to_book(
                select(Job.id, Job.kind, Job.chapter_no, Job.scene_no, Job.created_at).where(
                    Job.status == JobStatus.QUEUED
                ),
                book_id,
            ).order_by(Job.created_at)
        )
    ).all()
    recent_rows = (
        await session.execute(
            _scope_to_book(
                select(
                    Job.id,
                    Job.kind,
                    Job.status,
                    Job.chapter_no,
                    Job.scene_no,
                    Job.last_error,
                    Job.claimed_at,
                    Job.finished_at,
                    Scene.word_count,
                )
                .join(Scene, Scene.id == Job.target_scene_id, isouter=True)
                .where(Job.status.in_([JobStatus.DONE, JobStatus.FAILED])),
                book_id,
            )
            .order_by(Job.finished_at.desc().nulls_last(), Job.created_at.desc())
            .limit(limit)
        )
    ).all()
    return RecentJobsOut(
        queued=[
            QueuedJobOut(id=jid, kind=kind, chapter_no=ch, scene_no=sc, created_at=created)
            for jid, kind, ch, sc, created in queued_rows
        ],
        recent=[
            RecentJobOut(
                id=jid,
                kind=kind,
                status=status,
                chapter_no=ch,
                scene_no=sc,
                last_error=err,
                claimed_at=claimed,
                finished_at=finished,
                # Null-safe: rows that finished before the finished_at column existed show no duration.
                duration_s=int((finished - claimed).total_seconds()) if finished and claimed else None,
                word_count=wc,
            )
            for jid, kind, status, ch, sc, err, claimed, finished, wc in recent_rows
        ],
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
