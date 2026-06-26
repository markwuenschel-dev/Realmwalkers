"""In-process drafting-phase registry — runtime exhaust, never persisted (DESIGN §3).

The browser-driven drain (`jobs._drain` -> `worker.run_once` -> `pipeline.generate_one_scene`) runs
inside the API process, so a module-level dict is enough to surface *what* a job is doing right now
("drafting prose", "enriching · combat", "reviewing") to `GET /jobs/status` — with no DB write on the
hot path and no schema column for what is pure runtime state.

Contract: best-effort and fully defensive. A progress call must NEVER break drafting, so every public
function swallows its own errors; a missing entry just means the UI shows a generic "drafting…". The
terminal worker (`dominion-worker`) is a separate process, so its phases aren't visible here — that's
fine, since the Desk is the path a human actually watches.
"""
from __future__ import annotations

import time
from typing import TypedDict

# job_id (str) -> (phase, started_at_epoch_seconds). Cleared when the job finishes.
_phases: dict[str, tuple[str, float]] = {}

# job_id -> cache stats while the job is still RUNNING (cleared with phases on job completion).
_cache_stats: dict[str, "CacheStats"] = {}

# The most recent completed scene's cache stats — persists until the next job starts so the Desk
# can display it during the idle window after drafting completes.
_last_cache: "CacheStats | None" = None


class CacheStats(TypedDict):
    cache_hit_ratio: float
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    cache_tokens_saved: int


def set_phase(job_id: str | None, phase: str) -> None:
    """Record the current phase for a job, preserving the original start time across phase changes."""
    if not job_id:
        return
    try:
        existing = _phases.get(job_id)
        started = existing[1] if existing else time.time()
        _phases[job_id] = (phase, started)
    except Exception:  # noqa: BLE001 — progress reporting must never break the draft
        pass


def get(job_id: str | None) -> tuple[str | None, int | None]:
    """Current (phase, elapsed_seconds) for a job, or (None, None) if untracked."""
    if not job_id:
        return None, None
    try:
        entry = _phases.get(job_id)
        if not entry:
            return None, None
        phase, started = entry
        return phase, int(time.time() - started)
    except Exception:  # noqa: BLE001
        return None, None


def set_cache_stats(
    job_id: str | None,
    *,
    cache_hit_ratio: float,
    total_cache_read_tokens: int,
    total_cache_creation_tokens: int,
    cache_tokens_saved: int = 0,
) -> None:
    """Store cache stats for a running job and update the persistent last-scene snapshot."""
    global _last_cache
    if not job_id:
        return
    try:
        stats = CacheStats(
            cache_hit_ratio=cache_hit_ratio,
            total_cache_read_tokens=total_cache_read_tokens,
            total_cache_creation_tokens=total_cache_creation_tokens,
            cache_tokens_saved=cache_tokens_saved,
        )
        _cache_stats[job_id] = stats
        _last_cache = stats          # persists past job completion so the Desk sees it while idle
    except Exception:  # noqa: BLE001
        pass


def get_cache_stats(job_id: str | None) -> "CacheStats | None":
    """Cache stats for a currently-running job, or None if not yet recorded."""
    if not job_id:
        return None
    try:
        return _cache_stats.get(job_id)
    except Exception:  # noqa: BLE001
        return None


def get_last_cache() -> "CacheStats | None":
    """Cache stats for the most recently completed scene — available during idle windows."""
    return _last_cache


def clear(job_id: str | None) -> None:
    """Forget a job once it's done/failed, so the registry doesn't grow without bound."""
    if not job_id:
        return
    try:
        _phases.pop(job_id, None)
        _cache_stats.pop(job_id, None)
        # _last_cache intentionally not cleared — it stays until the next scene overwrites it
    except Exception:  # noqa: BLE001
        pass
