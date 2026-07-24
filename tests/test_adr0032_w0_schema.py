"""ADR-0032 W0 — guarded schema foundation oracles.

W0 installs the two structural primitives every later wave depends on (ADR-0032 §D2/§D3/§D13):

  * `import_adoptions.liveness_basis` — the retention-authority axis (`request_bound |
    operator_independent`), added with a TEMPORARY db default that also backfills existing rows.
  * `uq_import_adoptions_active_chapter` — a partial-unique index making "≤1 active adoption per
    chapter" a DATABASE structural guarantee, guarded by a duplicate PREFLIGHT that fails CLOSED
    (deletes nothing, picks no winner) rather than let the index silently discard data.

Three oracles, mirroring the migration suite's static + DB layers:

  1. index rejects a 2nd active adoption per chapter (and tolerates terminal states / other chapters);
  2. the preflight fails closed on seeded dirty data with the conflicting identities visible, and the
     index is NOT built (proving the preflight runs BEFORE the index DDL);
  3. the migration backfills pre-existing rows to `operator_independent` on clean data and builds the
     index.

Oracles 2 and 3 need a DB where `uq_import_adoptions_active_chapter` does NOT yet exist — impossible in
the shared test DB (its schema-setup already built the index). Because the index lives only in
`migrations._EXTRA_DDL` (never as an ORM `Index`), a `create_all`-only throwaway database is naturally
"pre-index"; `isolated_db_engine` provisions and drops one.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dominion.shared.enums import LivenessBasis
from dominion.shared.migrations import DuplicateActiveAdoptionError, apply_lightweight_migrations
from dominion.shared.models import Base, Book, Chapter, ImportAdoption

_INDEX = "uq_import_adoptions_active_chapter"


# --------------------------------------------------------------------------- helpers / fixtures


async def _seed_book_chapter(s, *, chapter_no: int = 1) -> tuple[Book, Chapter]:
    """The minimal FK context an ImportAdoption needs: a book and one chapter."""
    book = Book(title="ADR-0032 W0")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=chapter_no, pov="Marcus")
    s.add(ch)
    await s.flush()
    return book, ch


def _active(book_id, chapter_id, *, source_fingerprint, status, liveness_basis="operator_independent"):
    """An ImportAdoption seed. `liveness_basis` is now a required NOT-NULL column (W1), so tests must
    supply it explicitly; this helper defaults the value tests don't care about."""
    return ImportAdoption(
        book_id=book_id,
        chapter_id=chapter_id,
        source_fingerprint=source_fingerprint,
        status=status,
        liveness_basis=liveness_basis,
    )


def _require_db_or_skip(exc: Exception) -> None:
    """Same fail/skip policy as conftest.db_factory: fail loudly under DOMINION_REQUIRE_DB, else skip."""
    require = os.environ.get("DOMINION_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}
    msg = f"Postgres not reachable for W0 isolated-DB test ({type(exc).__name__}): {exc}"
    if require:
        pytest.fail(msg, pytrace=False)
    pytest.skip(msg)


def _base_url() -> str:
    """The test connection URL minus its database name (conftest set DOMINION_DATABASE_URL to the test DB)."""
    app_url = os.environ["DOMINION_DATABASE_URL"]
    base, _, _dbname = app_url.rpartition("/")
    return base


@pytest.fixture
async def isolated_db_engine():
    """A brand-new, empty Postgres database brought up with `create_all` ONLY — so the `_EXTRA_DDL`
    indexes (including `uq_import_adoptions_active_chapter`) are ABSENT. This models the pre-index /
    mid-migration world the W0 preflight and backfill must handle. The database is dropped on teardown.
    """
    base = _base_url()
    maint_url = f"{base}/postgres"
    dbname = f"dominion_test_w0_{uuid.uuid4().hex[:12]}"

    maint = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
    try:
        async with maint.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    except Exception as exc:  # noqa: BLE001 — connect failure means Postgres is down; skip/fail per policy.
        await maint.dispose()
        _require_db_or_skip(exc)
    finally:
        await maint.dispose()

    engine = create_async_engine(f"{base}/{dbname}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
        maint2 = create_async_engine(maint_url, isolation_level="AUTOCOMMIT")
        try:
            async with maint2.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        finally:
            await maint2.dispose()


async def _index_exists(engine) -> bool:
    async with engine.connect() as conn:
        found = await conn.scalar(text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": _INDEX})
    return found == 1


# --------------------------------------------------------------------------- static (no DB)


def test_liveness_basis_enum_values():
    """The two permitted values W1 will CHECK-enforce; pinned here so a stray rename is caught early."""
    assert {b.value for b in LivenessBasis} == {"request_bound", "operator_independent"}


def test_liveness_basis_is_migration_added():
    """Forward-drift guard: the new model column MUST have an `ALTER TABLE ... ADD COLUMN` in
    migrations, or it boots green on a fresh create_all DB and throws UndefinedColumn against prod."""
    from tests.test_migration_forward_drift import _migration_added_columns

    assert ("import_adoptions", "liveness_basis") in _migration_added_columns()


# --------------------------------------------------------------------------- Oracle 1: the index


async def test_index_rejects_second_active_adoption(db_factory):
    """§D14: a 2nd active (awaiting_start/queued/running) adoption for the same chapter is rejected at
    the database — the "≤1 active adoption per chapter" invariant as a structural guarantee, not a race."""
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        s.add(_active(book.id, ch.id, source_fingerprint="fp1", status="queued"))
        await s.commit()

        s.add(_active(book.id, ch.id, source_fingerprint="fp2", status="running"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_index_allows_active_alongside_terminal(db_factory):
    """§D3: terminal states (contract_proposed/failed/invalidated/cancelled) fall OUTSIDE the partial
    predicate, so they never permanently block a later valid adoption — but a 2nd ACTIVE row still can't."""
    async with db_factory() as s:
        book, ch = await _seed_book_chapter(s)
        s.add(_active(book.id, ch.id, source_fingerprint="t1", status="cancelled"))
        s.add(_active(book.id, ch.id, source_fingerprint="t2", status="contract_proposed"))
        await s.commit()  # two terminal rows for one chapter — allowed

        s.add(_active(book.id, ch.id, source_fingerprint="a1", status="queued"))
        await s.commit()  # one active alongside the terminals — allowed

        s.add(_active(book.id, ch.id, source_fingerprint="a2", status="awaiting_start"))
        with pytest.raises(IntegrityError):
            await s.commit()  # a SECOND active — rejected
        await s.rollback()


async def test_index_allows_active_in_different_chapters(db_factory):
    """The invariant is PER chapter: two active adoptions in different chapters of the same book coexist."""
    async with db_factory() as s:
        book, ch1 = await _seed_book_chapter(s, chapter_no=1)
        ch2 = Chapter(book_id=book.id, chapter_no=2, pov="Marcus")
        s.add(ch2)
        await s.flush()
        s.add(_active(book.id, ch1.id, source_fingerprint="c1", status="queued"))
        s.add(_active(book.id, ch2.id, source_fingerprint="c2", status="running"))
        await s.commit()  # different chapters — both active, no collision


# --------------------------------------------------------------------------- Oracle 2: preflight fails closed


async def test_preflight_fails_closed_on_duplicate_active_adoptions(isolated_db_engine):
    """§D13/§D14: on a DB that ALREADY violates the invariant, the migration REFUSES to build the index —
    it fails closed with an operator report naming the chapter and each conflicting adoption's
    id/status/basis, deletes nothing, and never reaches the index DDL (which stays absent)."""
    engine = isolated_db_engine
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Two active adoptions in ONE chapter — allowed here only because the index does not exist yet.
    async with factory() as s:
        book, ch = await _seed_book_chapter(s)
        a1 = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            source_fingerprint="fp1",
            status="queued",
            liveness_basis="request_bound",
        )
        a2 = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            source_fingerprint="fp2",
            status="running",
            liveness_basis="operator_independent",
        )
        s.add_all([a1, a2])
        await s.commit()
        chapter_id, a1_id, a2_id = ch.id, a1.id, a2.id

    async with engine.begin() as conn:
        with pytest.raises(DuplicateActiveAdoptionError) as excinfo:
            await apply_lightweight_migrations(conn)

    report = str(excinfo.value)
    assert str(chapter_id) in report, "operator report must name the offending chapter_id"
    assert str(a1_id) in report and str(a2_id) in report, "both conflicting adoption ids must be visible"
    assert "request_bound" in report and "operator_independent" in report, "each row's basis must be visible"

    # The preflight ran BEFORE the index DDL and the aborted txn rolled back: no index was created.
    assert not await _index_exists(engine)

    # Fail-closed means NO data was touched — both rows survive unchanged.
    async with factory() as s2:
        assert await s2.get(ImportAdoption, a1_id) is not None
        assert await s2.get(ImportAdoption, a2_id) is not None


# --------------------------------------------------------------------------- Oracle 3: backfill + happy path


async def test_migration_backfills_existing_rows_and_builds_index(isolated_db_engine):
    """§D13: adding the column to a table with PRE-EXISTING rows backfills them all to
    `operator_independent` (conservative compatibility), and on clean data the guarded migration
    completes — the partial-unique index is built."""
    engine = isolated_db_engine
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # Seed two active adoptions in DIFFERENT chapters (no duplicate), then simulate a table that predates
    # the column by DROPPING it — the rows survive, minus that column, exactly like a pre-W0 prod table.
    async with factory() as s:
        book = Book(title="ADR-0032 W0 backfill")
        s.add(book)
        await s.flush()
        ch1 = Chapter(book_id=book.id, chapter_no=1, pov="A")
        ch2 = Chapter(book_id=book.id, chapter_no=2, pov="B")
        s.add_all([ch1, ch2])
        await s.flush()
        a1 = _active(book.id, ch1.id, source_fingerprint="fp1", status="queued")
        a2 = _active(book.id, ch2.id, source_fingerprint="fp2", status="awaiting_start")
        s.add_all([a1, a2])
        await s.commit()
        a1_id, a2_id = a1.id, a2.id

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE import_adoptions DROP COLUMN liveness_basis"))
        # Re-adds the column with the temp default (backfilling both pre-existing rows), passes the
        # preflight (distinct chapters), and builds the index.
        await apply_lightweight_migrations(conn)

    async with factory() as s2:
        got1 = await s2.get(ImportAdoption, a1_id)
        got2 = await s2.get(ImportAdoption, a2_id)
        assert got1.liveness_basis == "operator_independent"
        assert got2.liveness_basis == "operator_independent"

    assert await _index_exists(engine)
