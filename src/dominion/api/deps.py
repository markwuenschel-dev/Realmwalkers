"""Shared FastAPI dependencies."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.db import SessionFactory


async def db_session() -> AsyncIterator[AsyncSession]:
    # Mutating handlers commit explicitly, so the write lands BEFORE the response is sent. (A
    # yield-dependency's post-yield code runs AFTER the response — committing here would let an
    # immediate read-after-write observe stale data.) This dependency only guarantees rollback on
    # error and close; read-only handlers never commit (nothing to persist).
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(db_session)]
