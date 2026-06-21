"""FastAPI app — the thin boundary between the React review app and Postgres (DESIGN §1).

It never runs generation; it reads the queue and writes decisions. The ~20-minute work lives in the
separate worker process.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# aliased: the module name `annotations` would shadow `from __future__ import annotations`
from dominion.api.routers import annotations as annotations_routes
from dominion.api.routers import (
    beats,
    books,
    chapters,
    health,
    reviews,
    runs,
    scenes,
    suggestions,
    threads,
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
app.include_router(threads.router)
app.include_router(annotations_routes.router)
app.include_router(suggestions.router)
