"""A1b/A1c — the repair-authorization boundary (ADR-0031 D16).

Ceiling authorization (the sweeper's) is distinct from a human approval, and manual-grant work can never
be autonomously authorized regardless of ceiling. The core apply seam is the invariant: it locks the task
row, then decides once via `shared/authorization.authorize_repair` before any stamp or job scheduling.
`human_approved_at` is a HUMAN audit stamp and is written only on a real human grant.

A1c changed what an automated caller must bring. Being autonomous is no longer itself a grant — a caller
authorizes work by DECLARING a ceiling that covers its blast radius. The matrix these pin:

    caller                            declared ceiling    human_approved  outcome
    sweeper: chapter-structural       chapter_structural  False           applied, no human stamp
    sweeper: chapter-structural       (default)           False           refused — above the ceiling
    sweeper: manual-grant work        any, incl. its own  False           refused (needs a human grant)
    plain manual apply (no grant)     (default)           False           waits for a human
    human "Approve & apply"           —                   True            applied, human stamp written
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from sqlalchemy import func, select
from test_sweeper import _approval_task, _seed_run

from dominion.shared.enums import RepairAuthorityLevel, RepairTaskStatus
from dominion.shared.models import Job, RepairTask
from dominion.workers import production, production_repair


async def _run_job_count(s, run_id) -> int:
    return int(await s.scalar(select(func.count()).select_from(Job).where(Job.production_run_id == run_id)) or 0)


def test_apply_requires_explicit_autonomous_no_default():
    # A defaulted `autonomous=False` is not a choke-point invariant — future automation could omit it and
    # impersonate the manual path. Both the facade and the core seam must require it explicitly.
    for fn in (production.apply_repair_task, production_repair.apply_repair_task):
        assert fn.__name__ == "apply_repair_task"
        assert inspect.signature(fn).parameters["autonomous"].default is inspect.Parameter.empty


async def test_core_refuses_autonomous_human_required(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.HUMAN_REQUIRED)
        await production.apply_repair_task(s, task.id, autonomous=True, human_approved=False)
        await s.commit()

        got = await s.get(RepairTask, task.id)
        assert got.status == RepairTaskStatus.WAITING_FOR_HUMAN  # refused, not applied
        assert got.human_approved_at is None  # no stamp
        assert await _run_job_count(s, run.id) == 0  # no revision job scheduled


async def test_core_ceiling_authorized_apply_writes_no_human_stamp(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        await production.apply_repair_task(
            s,
            task.id,
            autonomous=True,
            human_approved=False,
            authorization_ceiling=RepairAuthorityLevel.CHAPTER_STRUCTURAL.value,
        )
        await s.commit()

        got = await s.get(RepairTask, task.id)
        assert got.status == RepairTaskStatus.RUNNING  # ceiling-authorized + applied
        assert got.human_approved_at is None  # ceiling authorization is not a human approval


async def test_core_autonomous_without_a_covering_ceiling_is_refused(db_factory):
    # THE A1c invariant. The retired gate read `... or autonomous`, so an automated caller authorized
    # itself no matter how large the blast radius. An automated caller must now DECLARE a ceiling that
    # covers the work; the default ceiling does not cover chapter-structural, so this parks.
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        await production.apply_repair_task(s, task.id, autonomous=True, human_approved=False)
        await s.commit()

        got = await s.get(RepairTask, task.id)
        assert got.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert got.human_approved_at is None
        assert await _run_job_count(s, run.id) == 0  # refused before any revision was scheduled


async def test_core_human_can_grant_human_required(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.HUMAN_REQUIRED)
        await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)
        await s.commit()

        got = await s.get(RepairTask, task.id)
        assert got.status == RepairTaskStatus.RUNNING  # a human may grant human_required
        assert got.human_approved_at is not None  # human grant is stamped


async def test_concurrent_apply_schedules_one_revision(db_factory):
    # Row-lock the apply seam: a sweeper apply racing a human "Approve & apply" on the same task must
    # schedule ONE revision, not two. Without FOR UPDATE both read the pre-apply status and both fan out.
    async with db_factory() as setup:
        _book, _chapter, run, scenes = await _seed_run(setup)
        task = await _approval_task(setup, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        task_id = task.id
        await setup.commit()

    async def _apply(*, autonomous: bool, human_approved: bool, ceiling: str | None = None) -> str:
        async with db_factory() as s:
            try:
                kw = {"authorization_ceiling": ceiling} if ceiling else {}
                await production.apply_repair_task(
                    s, task_id, autonomous=autonomous, human_approved=human_approved, **kw
                )
                await s.commit()
                return "ok"
            except ValueError:
                return "rejected"  # lost the race → status guard 409

    results = await asyncio.gather(
        # The sweeper declares its configured ceiling (A1c) — otherwise it would simply park the task and
        # the race under test would never happen.
        _apply(autonomous=True, human_approved=False, ceiling=RepairAuthorityLevel.CHAPTER_STRUCTURAL.value),
        _apply(autonomous=False, human_approved=True),  # human Approve & apply
    )

    assert results.count("ok") == 1  # exactly one winner — the lock serialized them
    async with db_factory() as s:
        got = await s.get(RepairTask, task_id)
        assert got.status == RepairTaskStatus.RUNNING


async def test_preloaded_session_apply_after_concurrent_commit_does_not_double_schedule(db_factory):
    # The sweeper PRE-LOADS approval-gated tasks into its session (a plain SELECT), then applies later on
    # that same session. If a human applied the task in between, the pre-loaded session must still see the
    # fresh RUNNING status behind the FOR UPDATE lock — not the stale identity-mapped copy — or it schedules
    # a SECOND revision. (Regression for the with_for_update identity-map refresh: get(..., FOR UPDATE)
    # acquires the lock but does not repopulate an already-loaded instance; only populate_existing does.)
    async with db_factory() as setup:
        _book, _chapter, run, scenes = await _seed_run(setup)
        task = await _approval_task(setup, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        task_id = task.id
        await setup.commit()

    async with db_factory() as s1, db_factory() as s2:
        preloaded = await s1.get(RepairTask, task_id)  # sweeper-style pre-load: stale copy in s1's map
        assert preloaded.status == RepairTaskStatus.WAITING_FOR_HUMAN

        # A human Approve & apply lands first and commits → status RUNNING.
        await production.apply_repair_task(s2, task_id, autonomous=False, human_approved=True)
        await s2.commit()

        # The sweeper (s1) now applies. It must see RUNNING behind the lock and 409 — never re-schedule off
        # its stale pre-load. The raise is the proof it never reached the (post-guard) scheduling step, so
        # no second revision is fanned out.
        with pytest.raises(ValueError, match="only queued or waiting_for_human"):
            await production.apply_repair_task(
                s1,
                task_id,
                autonomous=True,
                human_approved=False,
                authorization_ceiling=RepairAuthorityLevel.CHAPTER_STRUCTURAL.value,
            )

    async with db_factory() as s:
        assert (await s.get(RepairTask, task_id)).status == RepairTaskStatus.RUNNING  # s2's single apply stands
