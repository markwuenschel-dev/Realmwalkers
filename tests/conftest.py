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
# Tests must NEVER touch the network. With a real OPENAI_API_KEY in the developer's .env and the
# default provider "openai", every canon/retrieval test was silently making LIVE OpenAI embedding
# calls — test_canon_cleanup alone took ~8 minutes (3.5s on the hash backend), which was the entire
# "why is local pytest 8x slower than CI" mystery. Force the deterministic hash embedder.
os.environ["DOMINION_EMBEDDING_PROVIDER"] = "hash"

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


# Schema setup runs ONCE per pytest run, not once per test. The old per-test version rebuilt the
# world for every DB test (maintenance-engine probe + CREATE EXTENSION + full create_all + every
# lightweight migration + truncate + two engine disposals) — ~0.2s each on CI's local socket but
# ~2s each on Windows/Docker, which turned the full suite from ~1 minute into 8-9. Per-test
# isolation needs only the TRUNCATE. The one-time setup runs inside the FIRST requesting test's
# event loop and fully disposes its engines there, so nothing leaks across pytest-asyncio's
# function-scoped loops. The (ok, message) result is cached so an unreachable Postgres is paid for
# once, not once per skipped test (a fresh TCP connect timeout per test, previously).
_TABLES_SQL = ", ".join(f'"{name}"' for name in Base.metadata.tables)
_db_state: tuple[bool, str] | None = None


async def _setup_schema_once() -> tuple[bool, str]:
    maint_url, dbname = _split_db(_TEST_URL)
    # 1) ensure the test database exists — this connect doubles as the "is Postgres up?" probe.
    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        conn = await maint.connect()
    except Exception as exc:  # noqa: BLE001
        await maint.dispose()
        return False, f"Postgres not reachable for DB tests ({type(exc).__name__}): {exc}"
    try:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname})
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await conn.close()
        await maint.dispose()

    # 2) extension + schema, brought up to the current ORM exactly like the app's boot provisioner.
    engine = create_async_engine(_TEST_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # create_all won't add new columns to tables it already created — same idempotent
            # migration the app runs at boot.
            await apply_lightweight_migrations(conn)
    finally:
        await engine.dispose()
    return True, ""


@pytest.fixture
async def db_factory():
    global _db_state
    if _db_state is None:
        _db_state = await _setup_schema_once()
    ok, msg = _db_state
    if not ok:
        # Locally, no Postgres -> skip (DB tests are opt-in). In CI we set DOMINION_REQUIRE_DB so an
        # unreachable DB fails loudly instead of producing a falsely-green run.
        if os.environ.get("DOMINION_REQUIRE_DB"):
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)

    # Per-test: a fresh engine bound to this test's loop + a clean slate. ONE round trip.
    engine = create_async_engine(_TEST_URL)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES_SQL} RESTART IDENTITY CASCADE"))

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
