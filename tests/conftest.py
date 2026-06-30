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

from dominion.shared.migrations import apply_lightweight_migrations  # noqa: E402
from dominion.shared.models import Base  # noqa: E402


def _split_db(url: str) -> tuple[str, str]:
    """(url, dbname) -> (maintenance url pointing at 'postgres', test db name)."""
    base, _, dbname = url.rpartition("/")
    return f"{base}/postgres", dbname


async def seed_scene_packet(s, *, chapter, beat, body: dict | None = None):
    """Test helper: drafting is fail-closed on an approved ScenePacket, so any test that runs the
    pipeline must give its beat one. Creates a minimal approved ChapterPacket + ScenePacket and links
    `beat.scene_packet_id` (which assemble_context reads). Returns the ScenePacket.

    Importable from tests: `from conftest import seed_scene_packet` (tests/ is on sys.path).
    """
    from dominion.shared.models import ChapterPacket, ScenePacket

    cp = ChapterPacket(
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": []},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    # No word_budget by default, so the length guard stays inert for tests that fake short prose
    # (a budget would trigger an expansion LLM call). Tests exercising length pass an explicit body.
    sp = ScenePacket(
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        chapter_packet_id=cp.id,
        scene_no=(beat.scene_no if beat is not None else 1),
        status="approved",
        qa_verdict="approve",
        body=body
        or {
            "scene_no": beat.scene_no if beat is not None else 1,
            "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
            "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
            "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
        },
        source_hash="test",
    )
    s.add(sp)
    await s.flush()
    if beat is not None:
        beat.scene_packet_id = sp.id
        await s.flush()
    return sp


@pytest.fixture
async def db_factory():
    maint_url, dbname = _split_db(_TEST_URL)

    # 1) ensure the test database exists — and use this connect as the "is Postgres up?" probe.
    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        conn = await maint.connect()
    except Exception as exc:  # noqa: BLE001
        await maint.dispose()
        msg = f"Postgres not reachable for DB tests ({type(exc).__name__}): {exc}"
        # Locally, no Postgres -> skip (DB tests are opt-in). In CI we set DOMINION_REQUIRE_DB so an
        # unreachable DB fails loudly instead of producing a falsely-green run.
        if os.environ.get("DOMINION_REQUIRE_DB"):
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)
    try:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname})
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
        # Bring a pre-existing test DB up to the current ORM (create_all won't add new columns to
        # tables it already created) — same idempotent migration the app runs at boot.
        await apply_lightweight_migrations(conn)
        tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
