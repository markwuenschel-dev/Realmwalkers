"""Autonomous self-repair sweeper — the background loop that drives stalled production runs forward.

The repair pipeline is otherwise reactive: structural triage produces `requires_human_approval` tasks
that the drain deliberately skips, and nothing auto-verifies after a revision drafts, so a run parks
until a human clicks. This loop closes that gap. Each tick it finds runs that have gone quiet,
re-triages them, auto-approves approval-gated repairs UP TO a configured authority ceiling (default
gates `human_required`; raise the ceiling to it for full autonomy), auto-verifies applied repairs
whose revisions have landed, and kicks the shared
drain. Every action it takes is written to the central Activity feed (source="sweeper") so the human
can see — and roll back — what autonomy did.

Guardrails, all honored every tick:
  * `autonomy_enabled` kill switch (persisted; default on) AND the existing `queue_paused` switch.
  * an authority ceiling — tasks above it are left for the human (default gates `human_required`).
  * a per-run attempt cap (in-process) so a run that keeps failing parks instead of looping forever.
  * one fresh DB session per run, so a poison run can't strand the sweep.

Config lives in `ModelOverride` KV rows (same no-migration trick as `queue_paused`); the Settings
screen edits them. Retention (housekeeping) runs on its own slow cadence regardless of the kill switch.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from dominion.shared.db import SessionFactory
from dominion.shared.enums import ProductionRunStatus, RepairAuthorityLevel, RepairTaskStatus
from dominion.shared.models import ModelOverride, ProductionRun, RepairTask
from dominion.workers import activity, background_work, production

log = structlog.get_logger()

# --- config (persisted KV; edited from Settings) -------------------------------------------------
AUTONOMY_ENABLED_KEY = "autonomy_enabled"
INTERVAL_KEY = "sweeper_interval_s"
STALE_WINDOW_KEY = "sweeper_stale_window_s"
CEILING_KEY = "sweeper_authority_ceiling"
MAX_ATTEMPTS_KEY = "sweeper_max_attempts"
RETENTION_DAYS_KEY = "retention_days"

_DEFAULTS = {
    AUTONOMY_ENABLED_KEY: "1",
    INTERVAL_KEY: "120",
    STALE_WINDOW_KEY: "120",
    CEILING_KEY: RepairAuthorityLevel.CHAPTER_STRUCTURAL.value,
    MAX_ATTEMPTS_KEY: "3",
    RETENTION_DAYS_KEY: "30",
}

# Authority is an ordered ladder; the ceiling gates what the sweeper may auto-approve. human_required
# is always above any ceiling (it must never be auto-approved).
_AUTHORITY_RANK = {level: i for i, level in enumerate(RepairAuthorityLevel)}

_ELIGIBLE_RUN_STATUSES = (
    ProductionRunStatus.QUEUED,
    ProductionRunStatus.RUNNING,
    ProductionRunStatus.WAITING_FOR_HUMAN,
    ProductionRunStatus.REPAIRING,
    ProductionRunStatus.BLOCKED,
)

# In-process state (reset on redeploy — a natural fresh start for a run that had parked).
_lock = asyncio.Lock()
_attempts: dict[str, int] = {}  # run_id -> autonomous apply attempts this process
_warned_human: set[str] = set()  # run_ids we've already flagged as needing a human, so we warn once
_last_retention_monotonic = 0.0
_RETENTION_MIN_INTERVAL_S = 3600.0  # run housekeeping at most hourly regardless of tick cadence

# Liveness heartbeat, refreshed EVERY tick (even when autonomy is off/paused, so the dashboard can show
# "swept 12s ago · off/paused"). Reset on redeploy. Reassigned atomically at the end of a tick so a
# reader never sees a half-updated dict (the API event loop is single-threaded).
_heartbeat: dict[str, Any] = {
    "last_tick_at": None,  # datetime | None — advances every tick; the proof the loop is alive
    "ran": False,  # did this tick actually sweep (autonomy on AND not paused)?
    "autonomy_enabled": True,
    "paused": False,
    "stale_runs_found": 0,
    "actions": [],  # [{run_id, kind}] this tick took (repair_applied | verified | blocked)
    "driving": [],  # run ids this tick swept
    "last_error": None,  # str | None — last per-run sweep failure this tick
}


@dataclass
class SweeperConfig:
    autonomy_enabled: bool
    interval_s: int
    stale_window_s: int
    ceiling: str
    max_attempts: int
    retention_days: int


async def _get(session, key: str) -> str:
    row = await session.get(ModelOverride, key)
    return row.model if row is not None and row.model is not None else _DEFAULTS[key]


def _int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


async def load_config(session) -> SweeperConfig:
    return SweeperConfig(
        autonomy_enabled=(await _get(session, AUTONOMY_ENABLED_KEY)) == "1",
        interval_s=max(15, _int(await _get(session, INTERVAL_KEY), 120)),
        stale_window_s=max(0, _int(await _get(session, STALE_WINDOW_KEY), 120)),
        ceiling=await _get(session, CEILING_KEY),
        max_attempts=max(0, _int(await _get(session, MAX_ATTEMPTS_KEY), 3)),
        retention_days=max(0, _int(await _get(session, RETENTION_DAYS_KEY), 30)),
    )


async def set_setting(session, key: str, value: str) -> None:
    """Upsert one sweeper KV row (same ModelOverride store as queue_paused — no migration)."""
    row = await session.get(ModelOverride, key)
    if row is None:
        session.add(ModelOverride(setting_name=key, model=value))
    else:
        row.model = value


async def save_config(
    session,
    *,
    autonomy_enabled: bool | None = None,
    interval_s: int | None = None,
    stale_window_s: int | None = None,
    authority_ceiling: str | None = None,
    max_attempts: int | None = None,
    retention_days: int | None = None,
) -> None:
    """Persist only the provided fields; None leaves a setting unchanged. Does not commit."""
    if autonomy_enabled is not None:
        await set_setting(session, AUTONOMY_ENABLED_KEY, "1" if autonomy_enabled else "0")
    if interval_s is not None:
        await set_setting(session, INTERVAL_KEY, str(interval_s))
    if stale_window_s is not None:
        await set_setting(session, STALE_WINDOW_KEY, str(stale_window_s))
    if authority_ceiling is not None:
        await set_setting(session, CEILING_KEY, authority_ceiling)
    if max_attempts is not None:
        await set_setting(session, MAX_ATTEMPTS_KEY, str(max_attempts))
    if retention_days is not None:
        await set_setting(session, RETENTION_DAYS_KEY, str(retention_days))


def _rank(level: str) -> int:
    try:
        return _AUTHORITY_RANK[RepairAuthorityLevel(level)]
    except ValueError:
        return 999  # unknown authority → treat as beyond any ceiling (never auto-approve)


def _within_ceiling(level: str, ceiling: str) -> bool:
    """True if `level` may be auto-approved under `ceiling` — an honest inclusive threshold. At the
    default ceiling (chapter_structural) human_required stays gated; raising the ceiling to
    human_required opts into full autonomy so the sweeper also drives the highest-authority repairs."""
    return _rank(level) <= _rank(ceiling)


async def _stale_run_ids(session, stale_window_s: int, limit: int = 10) -> list:
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_window_s)
    rows = await session.execute(
        select(ProductionRun.id)
        .where(ProductionRun.status.in_(_ELIGIBLE_RUN_STATUSES), ProductionRun.updated_at < cutoff)
        .order_by(ProductionRun.updated_at)
        .limit(limit)
    )
    return list(rows.scalars().all())


async def _sweep_one_run(session, run_id, cfg: SweeperConfig) -> list[dict[str, str]]:
    """Drive a single stalled run forward on the given session (caller commits). Best-effort
    throughout — a failure here parks the run for a human, it never crashes the loop. Returns the
    actions it took (for the heartbeat): [{run_id, kind}] with kind in repair_applied|verified|blocked."""
    actions: list[dict[str, str]] = []
    run = await session.get(ProductionRun, run_id)
    if run is None or run.status in (
        ProductionRunStatus.COMPLETED,
        ProductionRunStatus.CANCELLED,
        ProductionRunStatus.FAILED,
    ):
        return actions
    book_id, chapter_id = run.book_id, run.chapter_id
    rid = str(run_id)

    # 1) Re-triage — isolated in a savepoint so a failure here can't abort auto-approving the run's
    # EXISTING repair tasks, and labeled so a stage that fails in prod is named in the logs (we hit a
    # data-specific greenlet_spawn here that no synthetic run reproduces).
    try:
        async with session.begin_nested():
            await production.triage_production_run(session, run_id)
    except Exception as exc:  # noqa: BLE001 — best-effort; existing tasks are still driven below
        log.warning("sweeper.stage_error", run=rid, stage="triage", error=str(exc))

    tasks = (
        (
            await session.execute(
                select(RepairTask).where(
                    RepairTask.production_run_id == run_id,
                    RepairTask.status.in_((RepairTaskStatus.QUEUED, RepairTaskStatus.WAITING_FOR_HUMAN)),
                    RepairTask.requires_human_approval.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )

    needs_human = False
    for task in tasks:
        if task.human_approved_at is not None:
            continue  # already approved; the drain / a prior tick handles execution
        # Capture identity + authority as primitives BEFORE the savepoint. A mid-apply failure rolls the
        # savepoint back and expires this row's flushed attributes, so ANY post-rollback read — even
        # task.id — becomes a sync lazy-load on the async session (MissingGreenlet, observed at prod).
        # Mirrors background_work.drain_queued_repair_tasks, which captures task_id before its try.
        tid, task_id, authority = str(task.id), task.id, task.authority_level
        if not _within_ceiling(authority, cfg.ceiling):
            needs_human = True
            continue
        if _attempts.get(rid, 0) >= cfg.max_attempts:
            needs_human = True
            continue
        try:
            # SAVEPOINT so a mid-apply failure rolls back only this task, not the whole run's tick.
            async with session.begin_nested():
                await production.apply_repair_task(
                    session, task_id, human_approved=True, approval_reason="autonomous sweeper"
                )
            _attempts[rid] = _attempts.get(rid, 0) + 1
            actions.append({"run_id": rid, "kind": "repair_applied"})
            await activity.record_activity(
                session,
                kind="sweeper_repair",
                title=f"Sweeper auto-approved a {authority.replace('_', ' ')} repair",
                source="sweeper",
                severity="info",
                book_id=book_id,
                chapter_id=chapter_id,
                production_run_id=run_id,
                payload={"repair_task_id": tid, "authority_level": authority},
            )
        except ValueError as exc:
            # e.g. "draft the missing scenes first" — the sweeper can't resolve it; leave for a human.
            needs_human = True
            actions.append({"run_id": rid, "kind": "blocked"})
            await activity.record_activity(
                session,
                kind="sweeper_blocked",
                title="Sweeper could not auto-apply a repair — needs a human",
                source="sweeper",
                severity="warn",
                book_id=book_id,
                chapter_id=chapter_id,
                production_run_id=run_id,
                detail=str(exc),
                payload={"repair_task_id": tid},
            )
        except Exception as exc:  # noqa: BLE001 — unexpected apply failure; log the stage + move on
            needs_human = True
            log.error("sweeper.stage_error", run=rid, stage="apply", task=tid, error=str(exc))

    # 2) Auto-verify applied repairs whose revision jobs have landed (tolerate "not ready yet").
    applied = (
        (
            await session.execute(
                select(RepairTask).where(
                    RepairTask.production_run_id == run_id,
                    RepairTask.status.in_((RepairTaskStatus.RUNNING, RepairTaskStatus.WAITING_FOR_HUMAN)),
                    RepairTask.human_approved_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for task in applied:
        tid, task_id = str(task.id), task.id  # primitives before the savepoint (see the apply loop)
        try:
            # SAVEPOINT: a "still drafting" verify raises after possibly touching rows — isolate it.
            async with session.begin_nested():
                await production.verify_repair_task(session, task_id)  # emits repair_verified via support.record_event
            actions.append({"run_id": rid, "kind": "verified"})
        except ValueError:
            pass  # revision still drafting — a later tick will pick it up
        except Exception as exc:  # noqa: BLE001 — unexpected verify failure; log the stage + move on
            log.warning("sweeper.stage_error", run=rid, stage="verify", task=tid, error=str(exc))

    # 3) Warn once if the run is blocked only on above-ceiling / human-required work.
    if needs_human and rid not in _warned_human:
        _warned_human.add(rid)
        await activity.record_activity(
            session,
            kind="run_blocked",
            title="Run needs human approval (above the autonomy ceiling)",
            source="sweeper",
            severity="warn",
            book_id=book_id,
            chapter_id=chapter_id,
            production_run_id=run_id,
        )
    return actions


async def _maybe_retention(cfg: SweeperConfig) -> None:
    global _last_retention_monotonic
    if cfg.retention_days <= 0:
        return
    now = time.monotonic()
    if now - _last_retention_monotonic < _RETENTION_MIN_INTERVAL_S:
        return
    _last_retention_monotonic = now
    try:
        from dominion.workers import retention

        async with SessionFactory() as session:
            await retention.run_retention(session, days=cfg.retention_days)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — housekeeping must never crash the loop
        log.warning("sweeper.retention_failed", error=str(exc))


async def run_tick() -> None:
    """One sweep pass. Guarded by the single-flight lock so a slow tick can't overlap the next."""
    if _lock.locked():
        return
    async with _lock:
        async with SessionFactory() as session:
            cfg = await load_config(session)
            paused = await background_work.load_queue_paused(session)

        # Build this tick's heartbeat as we go, then publish it atomically at the end. Populated even
        # when autonomy is off/paused so the dashboard's liveness line ("swept 12s ago") stays honest.
        tick: dict[str, Any] = {
            "last_tick_at": datetime.now(UTC),
            "ran": False,
            "autonomy_enabled": cfg.autonomy_enabled,
            "paused": paused,
            "stale_runs_found": 0,
            "actions": [],
            "driving": [],
            "last_error": None,
        }

        if cfg.autonomy_enabled and not paused:
            tick["ran"] = True
            run_ids: list = []
            async with SessionFactory() as session:
                run_ids = await _stale_run_ids(session, cfg.stale_window_s)
            tick["stale_runs_found"] = len(run_ids)
            tick["driving"] = [str(run_id) for run_id in run_ids]
            for run_id in run_ids or []:
                try:
                    async with SessionFactory() as session:  # fresh session per run — isolate failures
                        acted = await _sweep_one_run(session, run_id, cfg)
                        await session.commit()
                    tick["actions"].extend(acted)
                except Exception as exc:  # noqa: BLE001 — one bad run must not strand the rest
                    # Full traceback: a data-specific greenlet_spawn fires OUTSIDE the wrapped stages on
                    # one real run and no synthetic run reproduces it — the frame names the exact line.
                    tick["last_error"] = f"{run_id}: {exc}"
                    log.error("sweeper.run_error", run=str(run_id), error=str(exc), tb=traceback.format_exc())
            if run_ids:
                # Drive all newly-eligible non-approval tasks + chain the job drain (drafts revisions).
                await background_work.drain_queued_repair_tasks()

        global _heartbeat
        _heartbeat = tick
        await _maybe_retention(cfg)


async def sweeper_status(session) -> dict[str, Any]:
    """The sweeper's live status = in-process heartbeat + persisted config + per-run attempt counts.
    autonomy_enabled/paused are re-read live (the heartbeat may lag by up to one tick after a settings
    change). Cheap — a few KV reads plus in-process state; the single API process owns the loop."""
    cfg = await load_config(session)
    return {
        **_heartbeat,
        "autonomy_enabled": cfg.autonomy_enabled,
        "paused": background_work.queue_paused(),
        "interval_s": cfg.interval_s,
        "stale_window_s": cfg.stale_window_s,
        "authority_ceiling": cfg.ceiling,
        "max_attempts": cfg.max_attempts,
        "attempts": dict(_attempts),
    }


async def _current_interval_s() -> int:
    """The live tick cadence, re-read from the KV config each loop so a Settings change takes effect on
    the next sleep. Extracted from `run_forever` so the loop's scheduling can be driven in tests on a
    fake clock with no DB (inject `read_interval`)."""
    async with SessionFactory() as session:
        return (await load_config(session)).interval_s


async def run_forever(
    *,
    tick: Callable[[], Awaitable[None]] = run_tick,
    read_interval: Callable[[], Awaitable[int]] = _current_interval_s,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """The lifespan-owned loop. Sleeps the configured interval between ticks; a tick failure is logged
    and the loop continues (it must survive transient DB blips across the whole deploy lifetime).

    The four collaborators all default to the real ones, so production behavior is unchanged — they are a
    pure test seam. `tick`/`read_interval`/`sleep` let a test drive scheduling on a fake clock (no
    wall-clock wait, no DB); `should_stop` (default None = never stops, exactly as before) lets a test run
    the loop a fixed number of iterations and then exit cleanly."""
    log.info("sweeper.started")
    while True:
        interval = 120
        try:
            await tick()
            interval = await read_interval()
        except asyncio.CancelledError:
            log.info("sweeper.stopped")
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die on a tick error
            log.error("sweeper.tick_error", error=str(exc))
        if should_stop is not None and should_stop():
            return
        await sleep(interval)
