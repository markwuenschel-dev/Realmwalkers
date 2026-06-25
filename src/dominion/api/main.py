"""FastAPI app — the thin boundary between the React review app and Postgres (DESIGN §1).

It never runs generation; it reads the queue and writes decisions. The ~20-minute work lives in the
separate worker process.
"""
from __future__ import annotations

import os
from pathlib import Path

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
    reviews,
    runs,
    scenes,
    threads,
    world,
)
from dominion.shared.config import settings

app = FastAPI(title="Dominion Realm API", version="0.1.0")

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
app.include_router(jobs.router)
app.include_router(world.router)
app.include_router(threads.router)
app.include_router(markup.router)
app.include_router(learning.router)
app.include_router(docs.router)

# Serve the built React app from the SAME origin as the API (single-service deploy, e.g. Railway).
# The SPA calls the API with relative paths, so there's no separate API host, no CORS, no localhost.
# Guarded by is_dir() so local dev (Vite on its own port, no dist) and the test suite are unaffected.
_STATIC_DIR = Path(
    os.environ.get("DOMINION_STATIC_DIR")
    or Path(__file__).resolve().parents[3] / "frontend" / "dist"
)
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
