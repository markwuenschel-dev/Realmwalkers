"""FastAPI app — the thin boundary between the React review app and Postgres (DESIGN §1).

Generation runs in-process as FastAPI background tasks (the drain in workers.background_work); this
module only wires routes, applies saved settings at boot, and resumes a drain that a redeploy killed.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dominion.api.routers import (
    beats,
    books,
    chapters,
    docs,
    health,
    jobs,
    learning,
    markup,
    packets,
    production,
    reviews,
    runs,
    scene_packets,
    scenes,
    telemetry,
    threads,
    world,
)
from dominion.api.routers import (
    settings as settings_router,
)
from dominion.api.routers.settings import apply_model_overrides
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobKind, JobStatus
from dominion.shared.models import Job
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
        from sqlalchemy import func, select

        async with SessionFactory() as session:
            queued = (
                await session.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.kind == JobKind.DRAFT, Job.status == JobStatus.QUEUED)
                )
            ).scalar_one()
        if queued:
            log.info("draft.drain_resumed_on_boot", queued=queued)
            asyncio.get_running_loop().create_task(background_work.drain_queued_jobs())
    except Exception as exc:  # noqa: BLE001 — never block boot on the resume probe
        log.warning("draft.drain_resume_failed", error=str(exc))
    yield


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
app.include_router(beats.router)
app.include_router(packets.router)
app.include_router(scene_packets.router)
app.include_router(production.router)
app.include_router(telemetry.router)
app.include_router(jobs.router)
app.include_router(world.router)
app.include_router(threads.router)
app.include_router(markup.router)
app.include_router(learning.router)
app.include_router(settings_router.router)
app.include_router(docs.router)

# Serve the built React app from the SAME origin as the API (single-service deploy, e.g. Railway).
# The SPA calls the API with relative paths, so there's no separate API host, no CORS, no localhost.
# Guarded by is_dir() so local dev (Vite on its own port, no dist) and the test suite are unaffected.
_STATIC_DIR = Path(os.environ.get("DOMINION_STATIC_DIR") or Path(__file__).resolve().parents[3] / "frontend" / "dist")
if _STATIC_DIR.is_dir():
    _assets = _STATIC_DIR / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        """Serve a real static file if it exists; otherwise index.html so client-side routes work.
        Registered last, so every API route above still takes precedence."""
        candidate = _STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
