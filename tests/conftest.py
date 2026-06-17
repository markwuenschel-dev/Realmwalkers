"""Postgres-backed test fixtures.

Forces the app DB to a dedicated test database (never your dev DB — these TRUNCATE), then hands out
a session factory bound to the running event loop. DB tests request `db_factory`; everything else
runs without a database. If Postgres isn't reachable, the DB tests skip rather than fail.
"""
from __future__ import annotations

import os

# Must run BEFORE any dominion import so Settings binds to the test database.
_TEST_URL = os.environ.get(
    "DOMINION_TEST_DATABASE_URL",
    "postgresql+asyncpg://dominion:dominion@127.0.0.1:5432/dominion_test",
)
os.environ["DOMINION_DATABASE_URL"] = _TEST_URL

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dominion.shared.models import Base  # noqa: E402


def _split_db(url: str) -> tuple[str, str]:
    """(url, dbname) -> (maintenance url pointing at 'postgres', test db name)."""
    base, _, dbname = url.rpartition("/")
    return f"{base}/postgres", dbname


@pytest.fixture
async def db_factory():
    maint_url, dbname = _split_db(_TEST_URL)

    # 1) ensure the test database exists — and use this connect as the "is Postgres up?" probe.
    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        conn = await maint.connect()
    except Exception as exc:  # noqa: BLE001
        await maint.dispose()
        pytest.skip(f"Postgres not reachable for DB tests ({type(exc).__name__}): {exc}")
    try:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await conn.close()
        await maint.dispose()

    # 2) extension + schema + clean slate. Engine is created in (and bound to) the test's loop.
    engine = create_async_engine(_TEST_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
