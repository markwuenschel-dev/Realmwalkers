"""Live pipeline dashboard endpoint — one fan-out of reads for the Desk's Pipeline tab.

Everything the production pipeline is doing right now, book-wide: what's running, what's queued (and
that it runs one at a time), what's waiting on the human (and why), what's blocked (and how to
unblock), what's completed, and whether the autonomous sweeper is alive. All the read/derive logic
lives in workers/pipeline_status.py; this is the thin HTTP boundary.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException

from dominion.api.deps import SessionDep
from dominion.shared.models import Book
from dominion.shared.schemas import PipelineStatusOut
from dominion.workers.pipeline_status import build_pipeline_status

log = structlog.get_logger()
router = APIRouter(prefix="/books", tags=["pipeline"])


@router.get("/{book_id}/pipeline", response_model=PipelineStatusOut)
async def get_pipeline(book_id: uuid.UUID, session: SessionDep) -> PipelineStatusOut:
    """One live snapshot of the whole production pipeline for a book (see PipelineStatusOut). The Desk
    polls this ~3s while the Pipeline tab is active; each section carries pre-computed human reasons +
    suggested actions so the frontend stays thin."""
    if await session.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")
    return await build_pipeline_status(session, book_id)
