"""The worker: claim ONE queued job, draft one scene, persist, exit (DESIGN §1, §4, §10).

Between jobs, zero processes run — so there is nothing to boot, nothing to re-verify, and no
autonomous rolling. `--once` does a single job and exits; `--loop` polls. A hung job is killed
cleanly by the wall-clock budget (or the OS), leaving Postgres consistent.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import traceback
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared import agent_ops
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobStatus
from dominion.shared.models import Job, ProductionRun
from dominion.workers import progress, run_stages
from dominion.workers.llm import find_rate_limit
from dominion.workers.pipeline import generate_one_scene

log = structlog.get_logger()
WORKER_ID = f"worker-{os.getpid()}"

# Pinned classification vocabulary (recovery L7): a job that dies on a provider 429 is retryable
# INFRASTRUCTURE state, never an author/contract failure. `infra_rate_limit` is the issue/problem
# kind; `provider_rate_limited` is the stage string parked on a production run whose scene job was
# refused by the provider (mirrors ScenePacketStatus.RATE_LIMITED on the derive side).
INFRA_RATE_LIMIT = "infra_rate_limit"
PROVIDER_RATE_LIMITED_STAGE = "provider_rate_limited"


def classify_job_failure(exc: BaseException, loc: str = "") -> tuple[str, str | None]:
    """(last_error, error_kind) for a job that just failed.

    A provider 429 ANYWHERE in the exception chain makes the failure retryable provider state:
    last_error is prefixed "LlmRateLimited" (even when the 429 arrived wrapped in another error) and
    the kind is "infra_rate_limit", so the Desk/diagnostics/retry-failed treat it as transient
    infrastructure — the scene contract was never invalid, the provider just refused the call.
    Anything else keeps the existing "<Type>: <message> @ file:line" shape."""
    rate_limited = find_rate_limit(exc)
    if rate_limited is not None:
        detail = str(exc) if exc is rate_limited else f"{type(exc).__name__}: {exc}"
        return f"LlmRateLimited: {detail}{loc}"[:2000], INFRA_RATE_LIMIT
    return f"{type(exc).__name__}: {exc}{loc}"[:2000], None


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
        await agent_ops.apply_model_overrides(session)
        job = await claim_one_job(session)
        if job is None:
            await session.commit()
            return False
        # Capture as primitives: rollback() expires every ORM attribute, and reading an expired
        # one would fire a *sync* reload query (illegal under the async engine -> MissingGreenlet).
        job_id = job.id
        production_run_id = job.production_run_id
        progress.set_phase(str(job_id), "starting")
        try:
            scene = await asyncio.wait_for(generate_one_scene(session, job), timeout=settings.scene_time_budget_s)
            job.status = JobStatus.DONE
            job.finished_at = datetime.now(UTC)
            # Stamp the produced scene so /jobs/recent can join word counts for draft jobs too
            # (fresh drafts otherwise never carry target_scene_id — only revisions do).
            if job.target_scene_id is None:
                job.target_scene_id = scene.id
            await session.commit()
            log.info("scene.drafted", job=str(job_id), scene=str(scene.id), tokens=scene.token_count)
            return True
        except Exception as exc:
            # rollback ends the failed transaction; the same session is then reusable for the
            # failure write. Use the captured id, never the expired `job` object.
            await session.rollback()
            # Persist the reason + the in-repo line it came from, so a FAILED job is diagnosable from
            # the Desk/API without trawling server logs we can't always reach. Capped for sanity.
            tb = traceback.extract_tb(exc.__traceback__)
            frame = next((f for f in reversed(tb) if "dominion" in f.filename), tb[-1] if tb else None)
            loc = f" @ {os.path.basename(frame.filename)}:{frame.lineno}" if frame else ""
            last_error, error_kind = classify_job_failure(exc, loc)
            await session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(status=JobStatus.FAILED, last_error=last_error, finished_at=datetime.now(UTC))
            )
            if error_kind == INFRA_RATE_LIMIT and production_run_id is not None:
                # The run is not broken and neither is the contract — the provider refused the call.
                # Park the run on the retryable provider stage (a plain stage string, no migration)
                # instead of leaving it stuck on a stage that reads like a pipeline failure.
                await session.execute(
                    update(ProductionRun)
                    .where(ProductionRun.id == production_run_id)
                    .values(current_stage=PROVIDER_RATE_LIMITED_STAGE)
                )
            await session.commit()
            # L6 (run orchestration): a provider 429 past retries is transient infrastructure — the
            # owning production run parks in the retryable provider_rate_limited stage, never in a
            # contract-failure state. L7's classify_job_failure already stamped the stage above; this
            # best-effort pass adds the run event trail. Never mask the original error being re-raised.
            if production_run_id is not None and run_stages.stage_after_draft_failure(exc) is not None:
                try:
                    from dominion.workers import production as prod

                    await prod.mark_run_provider_rate_limited(session, production_run_id, str(exc))
                    await session.commit()
                except Exception:  # noqa: BLE001 — diagnostics only; the job failure already persisted
                    log.error("run.rate_limit_flag_failed", job=str(job_id))
            log.error("scene.failed", job=str(job_id), error=str(exc), error_kind=error_kind)
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
