"""The worker: claim ONE queued job, draft one scene, persist, exit (DESIGN §1, §4, §10).

Between jobs, zero processes run — so there is nothing to boot, nothing to re-verify, and no
autonomous rolling. `--once` does a single job and exits; `--loop` polls. A hung job is killed
cleanly by the wall-clock budget (or the OS), leaving Postgres consistent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job
from dominion.workers import progress
from dominion.workers.pipeline import generate_one_scene

log = structlog.get_logger()
WORKER_ID = f"worker-{os.getpid()}"


async def claim_one_job(session: AsyncSession) -> Job | None:
    """Atomically claim the oldest queued job (FOR UPDATE SKIP LOCKED makes parallel workers safe)."""
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.claimed_by = WORKER_ID
    job.claimed_at = datetime.now(UTC)
    await session.flush()
    return job


async def run_once(session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> bool:
    """Process a single job. Returns False if the queue is empty. Wall-clock bounded.

    session_factory is injectable so tests can drive the worker against a test database.
    """
    async with session_factory() as session:
        job = await claim_one_job(session)
        if job is None:
            await session.commit()
            return False
        # Capture as a primitive: rollback() expires every ORM attribute, and reading an expired
        # one would fire a *sync* reload query (illegal under the async engine -> MissingGreenlet).
        job_id = job.id
        progress.set_phase(str(job_id), "starting")
        try:
            scene = await asyncio.wait_for(
                generate_one_scene(session, job), timeout=settings.scene_time_budget_s
            )
            job.status = JobStatus.DONE
            await session.commit()
            log.info("scene.drafted", job=str(job_id), scene=str(scene.id), tokens=scene.token_count)
            return True
        except Exception as exc:
            # rollback ends the failed transaction; the same session is then reusable for the
            # failure write. Use the captured id, never the expired `job` object.
            await session.rollback()
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                # Persist the reason so a FAILED job is diagnosable from the Desk/API without trawling
                # server logs (the prod worker logs to stdout we can't always reach). Capped for sanity.
                .values(status=JobStatus.FAILED, last_error=f"{type(exc).__name__}: {exc}"[:2000])
            )
            await session.commit()
            log.error("scene.failed", job=str(job_id), error=str(exc))
            raise
        finally:
            # The job is no longer running (done, failed, or timed out) — drop its live phase so the
            # status indicator doesn't report a stale "drafting…" for a scene that already finished.
            progress.clear(str(job_id))


async def _loop(interval: float) -> None:
    while True:
        try:
            did = await run_once()
        except Exception:
            did = True  # error already logged + persisted; keep the loop alive
        if not did:
            await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dominion worker: draft one scene, then exit.")
    parser.add_argument("--once", action="store_true", help="process a single job and exit")
    parser.add_argument("--loop", action="store_true", help="poll for jobs continuously")
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval for --loop")
    args = parser.parse_args()

    if args.loop:
        asyncio.run(_loop(args.interval))
    else:
        did = asyncio.run(run_once())
        if not did:
            log.info("worker.idle", msg="no queued jobs")


if __name__ == "__main__":
    main()
