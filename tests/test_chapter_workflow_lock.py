"""First coverage of the per-chapter workflow lock wrapper (ADR-0028 Q15, shared/chapter_lock.py).

Direct-DB (needs Postgres; skips locally, runs under `just test` / CI). Exercises the four properties the
`run_under_chapter_workflow` contract promises: it commits and returns on success and releases the lock;
it rolls back and re-raises on a body error; it fails closed with `ChapterWorkflowBusy` under contention
(the lock is acquired BEFORE the body runs, so a busy chapter never enters the body); and locks on
different chapters are independent.
"""

from __future__ import annotations

import uuid

import pytest

from dominion.shared.chapter_lock import (
    ChapterWorkflowBusy,
    acquire_chapter_workflow_lock,
    run_under_chapter_workflow,
)
from dominion.shared.models import Book, Chapter


async def _seed_chapter(s) -> Chapter:
    book = Book(title="Chapter Lock Test")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return ch


async def test_happy_path_commits_returns_and_releases(db_factory):
    """Body's write commits, the body's value is returned, and the lock releases so a second run on the
    same session acquires cleanly and also commits."""
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await s.commit()
        ch_id = ch.id

        async def body():
            loaded = await s.get(Chapter, ch_id)
            loaded.title = "written-by-body"
            return "sentinel"

        result = await run_under_chapter_workflow(s, ch_id, body)
        assert result == "sentinel"

        async def body_again():
            loaded = await s.get(Chapter, ch_id)
            loaded.status = "planned-again"
            return 42

        # Lock released at the first commit → the second run acquires without hanging.
        assert await run_under_chapter_workflow(s, ch_id, body_again) == 42

    async with db_factory() as s2:
        got = await s2.get(Chapter, ch_id)
        assert got.title == "written-by-body"  # first body committed
        assert got.status == "planned-again"  # second body committed


async def test_body_error_rolls_back_and_propagates(db_factory):
    """A body exception rolls the whole unit back (no partial write persists) and re-raises unchanged."""

    class Boom(Exception):
        pass

    async with db_factory() as s:
        ch = await _seed_chapter(s)
        ch.title = "original"
        await s.commit()
        ch_id = ch.id

        async def body():
            loaded = await s.get(Chapter, ch_id)
            loaded.title = "should-not-persist"
            await s.flush()  # the write reaches the DB inside the tx before the failure
            raise Boom()

        with pytest.raises(Boom):
            await run_under_chapter_workflow(s, ch_id, body)

    async with db_factory() as s2:
        got = await s2.get(Chapter, ch_id)
        assert got.title == "original"  # rolled back — the body's write did not persist


async def test_contention_raises_chapter_workflow_busy(db_factory):
    """A second session holding the chapter lock forces the wrapper's acquire to time out → the body
    never runs and ChapterWorkflowBusy is raised."""
    chapter_id = uuid.uuid4()
    async with db_factory() as holder, db_factory() as contender:
        # Holder takes the lock and does NOT commit, so it stays held for the whole test.
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        ran = {"body": False}

        async def body():
            ran["body"] = True
            return "unreachable"

        with pytest.raises(ChapterWorkflowBusy):
            await run_under_chapter_workflow(contender, chapter_id, body, timeout_ms=250)
        assert ran["body"] is False  # failed at acquire, before the body

        await holder.rollback()  # release the held advisory lock


async def test_different_chapters_do_not_block(db_factory):
    """The lock is per-chapter: a hold on chapter A must not delay a run on chapter B (a short timeout
    would trip if they collided)."""
    chapter_a = uuid.uuid4()
    chapter_b = uuid.uuid4()
    async with db_factory() as holder, db_factory() as other:
        await acquire_chapter_workflow_lock(holder, chapter_a, timeout_ms=None)

        async def body():
            return "b-ran"

        # Different key → acquires immediately despite A being held; the 250ms ceiling never fires.
        assert await run_under_chapter_workflow(other, chapter_b, body, timeout_ms=250) == "b-ran"

        await holder.rollback()
