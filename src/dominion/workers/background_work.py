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

# Same single-flight guarantee for the repair-task drain (a separate lock: repairs feed the job
# queue, so the two drains chain rather than compete).
_repair_drain_lock = asyncio.Lock()

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


def repair_drain_locked() -> bool:
    return _repair_drain_lock.locked()


async def drain_queued_repair_tasks() -> None:
    """Apply queued repair tasks one at a time until none are left, then drain the job queue once
    (the applies queue revision Jobs that nothing else here would draft).

    Deliberate autonomy change (DESIGN §5): already-triaged, bounded repair work executes without a
    per-task click. The human keeps the same controls as drafting — the queue pause switch stops the
    loop between tasks, requires_human_approval tasks are NEVER claimed (they wait for the explicit
    Approve & apply), and every application stays verifiable/rejectable/rollbackable. One fresh
    session per task so a poison task can't poison the batch; on failure the task parks
    WAITING_FOR_HUMAN with a repair_drain_error event, so the row leaves QUEUED and the loop always
    advances. Imported lazily so the API process only loads the production stack when it repairs."""
    if _repair_drain_lock.locked():
        return
    async with _repair_drain_lock:
        from sqlalchemy import false, select

        from dominion.shared.db import SessionFactory
        from dominion.shared.enums import RepairTaskStatus
        from dominion.shared.models import ProductionRun, RepairTask
        from dominion.workers import production, production_support

        while True:
            if _queue_paused:
                log.info("repair.drain_paused", note="queue paused by human; drain stops between tasks")
                break
            async with SessionFactory() as session:
                task = (
                    await session.execute(
                        select(RepairTask)
                        .where(
                            RepairTask.status == RepairTaskStatus.QUEUED,
                            RepairTask.requires_human_approval == false(),
                        )
                        .order_by(RepairTask.created_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if task is None:
                    await session.commit()
                    break
                task_id = task.id
                try:
                    await production.apply_repair_task(session, task_id, autonomous=True)
                    await session.commit()
                    log.info("repair.drain_applied", task=str(task_id))
                except Exception as exc:  # noqa: BLE001 — park + advance; one bad task must not strand the rest
                    await session.rollback()
                    log.error("repair.drain_error", task=str(task_id), error=str(exc))
                    parked = await session.get(RepairTask, task_id)
                    if parked is not None:
                        parked.status = RepairTaskStatus.WAITING_FOR_HUMAN
                        run = await session.get(ProductionRun, parked.production_run_id)
                        if run is not None:
                            await production_support.record_event(
                                session,
                                run_id=run.id,
                                event_type="repair_drain_error",
                                stage="repair_execution",
                                message=f"Auto-apply failed; task parked for human review: {exc}",
                                payload={"repair_task_id": str(task_id)},
                            )
                        await session.commit()
        await drain_queued_jobs()


def set_derive_result(chapter_id: str, counts: dict[str, Any]) -> None:
    _derive_results[chapter_id] = counts


def get_derive_result(chapter_id: str) -> dict[str, Any] | None:
    return _derive_results.get(chapter_id)


def pop_derive_result(chapter_id: str) -> dict[str, Any] | None:
    return _derive_results.pop(chapter_id, None)
