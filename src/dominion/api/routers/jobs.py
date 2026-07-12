"""Browser-driven drafting (DESIGN §1, §4).

The worker normally runs from a terminal (`dominion-worker --once`). This router lets the review app
drive it instead: POST /jobs/draft-next schedules a single-flight background drain of the queue, so
clicking "approve beats" or "revise" in the Desk is all it takes to get prose drafted — no terminal.

A draft runs ONLY when triggered, so the "nothing runs between approvals" guarantee holds. The lock
keeps at most one drain in flight per process; the worker's atomic claim (FOR UPDATE SKIP LOCKED)
keeps it safe even if a terminal worker drains concurrently.

A second triggered/boot-resumed drain exists for repair tasks (background_work.drain_queued_repair_tasks,
kicked by triage/verify/apply-all and the boot resume): already-triaged bounded repairs auto-apply under
the SAME pause switch, and requires_human_approval tasks always wait for an explicit Approve & apply
(DESIGN §5 documents this deliberate posture change).
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import func, or_, select

from dominion.api.deps import SessionDep
from dominion.shared.enums import JobStatus
from dominion.shared.job_integrity import inspect_job_ownership
from dominion.shared.job_policy import scope_jobs_to_book
from dominion.shared.models import Job, Scene
from dominion.shared.schemas import (
    ActiveScene,
    CancelJobOut,
    ClearFailedOut,
    ClearFinishedJobsOut,
    DraftNextOut,
    FailedJobOut,
    IntegrityHoldOut,
    IntegrityHoldsOut,
    JobsPauseOut,
    JobsStatusOut,
    QueuedJobOut,
    QueuePauseIn,
    RecentJobOut,
    RecentJobsOut,
    RetryFailedOut,
)
from dominion.workers import background_work, progress

log = structlog.get_logger()
router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _queue_counts(session: SessionDep, book_id: uuid.UUID | None = None) -> dict[str, int]:
    """Counts grouped by status. Scoped to one book when book_id is given via the shared single-key
    `scope_jobs_to_book` (ADR 0027), so the Desk's indicator reflects the book you're viewing."""
    stmt = scope_jobs_to_book(select(Job.status, func.count()), book_id)
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
    if queued and not running and not background_work.queue_paused():
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
    if queued and not running and not background_work.queue_paused():
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


@router.post("/pause", response_model=JobsPauseOut)
async def pause(
    body: QueuePauseIn,
    background: BackgroundTasks,
    session: SessionDep,
    book_id: uuid.UUID | None = None,
) -> JobsPauseOut:
    """Flip the human pause switch. Pausing lets the in-flight scene finish and stops the drain
    from claiming more (persisted — survives redeploys; the boot-resume honors it). Resuming with
    jobs waiting kicks the drain immediately, same pattern as /draft-next."""
    await background_work.set_queue_paused(session, body.paused)
    await session.commit()
    counts = await _queue_counts(session, book_id)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = background_work.drain_locked()
    scheduled = False
    if not body.paused:
        # Resume BOTH drains the switch was holding. The repair drain chains into the job drain and
        # both single-flight, so kicking it first covers queued repairs AND queued jobs in one shot.
        from sqlalchemy import false

        from dominion.shared.models import RepairTask

        queued_repairs = (
            await session.execute(
                select(func.count())
                .select_from(RepairTask)
                .where(RepairTask.status == "queued", RepairTask.requires_human_approval == false())
            )
        ).scalar_one()
        if queued_repairs and not background_work.repair_drain_locked():
            background.add_task(background_work.drain_queued_repair_tasks)
            scheduled = True
            running = True
        elif queued and not running:
            background.add_task(background_work.drain_queued_jobs)
            scheduled = True
            running = True
    log.info("jobs.queue_paused" if body.paused else "jobs.queue_resumed", queued=queued)
    return JobsPauseOut(queue_paused=body.paused, queued=queued, running=running, scheduled=scheduled)


@router.delete("/{job_id}", response_model=CancelJobOut)
async def cancel(job_id: uuid.UUID, session: SessionDep) -> CancelJobOut:
    """Cancel one QUEUED job (Activity drawer ×). RUNNING jobs are not cancellable — the worker
    holds their row lock for the whole generation, and skip_locked turns that into a clean 409."""
    from fastapi import HTTPException

    from dominion.workers.draft_queue import cancel_queued_job

    cancelled = await cancel_queued_job(session, job_id)
    if cancelled is None:
        existing = await session.get(Job, job_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="job not found")
        raise HTTPException(
            status_code=409,
            detail=f"only QUEUED jobs can be cancelled — this job is {existing.status}",
        )
    await session.commit()
    counts = await _queue_counts(session, None)
    log.info("jobs.cancelled", job=str(job_id), scene=cancelled.scene_no, chapter=cancelled.chapter_no)
    return CancelJobOut(
        id=job_id,
        chapter_no=cancelled.chapter_no,
        scene_no=cancelled.scene_no,
        queued=counts.get(JobStatus.QUEUED, 0),
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


@router.post("/clear-finished", response_model=ClearFinishedJobsOut)
async def clear_finished(
    session: SessionDep,
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
) -> ClearFinishedJobsOut:
    """Delete DONE jobs — clears the Activity drawer's 'recently finished' history. DONE jobs are pure
    exhaust; the scenes they produced are untouched. (Failed jobs have their own /jobs/clear-failed.)"""
    from dominion.workers.draft_queue import purge_done_draft_jobs

    purge = await purge_done_draft_jobs(session, book_id=book_id, chapter_id=chapter_id)
    await session.commit()
    log.info(
        "jobs.clear_finished",
        book=str(book_id) if book_id else None,
        chapter=str(chapter_id) if chapter_id else None,
        purged=purge.purged,
    )
    return ClearFinishedJobsOut(purged=purge.purged)


@router.get("/status", response_model=JobsStatusOut)
async def status(session: SessionDep, book_id: uuid.UUID | None = None) -> JobsStatusOut:
    """Queue depth + which scene is drafting now, so the Desk shows a live indicator.

    Scoped to book_id when given: `running` then means *this* book has a job in flight, so drafting
    another book never lights up this book's indicator. Unscoped, the global drain lock still counts
    (the terminal-driven path has no book context)."""
    counts = await _queue_counts(session, book_id)
    active_stmt = scope_jobs_to_book(
        select(Job.id, Job.chapter_no, Job.scene_no).where(Job.status == JobStatus.RUNNING), book_id
    )
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
    integrity = (
        await session.execute(
            select(func.count()).select_from(Job).where(or_(Job.status == JobStatus.QUARANTINED, Job.book_id.is_(None)))
        )
    ).scalar_one()
    return JobsStatusOut(
        running=running,
        queued=counts.get(JobStatus.QUEUED, 0),
        failed=counts.get(JobStatus.FAILED, 0),
        queue_paused=background_work.queue_paused(),
        active_scene=active_scene,
        integrity_holds=integrity,
        last_cache_hit_ratio=last["cache_hit_ratio"] if last else None,
        last_cache_read_tokens=last["total_cache_read_tokens"] if last else None,
        last_cache_creation_tokens=last["total_cache_creation_tokens"] if last else None,
        last_cache_tokens_saved=last["cache_tokens_saved"] if last else None,
    )


@router.get("/recent", response_model=RecentJobsOut)
async def recent(session: SessionDep, book_id: uuid.UUID | None = None, limit: int = 15) -> RecentJobsOut:
    """Queue positions + the last N terminal jobs, for the Activity drawer. The LIVE job is not
    here — /jobs/status already carries it with phase/elapsed at the fast poll. Two slim queries."""
    limit = max(1, min(limit, 50))
    queued_rows = (
        await session.execute(
            scope_jobs_to_book(
                select(Job.id, Job.kind, Job.chapter_no, Job.scene_no, Job.created_at).where(
                    Job.status == JobStatus.QUEUED
                ),
                book_id,
            ).order_by(Job.created_at)
        )
    ).all()
    recent_rows = (
        await session.execute(
            scope_jobs_to_book(
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
    stmt = scope_jobs_to_book(
        select(Job.id, Job.chapter_no, Job.scene_no, Job.last_error).where(Job.status == JobStatus.FAILED), book_id
    )
    rows = (await session.execute(stmt.order_by(Job.chapter_no, Job.scene_no))).all()
    return [FailedJobOut(id=jid, chapter_no=ch, scene_no=sc, last_error=err) for jid, ch, sc, err in rows]


@router.get("/integrity-holds", response_model=IntegrityHoldsOut)
async def integrity_holds(session: SessionDep) -> IntegrityHoldsOut:
    """Operator surface for the book-ownership invariant (ADR 0027): every job blocking full constraint
    promotion — quarantined live jobs AND any still-unresolved (terminal/conflict) NULL-book row. These
    have no book, so this endpoint is deliberately NOT book-scoped."""
    report = await inspect_job_ownership(await session.connection())
    return IntegrityHoldsOut(
        count=report.hold_count,
        promoted=report.promoted,
        conflicts=report.conflicts,
        holds=[
            IntegrityHoldOut(
                id=h["id"],
                status=h["status"],
                reason=h["reason"],
                chapter_no=h["chapter_no"],
                scene_no=h["scene_no"],
                last_error=h["last_error"],
            )
            for h in report.holds
        ],
    )
