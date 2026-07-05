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


# --- repair-task drain (Desk Control Round P14) -----------------------------------------------------


async def test_repair_drain_applies_queued_and_never_touches_approval_tasks(db_factory, monkeypatch):
    from test_repair_tasks import _chapter_task, _seed

    from dominion.shared.enums import RepairTaskStatus
    from dominion.shared.models import RepairTask

    monkeypatch.setattr("dominion.shared.db.SessionFactory", db_factory)

    async def no_jobs() -> bool:
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", no_jobs)

    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        auto_task, _ = await _chapter_task(s, run, scenes, requires_approval=False)
        held_task, _ = await _chapter_task(s, run, scenes, requires_approval=True)
        await s.commit()
        auto_id, held_id = auto_task.id, held_task.id

    await bw.drain_queued_repair_tasks()

    async with db_factory() as s:
        auto_task = await s.get(RepairTask, auto_id)
        held_task = await s.get(RepairTask, held_id)
        assert auto_task.status == RepairTaskStatus.RUNNING  # fan-out applied by the drain
        assert held_task.status == RepairTaskStatus.WAITING_FOR_HUMAN  # never claimed
        assert held_task.human_approved_at is None


async def test_repair_drain_honors_pause_between_tasks(db_factory, monkeypatch):
    from test_repair_tasks import _chapter_task, _seed

    from dominion.shared.enums import RepairTaskStatus
    from dominion.shared.models import RepairTask

    monkeypatch.setattr("dominion.shared.db.SessionFactory", db_factory)
    monkeypatch.setattr(bw, "_queue_paused", True)

    async def no_jobs() -> bool:
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", no_jobs)

    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _ = await _chapter_task(s, run, scenes, requires_approval=False)
        await s.commit()
        task_id = task.id

    await bw.drain_queued_repair_tasks()

    async with db_factory() as s:
        task = await s.get(RepairTask, task_id)
        assert task.status == RepairTaskStatus.QUEUED  # paused: nothing claimed


async def test_repair_drain_parks_poison_task_and_advances(db_factory, monkeypatch):
    from test_repair_tasks import _chapter_task, _seed

    from dominion.shared.enums import RepairTaskStatus
    from dominion.shared.models import AgentEvent, RepairTask

    monkeypatch.setattr("dominion.shared.db.SessionFactory", db_factory)

    async def no_jobs() -> bool:
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", no_jobs)

    async def explode(session, task_id, **kwargs):
        raise RuntimeError("poison task")

    monkeypatch.setattr("dominion.workers.production.apply_repair_task", explode)

    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _ = await _chapter_task(s, run, scenes, requires_approval=False)
        await s.commit()
        task_id, run_id = task.id, run.id

    await bw.drain_queued_repair_tasks()  # must terminate: the parked row leaves QUEUED

    async with db_factory() as s:
        task = await s.get(RepairTask, task_id)
        assert task.status == RepairTaskStatus.WAITING_FOR_HUMAN
        from sqlalchemy import select

        events = (
            (
                await s.execute(
                    select(AgentEvent).where(
                        AgentEvent.production_run_id == run_id, AgentEvent.event_type == "repair_drain_error"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events and "poison task" in (events[0].message or "")


async def test_repair_drain_single_flights():
    async with bw._repair_drain_lock:
        assert bw.repair_drain_locked()
        await bw.drain_queued_repair_tasks()  # second entry is a no-op, not a deadlock
    assert not bw.repair_drain_locked()


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
