"""Hard-delete for llm_calls telemetry rows (book, run, or global wipe)."""

from __future__ import annotations

import uuid

import structlog
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import LlmCall

log = structlog.get_logger()


async def delete_book_telemetry(session: AsyncSession, book_id: uuid.UUID) -> int:
    """Delete all telemetry calls for one book. Returns deleted row count."""
    result = await session.execute(delete(LlmCall).where(LlmCall.book_id == book_id))
    deleted = result.rowcount or 0
    log.info("telemetry.cleared", scope="book", book_id=str(book_id), deleted_calls=deleted)
    return deleted


async def delete_run_telemetry(session: AsyncSession, book_id: uuid.UUID, run_id: uuid.UUID) -> int:
    """Delete telemetry for one run scoped to a book. 404 if the run has no calls in that book."""
    exists = (
        await session.execute(select(LlmCall.id).where(LlmCall.run_id == run_id, LlmCall.book_id == book_id).limit(1))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Run not found in book")

    result = await session.execute(delete(LlmCall).where(LlmCall.run_id == run_id, LlmCall.book_id == book_id))
    deleted = result.rowcount or 0
    log.info(
        "telemetry.run_deleted",
        book_id=str(book_id),
        run_id=str(run_id),
        deleted_calls=deleted,
    )
    return deleted


async def delete_all_telemetry(session: AsyncSession) -> int:
    """Delete every llm_calls row (global wipe). Returns deleted row count."""
    result = await session.execute(delete(LlmCall))
    deleted = result.rowcount or 0
    log.info("telemetry.cleared", scope="global", deleted_calls=deleted)
    return deleted
