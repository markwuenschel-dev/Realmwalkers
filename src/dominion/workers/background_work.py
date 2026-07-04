"""Shared single-flight, drain, and result-cache primitives for API background work.

Routers schedule long-running LLM pipelines in FastAPI BackgroundTasks. This module centralizes the
in-process registries those endpoints share: at-most-one run per key, optimistic progress phases, a
global job-queue drain lock, and ephemeral derive-result counts for post-run polling.
"""

from __future__ import annotations

import asyncio
import collections.abc
from typing import TYPE_CHECKING, Any

import structlog

from dominion.workers import progress

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

log = structlog.get_logger()

# Keys whose background work is running. The API event loop is single-threaded and we never await
# between checking and mutating this set, so a plain set is race-free.
_inflight: set[str] = set()

# Last finished derive counts per chapter id, so a poll after completion can report what happened.
_derive_results: dict[str, dict[str, Any]] = {}

# At most one drain loop per process. FastAPI background tasks share the API event loop.
_drain_lock = asyncio.Lock()

# Queue pause (Desk Control Round): a human-set switch that stops the drain from claiming new jobs
# (the in-flight scene always finishes). Persisted as a ModelOverride row keyed "queue_paused" —
# inert to the model loader (apply_model_overrides filters on ROLE_KEYS) and survives redeploys
# without a migration. The module global is the fast path; load_queue_paused refreshes it from the
# DB so terminal workers and fresh containers agree.
QUEUE_PAUSED_KEY = "queue_paused"
_queue_paused = False


def queue_paused() -> bool:
    return _queue_paused


async def set_queue_paused(session: Any, paused: bool) -> None:
    """Flip the switch: update the process-global AND upsert the persisted row."""
    global _queue_paused
    from dominion.shared.models import ModelOverride

    _queue_paused = paused
    row = await session.get(ModelOverride, QUEUE_PAUSED_KEY)
    if row is None:
        session.add(ModelOverride(setting_name=QUEUE_PAUSED_KEY, model="1" if paused else "0"))
    else:
        row.model = "1" if paused else "0"


async def load_queue_paused(session: Any) -> bool:
    """Read the persisted switch into the process-global (boot, and each worker poll)."""
    global _queue_paused
    from dominion.shared.models import ModelOverride

    row = await session.get(ModelOverride, QUEUE_PAUSED_KEY)
    _queue_paused = row is not None and row.model == "1"
    return _queue_paused


def try_begin(key: str) -> bool:
    """Add key if absent. Return True when the caller should start work."""
    if key in _inflight:
        return False
    _inflight.add(key)
    return True


def finish(key: str) -> None:
    """Release the single-flight slot and clear progress for key."""
    _inflight.discard(key)
    progress.clear(key)


def is_running(key: str) -> bool:
    return key in _inflight


def begin_with_phase(key: str, phase: str) -> bool:
    """try_begin plus optimistic progress.set_phase so the first poll already has a phase."""
    if not try_begin(key):
        return False
    progress.set_phase(key, phase)
    return True


def schedule(
    background: BackgroundTasks,
    key: str,
    phase: str,
    coro_fn: collections.abc.Callable[[], collections.abc.Coroutine[object, object, None]],
) -> bool:
    """begin_with_phase and, on success, background.add_task with finish in finally."""
    if not begin_with_phase(key, phase):
        return False

    async def _wrapped() -> None:
        try:
            await coro_fn()
        finally:
            finish(key)

    background.add_task(_wrapped)
    return True


def drain_locked() -> bool:
    return _drain_lock.locked()


async def drain_queued_jobs() -> None:
    """Draft queued jobs one at a time until the queue empties.

    run_once already persists a failed job as FAILED and logs it; we swallow + keep draining so one
    bad scene doesn't strand the rest of the chapter. Imported lazily so the API process only loads
    the LLM stack when it actually drafts (mirrors legacy/enqueue)."""
    if _drain_lock.locked():
        return
    async with _drain_lock:
        from dominion.workers.worker import run_once

        while True:
            if _queue_paused:
                log.info("draft.drain_paused", note="queue paused by human; drain stops between jobs")
                break
            try:
                did = await run_once()
            except Exception as exc:  # noqa: BLE001 — already marked FAILED + logged in run_once
                log.error("draft.drain_error", error=str(exc))
                did = True  # a FAILED job is no longer QUEUED, so the loop advances
            if not did:
                break


def set_derive_result(chapter_id: str, counts: dict[str, Any]) -> None:
    _derive_results[chapter_id] = counts


def get_derive_result(chapter_id: str) -> dict[str, Any] | None:
    return _derive_results.get(chapter_id)


def pop_derive_result(chapter_id: str) -> dict[str, Any] | None:
    return _derive_results.pop(chapter_id, None)
