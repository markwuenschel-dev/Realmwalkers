"""T2 (#230, ADR-0031 D7) — the persisted repair-attempt ceiling and terminal parking.

The defect: nothing bounded automatic repair. `RepairAttempt.attempt_no` was never compared to a cap;
the only cap was `sweeper._attempts`, an in-process dict keyed by `run_id` that
`drain_queued_repair_tasks` never consulted. Because `NEEDS_ANOTHER_REPAIR` re-queues a task and the
drain re-applies it, the drain path was unbounded.

These are #230's seven acceptance tests. Each one fails against the pre-T2 code.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from test_sweeper import _approval_task, _seed_run

from dominion.shared import repair_budget
from dominion.shared.enums import RepairAuthorityLevel, RepairTaskStatus, RepairTerminalReason
from dominion.shared.models import AgentEvent, RepairTask
from dominion.workers import production

CEILING = RepairAuthorityLevel.CHAPTER_STRUCTURAL.value


async def _drainable_task(s, *, scene_count: int = 2):
    """A chapter-scoped repair whose member scenes have real prose, at a blast radius the DEFAULT
    authorization ceiling covers — so an autonomous apply is authorized, reaches the budget seam, and
    actually applies. Built on test_sweeper's fixtures so this file measures the budget and nothing else."""
    _book, _chapter, run, scenes = await _seed_run(s, scene_count=scene_count)
    task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.SCENE_LOCAL)
    return run, task


async def _requeue(s, task_id) -> None:
    """What a NEEDS_ANOTHER_REPAIR verdict does (`production_repair.py:1003`): put the task back in the
    queue for another automatic attempt. The budget must survive this — that is the whole point."""
    task = await s.get(RepairTask, task_id)
    task.status = RepairTaskStatus.QUEUED
    await s.flush()


# --- 1 + 4: bounded, and bounded at the right boundary --------------------------------------------


async def test_cap_boundary_below_at_and_above(db_factory):
    """#230 acceptance 4. Attempts 1-2 proceed; the 3rd is the last automatic one; a 4th is NEVER
    enqueued — it parks with a persisted terminal reason instead."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        for expected in (1, 2, 3):
            await _requeue(s, task_id)
            out = await production.apply_repair_task(s, task_id, autonomous=True)
            assert out.repair_cycle_attempts == expected, expected
            assert out.terminal_reason is None, expected

        await _requeue(s, task_id)
        out = await production.apply_repair_task(s, task_id, autonomous=True)
        assert out.repair_cycle_attempts == repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS  # not incremented
        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert out.terminal_reason == RepairTerminalReason.ATTEMPT_CAP_REACHED.value


async def test_bounded_from_the_sweeper_entry_point_too(db_factory):
    """#230 acceptance 1, sweeper half. The sweeper declares a ceiling, so it reaches the same seam —
    one central policy, not a per-worker one. Pre-T2 the sweeper had its own process-local counter."""
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        task_id = task.id
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True, authorization_ceiling=CEILING)
        await _requeue(s, task_id)
        out = await production.apply_repair_task(s, task_id, autonomous=True, authorization_ceiling=CEILING)
        assert out.terminal_reason == RepairTerminalReason.ATTEMPT_CAP_REACHED.value
        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN


async def test_the_drain_query_cannot_reclaim_a_parked_task(db_factory):
    """#230 acceptance 1, drain half + acceptance 6. The drain claims only QUEUED rows; a parked cycle is
    WAITING_FOR_HUMAN, so the loop that used to re-apply forever can no longer see it."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS + 1):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True)
        await s.flush()
        claimable = (
            (await s.execute(select(RepairTask.id).where(RepairTask.status == RepairTaskStatus.QUEUED))).scalars().all()
        )
        assert task_id not in claimable


# --- 2: survives restart --------------------------------------------------------------------------


async def test_budget_and_park_survive_a_restart(db_factory):
    """#230 acceptance 2. The pre-T2 cap was `sweeper._attempts`, reset on redeploy by its own comment.
    A fresh session standing in for a new process must see the spent budget and the park."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS + 1):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True)
        await s.commit()

    async with db_factory() as fresh:  # a new process: no in-process state carries over
        reloaded = await fresh.get(RepairTask, task_id)
        assert reloaded.repair_cycle_attempts == repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS
        assert reloaded.terminal_reason == RepairTerminalReason.ATTEMPT_CAP_REACHED.value
        # And it stays parked: another automatic apply is refused rather than starting a fresh budget.
        reloaded.status = RepairTaskStatus.QUEUED
        await fresh.flush()
        out = await production.apply_repair_task(fresh, task_id, autonomous=True)
        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert out.repair_cycle_attempts == repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS


# --- 3: the unit is the cycle, not the attempt row ------------------------------------------------


async def test_a_chapter_scoped_repair_consumes_one_attempt_not_one_per_scene(db_factory):
    """#230 acceptance 3. A chapter-scoped repair mints one RepairAttempt per member scene
    (`production_repair.py:576-601`). Counting rows would exhaust a cap of 3 on a 3-scene chapter's
    FIRST action; the budget counts cycles, so one apply costs exactly one."""
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s, scene_count=3)
        assert len(scenes) == 3
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        out = await production.apply_repair_task(s, task.id, autonomous=True, authorization_ceiling=CEILING)
        assert out.repair_cycle_attempts == 1  # one apply == one attempt, whatever it fans out to
        assert out.terminal_reason is None


# --- 5: the terminal reason is persisted and operator-visible -------------------------------------


async def test_terminal_reason_is_persisted_and_emitted_as_an_event(db_factory):
    """#230 acceptance 5. Pre-T2 no such field existed at all, so 'why did this stop?' was unanswerable."""
    async with db_factory() as s:
        run, task = await _drainable_task(s)
        task_id, run_id = task.id, run.id
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS + 1):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True)
        await s.flush()

        assert "terminal_reason" in RepairTask.__table__.columns  # a column, not an inferred event scan
        events = (
            (
                await s.execute(
                    select(AgentEvent).where(
                        AgentEvent.production_run_id == run_id,
                        AgentEvent.event_type == "repair_cycle_parked",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        payload = events[0].payload_json or {}
        assert payload["terminal_reason"] == RepairTerminalReason.ATTEMPT_CAP_REACHED.value
        assert payload["max_attempts"] == repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS


# --- 6: only explicit human action reopens a cycle ------------------------------------------------


async def test_only_an_explicit_manual_action_reopens_a_parked_cycle(db_factory):
    """#230 acceptance 6. A parked task never resumes on its own — no worker can reopen its own cycle,
    however many times it retries. A manual route (ADR-0030's `manual_command`: a deliberate command
    through an explicit route) refunds the budget and clears the reason."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS + 1):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True)
        assert (await s.get(RepairTask, task_id)).terminal_reason is not None

        # An autonomous retry does NOT reopen it, however many times it is tried.
        await _requeue(s, task_id)
        out = await production.apply_repair_task(s, task_id, autonomous=True)
        assert out.terminal_reason == RepairTerminalReason.ATTEMPT_CAP_REACHED.value

        # A human Approve & apply does.
        out = await production.apply_repair_task(s, task_id, autonomous=False, human_approved=True)
        assert out.terminal_reason is None
        assert out.repair_cycle_attempts == 0
        assert out.human_approved_at is not None


# --- 7: a manual apply is outside the budget entirely ---------------------------------------------


async def test_manual_apply_neither_consumes_budget_nor_is_blocked_by_it(db_factory):
    """#230 acceptance 7. The limit bounds UNATTENDED work; a human deciding to apply something is the
    thing the limit defers to, so it must be neither charged nor refused."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        # Not charged.
        for _ in range(5):
            await _requeue(s, task_id)
            out = await production.apply_repair_task(s, task_id, autonomous=False)
            assert out.repair_cycle_attempts == 0
            assert out.terminal_reason is None
        # Not blocked, even once the automatic budget is exhausted and the cycle has parked.
        for _ in range(repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS + 1):
            await _requeue(s, task_id)
            await production.apply_repair_task(s, task_id, autonomous=True)
        assert (await s.get(RepairTask, task_id)).terminal_reason is not None
        await _requeue(s, task_id)
        out = await production.apply_repair_task(s, task_id, autonomous=False)
        assert out.terminal_reason is None  # the manual apply reopened it rather than being refused
        assert out.status == RepairTaskStatus.RUNNING


# --- the seam itself (no DB) ----------------------------------------------------------------------


def test_reservation_is_a_pure_mutation_of_the_task_row():
    class _T:
        repair_cycle_attempts = 0
        terminal_reason = None

    t = _T()
    for expected in (1, 2, 3):
        r = repair_budget.reserve_automatic_attempt(t, autonomous=True)
        assert r.granted and r.attempts == expected
    r = repair_budget.reserve_automatic_attempt(t, autonomous=True)
    assert not r.granted
    assert r.terminal_reason == RepairTerminalReason.ATTEMPT_CAP_REACHED.value
    assert t.repair_cycle_attempts == repair_budget.MAX_REPAIR_CYCLE_ATTEMPTS  # refusal never increments

    repair_budget.reopen_cycle(t)
    assert t.repair_cycle_attempts == 0 and t.terminal_reason is None


def test_a_parked_task_is_refused_even_below_the_cap():
    """A hard failure parks with attempts to spare. The terminal reason, not the counter, is what keeps
    it parked — otherwise a restart plus a re-queue would resurrect it with budget remaining."""

    class _T:
        repair_cycle_attempts = 1
        terminal_reason = RepairTerminalReason.HARD_FAILURE.value

    r = repair_budget.reserve_automatic_attempt(_T(), autonomous=True)
    assert not r.granted and r.terminal_reason == RepairTerminalReason.HARD_FAILURE.value


@pytest.mark.parametrize("autonomous", [True, False])
def test_manual_is_never_charged_and_automatic_always_is(autonomous):
    class _T:
        repair_cycle_attempts = 0
        terminal_reason = None

    t = _T()
    repair_budget.reserve_automatic_attempt(t, autonomous=autonomous)
    assert t.repair_cycle_attempts == (1 if autonomous else 0)


async def test_a_manual_apply_does_not_refund_a_LIVE_cycle(db_factory):
    """The reopen is scoped to a PARKED cycle. A human clicking Apply mid-cycle must not silently hand
    the automatic workers a fresh budget — that would make the cap depend on how often a human looked."""
    async with db_factory() as s:
        _run, task = await _drainable_task(s)
        task_id = task.id
        await production.apply_repair_task(s, task_id, autonomous=True)
        assert (await s.get(RepairTask, task_id)).repair_cycle_attempts == 1
        await _requeue(s, task_id)
        await production.apply_repair_task(s, task_id, autonomous=False)
        assert (await s.get(RepairTask, task_id)).repair_cycle_attempts == 1  # unchanged, not refunded
