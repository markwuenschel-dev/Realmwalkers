"""Unit tests for the in-process drafting-phase registry (no database)."""

from __future__ import annotations

import uuid

import pytest

from dominion.workers import progress


@pytest.fixture
def job_id() -> str:
    jid = f"test-progress-{uuid.uuid4()}"
    yield jid
    progress.clear(jid)


@pytest.fixture(autouse=True)
def _restore_last_cache():
    saved = progress._last_cache
    yield
    progress._last_cache = saved


async def test_set_phase_get_returns_phase_and_elapsed(job_id: str, monkeypatch: pytest.MonkeyPatch):
    t = {"now": 1_000.0}
    monkeypatch.setattr(progress.time, "time", lambda: t["now"])

    progress.set_phase(job_id, "drafting prose")
    t["now"] = 1_007.0

    phase, elapsed = progress.get(job_id)
    assert phase == "drafting prose"
    assert elapsed == 7


async def test_phase_update_preserves_start_time(job_id: str, monkeypatch: pytest.MonkeyPatch):
    t = {"now": 2_000.0}
    monkeypatch.setattr(progress.time, "time", lambda: t["now"])

    progress.set_phase(job_id, "drafting")
    t["now"] = 2_010.0
    progress.set_phase(job_id, "enriching · combat")
    t["now"] = 2_025.0

    phase, elapsed = progress.get(job_id)
    assert phase == "enriching · combat"
    assert elapsed == 25


async def test_clear_removes_phase_and_per_job_cache_stats(job_id: str):
    progress.set_phase(job_id, "reviewing")
    progress.set_cache_stats(
        job_id,
        cache_hit_ratio=0.5,
        total_cache_read_tokens=100,
        total_cache_creation_tokens=50,
        cache_tokens_saved=90,
    )

    progress.clear(job_id)

    assert progress.get(job_id) == (None, None)
    assert progress.get_cache_stats(job_id) is None


async def test_set_cache_stats_get_cache_stats(job_id: str):
    progress.set_cache_stats(
        job_id,
        cache_hit_ratio=0.6,
        total_cache_read_tokens=500,
        total_cache_creation_tokens=100,
        cache_tokens_saved=450,
    )

    stats = progress.get_cache_stats(job_id)
    assert stats == {
        "cache_hit_ratio": 0.6,
        "total_cache_read_tokens": 500,
        "total_cache_creation_tokens": 100,
        "cache_tokens_saved": 450,
    }


async def test_get_last_cache_persists_after_clear(job_id: str):
    progress.set_cache_stats(
        job_id,
        cache_hit_ratio=0.8,
        total_cache_read_tokens=200,
        total_cache_creation_tokens=40,
        cache_tokens_saved=180,
    )

    progress.clear(job_id)

    assert progress.get_cache_stats(job_id) is None
    last = progress.get_last_cache()
    assert last is not None
    assert last["cache_hit_ratio"] == 0.8
    assert last["total_cache_read_tokens"] == 200
    assert last["total_cache_creation_tokens"] == 40
    assert last["cache_tokens_saved"] == 180


async def test_get_returns_none_for_untracked_job():
    unknown = f"test-progress-{uuid.uuid4()}"
    assert progress.get(unknown) == (None, None)


async def test_none_job_id_is_noop():
    progress.set_phase(None, "drafting")
    progress.set_cache_stats(
        None,
        cache_hit_ratio=1.0,
        total_cache_read_tokens=1,
        total_cache_creation_tokens=0,
    )
    progress.clear(None)
    assert progress.get(None) == (None, None)
    assert progress.get_cache_stats(None) is None
