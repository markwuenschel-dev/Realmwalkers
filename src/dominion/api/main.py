"""FastAPI app — the thin boundary between the React review app and Postgres (DESIGN §1).

Generation runs in-process as FastAPI background tasks (the drain in workers.background_work); this
module only wires routes, applies saved settings at boot, and resumes a drain that a redeploy killed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dominion.api.routers import (
    activity,
    beats,
    books,
    chapters,
    docs,
    health,
    jobs,
    learning,
    markup,
    packets,
    parts,
    pipeline,
    production,
    reviews,
    runs,
    scene_packets,
    scenes,
    telemetry,
    threads,
    volumes,
    world,
)
from dominion.api.routers import (
    settings as settings_router,
)
from dominion.api.routers.settings import apply_model_overrides
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobKind, JobStatus
from dominion.shared.models import Job, RepairTask
from dominion.workers import background_work

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """On startup, apply any saved per-agent model overrides to the live settings (so a model choice
    from the Settings screen survives a redeploy). Best-effort — a fresh DB has no table/rows yet."""
    try:
        async with SessionFactory() as session:
            n = await apply_model_overrides(session)
        if n:
            log.info("settings.model_overrides_applied", count=n)
    except Exception as exc:  # noqa: BLE001 — never block boot on an optional override load
        log.warning("settings.model_overrides_load_failed", error=str(exc))
    # Resume the drafting drain if a redeploy stranded QUEUED jobs. The drain is an in-process
    # background task, so a container swap kills it mid-queue and the jobs otherwise sit QUEUED
    # forever — silently, as "N queued" — until a human posts /jobs/draft-next. Fire-and-forget:
    # drain_queued_jobs already single-flights via its process-global lock and persists per-job
    # failures, so a boot-time kick is exactly as safe as a button-press kick.
    try:
        from sqlalchemy import false, func, select

        async with SessionFactory() as session:
            paused = await background_work.load_queue_paused(session)
            queued = (
                await session.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.kind == JobKind.DRAFT, Job.status == JobStatus.QUEUED)
                )
            ).scalar_one()
            queued_repairs = (
                await session.execute(
                    select(func.count())
                    .select_from(RepairTask)
                    .where(RepairTask.status == "queued", RepairTask.requires_human_approval == false())
                )
            ).scalar_one()
        if (queued or queued_repairs) and paused:
            # Honor the human pause switch across redeploys — work stays queued until resumed.
            log.info("draft.drain_resume_skipped_paused", queued=queued, queued_repairs=queued_repairs)
        else:
            # The repair drain chains into the job drain, so kick at most one of the two.
            if queued_repairs:
                log.info("repair.drain_resumed_on_boot", queued_repairs=queued_repairs)
                asyncio.get_running_loop().create_task(background_work.drain_queued_repair_tasks())
            elif queued:
                log.info("draft.drain_resumed_on_boot", queued=queued)
                asyncio.get_running_loop().create_task(background_work.drain_queued_jobs())
    except Exception as exc:  # noqa: BLE001 — never block boot on the resume probe
        log.warning("draft.drain_resume_failed", error=str(exc))

    # Start the autonomous self-repair + retention loop (workers/sweeper.py). One in-process background
    # task; it gates its own work behind the autonomy + queue-pause switches and single-flights each
    # tick. Cancelled on shutdown so a redeploy doesn't leave an orphan loop behind.
    sweeper_task: asyncio.Task[None] | None = None
    try:
        from dominion.workers import sweeper

        sweeper_task = asyncio.get_running_loop().create_task(sweeper.run_forever())
    except Exception as exc:  # noqa: BLE001 — a sweeper that fails to start must not block boot
        log.warning("sweeper.start_failed", error=str(exc))
    try:
        yield
    finally:
        if sweeper_task is not None:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Dominion Realm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(scenes.router)
app.include_router(reviews.router)
app.include_router(runs.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(parts.router)
app.include_router(volumes.router)
app.include_router(beats.router)
app.include_router(packets.router)
app.include_router(scene_packets.router)
app.include_router(production.router)
app.include_router(pipeline.router)
app.include_router(telemetry.router)
app.include_router(jobs.router)
app.include_router(world.router)
app.include_router(activity.router)
app.include_router(threads.router)
app.include_router(markup.router)
app.include_router(learning.router)
app.include_router(settings_router.router)
app.include_router(docs.router)
