"""Tests for the Desk-facing endpoints: browser draft trigger, world ledger, threads.

The draft-trigger drain logic is exercised without a database (run_once is mocked). The world +
threads endpoints call the router functions directly against real Postgres (like tests/test_gate1.py)
and skip automatically when Postgres isn't reachable (see tests/conftest.py).
"""
from __future__ import annotations

from fastapi import BackgroundTasks

from dominion.api.routers import jobs as jobs_router
from dominion.api.routers import threads as threads_router
from dominion.api.routers import world as world_router
from dominion.shared.enums import JobStatus
from dominion.shared.models import (
    Book,
    CanonEntity,
    Chapter,
    CharacterState,
    Job,
    Run,
)
from dominion.shared.schemas import ThreadBeatIn, ThreadIn, ThreadUpdateIn

# --- draft trigger (no DB) ------------------------------------------------------------------------

async def test_drain_runs_until_queue_empty(monkeypatch):
    """_drain keeps drafting until run_once reports the queue is empty (returns False)."""
    calls = {"n": 0}

    async def fake_run_once():
        calls["n"] += 1
        return calls["n"] < 3  # two drafted, then empty

    monkeypatch.setattr("dominion.workers.worker.run_once", fake_run_once)
    await jobs_router._drain()
    assert calls["n"] == 3


async def test_drain_keeps_going_after_a_failed_job(monkeypatch):
    """A job that raises is logged + (in run_once) marked FAILED; the drain must not stop."""
    calls = {"n": 0}

    async def flaky_run_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scene blew up")
        return calls["n"] < 3

    monkeypatch.setattr("dominion.workers.worker.run_once", flaky_run_once)
    await jobs_router._drain()
    assert calls["n"] == 3  # error didn't strand the rest


async def test_drain_is_single_flight(monkeypatch):
    """A second drain while one holds the lock is a no-op (no concurrent LLM storms)."""
    called = {"v": False}

    async def fake_run_once():
        called["v"] = True
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", fake_run_once)
    await jobs_router._drain_lock.acquire()
    try:
        await jobs_router._drain()
    finally:
        jobs_router._drain_lock.release()
    assert called["v"] is False


# --- draft trigger + status (DB) ------------------------------------------------------------------

async def _seed_queued_jobs(s, n: int) -> None:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    run = Run(book_id=book.id, scope_json={}, gate_mode="pause_each", token_budget=1000)
    s.add(run)
    await s.flush()
    for scene_no in range(1, n + 1):
        s.add(Job(
            run_id=run.id, kind="draft", chapter_no=1, scene_no=scene_no,
            token_budget=1000, status=JobStatus.QUEUED,
        ))
    await s.flush()


async def test_status_reports_queue_depth(db_factory):
    async with db_factory() as s:
        await _seed_queued_jobs(s, 2)
        out = await jobs_router.status(s)
        assert out.queued == 2 and out.failed == 0 and out.active_scene is None


async def test_draft_next_schedules_a_background_drain(db_factory):
    async with db_factory() as s:
        await _seed_queued_jobs(s, 1)
        bg = BackgroundTasks()
        out = await jobs_router.draft_next(bg, s)
        assert out.queued == 1 and out.scheduled is True and out.running is True
        assert len(bg.tasks) == 1  # the drain is scheduled, not run inline


async def test_draft_next_noop_when_queue_empty(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        bg = BackgroundTasks()
        out = await jobs_router.draft_next(bg, s)
        assert out.queued == 0 and out.scheduled is False
        assert len(bg.tasks) == 0


# --- world ledger (DB) ----------------------------------------------------------------------------

async def test_characters_merge_stats_canon_and_pov_flag(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        s.add(Chapter(book_id=book.id, chapter_no=1, pov="Soren"))
        s.add(CharacterState(book_id=book.id, character="Soren", stats_json={"level": 15}))
        s.add(CharacterState(book_id=book.id, character="Lyra", stats_json={"status": "sealed"}))
        s.add(CanonEntity(book_id=book.id, kind="character", name="Soren", body="An ascendant."))
        await s.flush()

        chars = await world_router.list_characters(book.id, s)
        soren = next(c for c in chars if c.character == "Soren")
        lyra = next(c for c in chars if c.character == "Lyra")
        assert soren.is_pov and soren.stats["level"] == 15 and soren.body == "An ascendant."
        assert not lyra.is_pov and lyra.body is None


async def test_canon_lists_and_filters_by_kind(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        s.add(CanonEntity(book_id=book.id, kind="character", name="Soren", body="a"))
        s.add(CanonEntity(book_id=book.id, kind="location", name="The Warded Door", body="b"))
        await s.flush()

        assert {e.kind for e in await world_router.list_canon(book.id, s)} == {"character", "location"}
        locs = await world_router.list_canon(book.id, s, kind="location")
        assert len(locs) == 1 and locs[0].name == "The Warded Door"


# --- threads (DB) ---------------------------------------------------------------------------------

async def test_thread_crud_roundtrip(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()

        created = await threads_router.create_thread(
            book.id, ThreadIn(name="Soren ⇄ Lyra", kind="relationship", state="sealed", note="n"), s
        )
        assert created.name == "Soren ⇄ Lyra" and created.beats == []

        with_beat = await threads_router.add_thread_beat(
            created.id, ThreadBeatIn(scene_no=5, label="threadbound"), s
        )
        assert [b.scene_no for b in with_beat.beats] == [5]

        updated = await threads_router.update_thread(created.id, ThreadUpdateIn(state="active"), s)
        assert updated.state == "active" and updated.name == "Soren ⇄ Lyra"  # untouched field kept

        listed = await threads_router.list_threads(book.id, s)
        assert len(listed) == 1 and listed[0].beats[0].label == "threadbound"

        await threads_router.delete_thread(created.id, s)
        assert await threads_router.list_threads(book.id, s) == []
