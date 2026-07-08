"""Async database engine + session (SQLAlchemy 2.0 + asyncpg)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dominion.shared.config import settings

# pool_size/max_overflow raised above the SQLAlchemy defaults (5/10) so the Desk's refresh fan-out
# (several concurrent chapter/scene/jobs queries) isn't throttled into serial waves of 15.
engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
# expire_on_commit=False is LOAD-BEARING: routers/workers read ORM attributes after commit() (enrich ->
# model_validate), and an async session can't lazy-load an expired attribute — that sync IO raises
# MissingGreenlet (the N1/C1 500 class). Keep it False; tests/test_session_config.py pins this invariant.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI/worker dependency: yields a session, commits on success, rolls back on error."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
