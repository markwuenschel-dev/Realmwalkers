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

# ---------------------------------------------------------------------------
# HARD COST KILL — process env wins over repo .env in pydantic-settings.
# A populated developer .env with LITELLM_VIRTUAL_KEY=sk-… turns on the LiteLLM
# gateway path inside llm.complete(). Unit tests that only mock _openai_client
# (Responses path) then sail past the mock and bill a real provider. Same for
# direct OPENAI_/ANTHROPIC_/XAI_/GEMINI_ keys. Blank every live credential here
# BEFORE Settings() is constructed (first dominion import below).
# ---------------------------------------------------------------------------
for _cost_env in (
    "LITELLM_VIRTUAL_KEY",
    "LITELLM_BASE_URL",
    "LITELLM_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
):
    os.environ[_cost_env] = ""
# Marker so accidental live code can detect hermetic test runs.
os.environ["DOMINION_HERMETIC_TESTS"] = "1"

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dominion.shared.config import settings as _app_settings  # noqa: E402
from dominion.shared.migrations import apply_lightweight_migrations  # noqa: E402
from dominion.shared.models import Base  # noqa: E402

# Fail closed if Settings still bound a real gateway key (env_file beat us somehow).
_gw = (_app_settings.litellm_virtual_key or "").strip()
if _gw.startswith("sk-"):
    raise RuntimeError(
        "HERMETIC TEST GUARD: settings.litellm_virtual_key is a live sk-… key. "
        "tests/conftest.py must blank LITELLM_VIRTUAL_KEY before Settings load. "
        "Refusing to run tests that would bill the LiteLLM gateway."
    )


@pytest.fixture(autouse=True)
def _hermetic_llm_no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test: gateway off, provider keys cleared, cached HTTP clients dropped.

    Individual tests may monkeypatch settings.* back to fake keys for mocked paths;
    they must still mock the client — empty keys alone do not open the network if
    mocks are correct. Clearing the gateway flag is the critical part: it keeps
    complete() on the Responses/Anthropic branches that tests actually patch.
    """
    monkeypatch.setattr(_app_settings, "litellm_virtual_key", None)
    monkeypatch.setattr(_app_settings, "litellm_model", None)
    monkeypatch.setattr(_app_settings, "openai_api_key", None)
    monkeypatch.setattr(_app_settings, "anthropic_api_key", None)
    monkeypatch.setattr(_app_settings, "xai_api_key", None)
    monkeypatch.setattr(_app_settings, "google_api_key", None)

    # Drop any cached httpx clients that might have been built with real base URLs.
    from dominion.workers import llm as _llm

    if hasattr(_llm._openai_compatible_client, "cache_clear"):
        _llm._openai_compatible_client.cache_clear()
    if hasattr(_llm, "_openai_client") and hasattr(_llm._openai_client, "cache_clear"):
        _llm._openai_client.cache_clear()


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
        # unreachable DB fails loudly instead of producing a falsely-green run. Only affirmative
        # values arm the gate — a bare truthy check treats "0"/"false" as ON, so "1"/"true"/"yes"/
        # "on" (case-insensitive) require the DB while ""/"0"/"false"/"no"/"off" fall through to skip.
        require_db = os.environ.get("DOMINION_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}
        if require_db:
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


@pytest.fixture
async def app_client(db_factory):
    """Inbound ASGI test harness (HTTP-HARNESS).

    A real httpx client that drives the FastAPI app through its ASGI interface, so a test exercises
    everything that calling a router coroutine directly never touches: URL routing, `Depends`
    injection, request-body validation (422s), the CORS middleware, exception handling, and
    response-model serialization. The app's DB dependency (`deps.db_session`, behind every router's
    `SessionDep`) is rebound to the per-test `db_factory`, so requests and the test share one
    truncated test database. The app's lifespan is booted against test config too, so startup wiring
    (settings overrides, drain resume, the boot integrity probe, the sweeper) runs under test.

    httpx's ASGITransport does NOT run the ASGI lifespan, and `asgi-lifespan` is not a dependency, so
    the lifespan is driven explicitly through Starlette's own `lifespan_context` — the exact async
    context manager `FastAPI(lifespan=...)` installs. Entering this fixture boots the app; leaving it
    runs shutdown (which cancels the sweeper task the lifespan started).
    """
    import httpx

    from dominion.api.deps import db_session
    from dominion.api.main import app

    async def _override_db_session():
        # Mirror deps.db_session exactly (roll back on error; NO implicit commit — mutating handlers
        # commit explicitly) but bind to the per-test factory instead of the app's global one.
        async with db_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[db_session] = _override_db_session
    try:
        # lifespan_context(app) is the async CM FastAPI built from main.lifespan; entering it runs the
        # real startup path against the test DB (SessionFactory is already bound to the test URL).
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
    finally:
        app.dependency_overrides.pop(db_session, None)
