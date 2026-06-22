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

import structlog
from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import func, select

from dominion.api.deps import SessionDep
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job
from dominion.shared.schemas import ActiveScene, DraftNextOut, JobsStatusOut

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


async def _queue_counts(session: SessionDep) -> dict[str, int]:
    rows = (await session.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    return {str(status): int(count) for status, count in rows}


@router.post("/draft-next", response_model=DraftNextOut)
async def draft_next(background: BackgroundTasks, session: SessionDep) -> DraftNextOut:
    """Kick off drafting of the queued scenes (background, single-flight). Returns immediately."""
    counts = await _queue_counts(session)
    queued = counts.get(JobStatus.QUEUED, 0)
    running = _drain_lock.locked()
    if queued and not running:
        background.add_task(_drain)
        running = True
    return DraftNextOut(scheduled=bool(queued) and running, queued=queued, running=running)


@router.get("/status", response_model=JobsStatusOut)
async def status(session: SessionDep) -> JobsStatusOut:
    """Queue depth + which scene is drafting now, so the Desk shows a live indicator."""
    counts = await _queue_counts(session)
    active = (await session.execute(
        select(Job.chapter_no, Job.scene_no)
        .where(Job.status == JobStatus.RUNNING)
        .order_by(Job.claimed_at.desc())
        .limit(1)
    )).first()
    return JobsStatusOut(
        running=_drain_lock.locked() or JobStatus.RUNNING in counts,
        queued=counts.get(JobStatus.QUEUED, 0),
        failed=counts.get(JobStatus.FAILED, 0),
        active_scene=ActiveScene(chapter_no=active[0], scene_no=active[1]) if active else None,
    )
