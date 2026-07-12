"""Behavioral coverage for the book-ownership invariant (ADR 0027): single-key scope, the execution-seam
claim guard, and the migration reconcile (backfill / conflict rejection / quarantine / promotion).

The fresh test DB runs `apply_lightweight_migrations` -> `reconcile_job_ownership`, which (with no rows)
promotes `jobs.book_id` to physical NOT NULL. Tests that need legacy NULL-book rows temporarily drop the
constraints ("un-promote"), then restore them in a finally by deleting all jobs and reconciling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from dominion.shared.enums import JobKind, JobStatus, RunStatus
from dominion.shared.job_integrity import reconcile_job_ownership
from dominion.shared.job_policy import scope_jobs_to_book
from dominion.shared.models import Book, Chapter, Job, Run
from dominion.workers.worker import claim_one_job


async def _book(s, title: str) -> Book:
    b = Book(title=title)
    s.add(b)
    await s.flush()
    return b


async def _chapter(s, book: Book) -> Chapter:
    c = Chapter(book_id=book.id, chapter_no=1, pov="X")
    s.add(c)
    await s.flush()
    return c


async def _run(s, book: Book) -> Run:
    r = Run(book_id=book.id, scope_json={}, gate_mode="pause_each", token_budget=40_000, status=RunStatus.ACTIVE)
    s.add(r)
    await s.flush()
    return r


async def _unpromote(s) -> None:
    """Drop the NOT NULL column + the NOT VALID CHECK so legacy NULL-book rows can be inserted."""
    for ddl in (
        "ALTER TABLE jobs ALTER COLUMN book_id DROP NOT NULL",
        "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_book_id_not_null",
        "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_book_id_fkey",
    ):
        await s.execute(text(ddl))
    await s.flush()


async def _restore(s) -> None:
    """Return the shared test schema to the promoted invariant state for the next test."""
    await s.execute(text("DELETE FROM jobs"))
    await reconcile_job_ownership(await s.connection())
    await s.commit()


async def test_scope_is_single_key_and_finds_run_less_revision(db_factory):
    """A run-less revision (book_id set, run_id NULL — the original stall) scopes to its book; another
    book's jobs never leak in."""
    async with db_factory() as s:
        a, b = await _book(s, "A"), await _book(s, "B")
        ca, cb = await _chapter(s, a), await _chapter(s, b)
        # run-less revision on book A, a draft on book A, and a job on book B
        s.add(
            Job(
                book_id=a.id,
                run_id=None,
                kind=JobKind.REVISE_FULL,
                chapter_id=ca.id,
                status=JobStatus.QUEUED,
                token_budget=1,
            )
        )
        s.add(Job(book_id=a.id, kind=JobKind.DRAFT, chapter_id=ca.id, status=JobStatus.QUEUED, token_budget=1))
        s.add(Job(book_id=b.id, kind=JobKind.DRAFT, chapter_id=cb.id, status=JobStatus.QUEUED, token_budget=1))
        await s.flush()
        rows = (await s.execute(scope_jobs_to_book(select(Job.book_id), a.id))).scalars().all()
        assert len(rows) == 2 and set(rows) == {a.id}


async def test_constraint_blocks_a_new_ownerless_job(db_factory):
    """The promoted invariant: inserting a book-less job fails at the database (no new orphan can be born)."""
    from sqlalchemy.exc import IntegrityError

    async with db_factory() as s:
        s.add(Job(book_id=None, kind=JobKind.DRAFT, status=JobStatus.QUEUED, token_budget=1))
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_reconcile_backfills_rejects_conflicts_quarantines_and_holds_promotion(db_factory):
    async with db_factory() as s:
        try:
            a, b = await _book(s, "A"), await _book(s, "B")
            ca = await _chapter(s, a)
            run_b = await _run(s, b)
            await _unpromote(s)
            # (1) chapter-resolvable, no conflicting run -> backfilled from chapter
            by_chapter = Job(book_id=None, kind=JobKind.DRAFT, chapter_id=ca.id, status=JobStatus.DONE, token_budget=1)
            # (2) run-only (no chapter) -> backfilled from run
            by_run = Job(book_id=None, run_id=run_b.id, kind=JobKind.DRAFT, status=JobStatus.DONE, token_budget=1)
            # (3) conflict: chapter says A, run says B -> rejected, stays NULL (terminal -> unresolved hold)
            conflict = Job(
                book_id=None,
                run_id=run_b.id,
                kind=JobKind.DRAFT,
                chapter_id=ca.id,
                status=JobStatus.DONE,
                token_budget=1,
            )
            # (4) live, no links -> quarantined
            ownerless_live = Job(book_id=None, kind=JobKind.DRAFT, status=JobStatus.QUEUED, token_budget=1)
            s.add_all([by_chapter, by_run, conflict, ownerless_live])
            await s.flush()

            report = await reconcile_job_ownership(await s.connection())

            for j in (by_chapter, by_run, conflict, ownerless_live):
                await s.refresh(j)
            assert by_chapter.book_id == a.id
            assert by_run.book_id == b.id
            assert conflict.book_id is None  # conflict rejected, not guessed
            assert ownerless_live.status == JobStatus.QUARANTINED
            assert ownerless_live.last_error == "INTEGRITY_OWNERLESS"
            assert ownerless_live.book_id is None
            assert report.backfilled_from_chapter >= 1
            assert report.backfilled_from_run >= 1
            assert report.newly_quarantined >= 1
            assert report.conflicts >= 1
            assert report.promoted is False  # unresolved holds block physical NOT NULL
            assert report.hold_count >= 2  # the conflict row + the quarantined row
        finally:
            await _restore(s)


async def test_claim_skips_ownerless_and_does_not_head_of_line_block(db_factory):
    async with db_factory() as s:
        try:
            a = await _book(s, "A")
            ca = await _chapter(s, a)
            await _unpromote(s)
            old = datetime.now(UTC) - timedelta(hours=1)
            newer = datetime.now(UTC)
            ownerless = Job(book_id=None, kind=JobKind.DRAFT, status=JobStatus.QUEUED, token_budget=1, created_at=old)
            valid = Job(
                book_id=a.id,
                kind=JobKind.DRAFT,
                chapter_id=ca.id,
                status=JobStatus.QUEUED,
                token_budget=1,
                created_at=newer,
            )
            s.add_all([ownerless, valid])
            await s.flush()

            claimed = await claim_one_job(s)
            # The older ownerless job is excluded from the candidate set, so the newer valid job is claimed.
            assert claimed is not None and claimed.id == valid.id
            await s.refresh(ownerless)
            assert ownerless.status == JobStatus.QUEUED  # never claimed/transitioned
        finally:
            await _restore(s)
