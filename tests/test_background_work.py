"""Unit tests for dominion.workers.background_work."""
from __future__ import annotations

import asyncio

from dominion.workers import background_work as bw
from dominion.workers import progress


def test_try_begin_single_flight():
    key = "test:single-flight"
    try:
        assert bw.try_begin(key) is True
        assert bw.is_running(key)
        assert bw.try_begin(key) is False
    finally:
        bw.finish(key)


def test_finish_clears_running_and_progress_phase():
    key = "test:finish"
    bw.begin_with_phase(key, "authoring")
    assert bw.is_running(key)
    phase, _elapsed = progress.get(key)
    assert phase == "authoring"

    bw.finish(key)
    assert not bw.is_running(key)
    assert progress.get(key) == (None, None)


async def test_drain_locked_reflects_lock_state(monkeypatch):
    assert not bw.drain_locked()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_run_once() -> bool:
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", slow_run_once)

    task = asyncio.create_task(bw.drain_queued_jobs())
    await entered.wait()
    assert bw.drain_locked()

    release.set()
    await task
    assert not bw.drain_locked()


def test_derive_result_get_set_pop():
    chapter_id = "chapter-derive-cache"
    counts = {"created": 2, "updated": 1, "blocked": 0, "stale": 0}

    assert bw.get_derive_result(chapter_id) is None
    bw.set_derive_result(chapter_id, counts)
    assert bw.get_derive_result(chapter_id) == counts

    popped = bw.pop_derive_result(chapter_id)
    assert popped == counts
    assert bw.get_derive_result(chapter_id) is None
    assert bw.pop_derive_result(chapter_id) is None
