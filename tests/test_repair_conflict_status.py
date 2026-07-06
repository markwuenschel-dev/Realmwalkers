"""Fitness check for the overlapping-span repair-conflict gate (audit candidate D1, 2026-07-06).

``apply_repair_task`` refuses to auto-apply a repair whose edit span overlaps another *in-flight*
repair on the same scene, parking the newcomer as WAITING_FOR_HUMAN so two repairs can't clobber the
same passage. The set of "in-flight" statuses must be keyed to real ``RepairTaskStatus`` members.

The bug this guards against: the query filtered on
``RepairTask.status.in_(["queued", "running", "repair_queued"])``. ``"repair_queued"`` is an
``IssueStatus`` value, *not* a ``RepairTaskStatus``, so that arm silently never matched; and
``waiting_for_human`` — the very status this gate assigns to a parked conflict, and which
``pipeline_status`` already treats as an open repair — was omitted. So a repair overlapping a
human-parked repair slipped through the gate unblocked.

Two layers:
  * behavioral (DB) — an overlapping OPEN repair (incl. waiting_for_human) blocks; an overlapping
    TERMINAL repair does not; a non-overlapping span never blocks.
  * contract guard (no DB) — the in-flight constant contains only real RepairTaskStatus members, and
    open + terminal partitions the whole enum, so a future status can't silently fall through.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared.enums import (
    IssueStatus,
    JobKind,
    ProductionRunStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
)
from dominion.shared.models import (
    Book,
    Chapter,
    Issue,
    Job,
    ProductionRun,
    RepairTask,
    Scene,
)
from dominion.workers import production

QUOTE = "Mara moves through the breach"
OTHER_QUOTE = "The signal fire gutters low"


async def _seed_scene(s):
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=7, pov="Mara", title="Signal Fire")
    s.add(chapter)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="repairing")
    s.add(run)
    await s.flush()
    scene = Scene(
        chapter_id=chapter.id,
        scene_no=1,
        version=1,
        status="pending_review",
        word_count=12,
        prose=f"{QUOTE}. {OTHER_QUOTE}.",
        prose_source="agent",
    )
    s.add(scene)
    await s.flush()
    return book, chapter, run, scene


async def _make_task(s, run, scene, *, status: RepairTaskStatus, quote: str = QUOTE) -> RepairTask:
    issue = Issue(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        artifact_type="scene_review_report",
        artifact_id=uuid.uuid4(),
        scene_id=scene.id,
        scene_no=scene.scene_no,
        validator="voice",
        issue_kind="flat_line",
        severity="repair",
        quote=quote,
        claim="This line reads flat; sharpen the sensory beat.",
        recommended_action="Rework the flagged span.",
        status=IssueStatus.ACCEPTED,
        payload_json={"signature": f"sig-{uuid.uuid4()}"},
    )
    s.add(issue)
    await s.flush()
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        scene_id=scene.id,
        scene_no=scene.scene_no,
        repair_kind="expand",
        authority_level=RepairAuthorityLevel.SPAN_ONLY,
        status=status,
        issue_ids=[str(issue.id)],
        target_spans={"items": [{"quote": quote}]},
        instructions="Repair kind: expand. Rework the flagged span with a sharper sensory beat.",
        preserve=["Preserve scene outcome."],
        must_change=[quote],
        allowed_operations=["replace_span", "rewrite_scene"],
        forbidden_operations=["change_canon", "change_chapter_outcome"],
        requires_human_approval=False,
    )
    s.add(task)
    await s.flush()
    return task


async def _revision_job_count(s, run) -> int:
    jobs = (
        (await s.execute(select(Job).where(Job.kind.in_([JobKind.REVISE_FULL, JobKind.REVISE_PASS])))).scalars().all()
    )
    return len(jobs)


# --- behavioral: overlapping OPEN repair blocks (the bug bit here for waiting_for_human) ----------


async def test_overlapping_waiting_for_human_repair_blocks_new_repair(db_factory):
    # A repair parked for a human is still an unresolved edit on this span. A new overlapping repair
    # must NOT auto-apply on top of it — it parks too. (RED before the fix: waiting_for_human was
    # omitted from the in-flight set, so the newcomer proceeded to RUNNING and queued a revision.)
    async with db_factory() as s:
        _book, _chapter, run, scene = await _seed_scene(s)
        await _make_task(s, run, scene, status=RepairTaskStatus.WAITING_FOR_HUMAN)
        newcomer = await _make_task(s, run, scene, status=RepairTaskStatus.QUEUED)

        out = await production.apply_repair_task(s, newcomer.id)

        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN
        run = await s.get(ProductionRun, run.id)
        assert run.status == ProductionRunStatus.WAITING_FOR_HUMAN
        # Parked before scheduling any work: no revision job was queued.
        assert await _revision_job_count(s, run) == 0


async def test_overlapping_running_repair_blocks_new_repair(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scene = await _seed_scene(s)
        await _make_task(s, run, scene, status=RepairTaskStatus.RUNNING)
        newcomer = await _make_task(s, run, scene, status=RepairTaskStatus.QUEUED)

        out = await production.apply_repair_task(s, newcomer.id)

        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert await _revision_job_count(s, run) == 0


# --- behavioral: overlapping TERMINAL repair does NOT block (no over-blocking) --------------------


async def test_overlapping_terminal_repair_does_not_block(db_factory):
    # A verified/cancelled/etc. repair can no longer change the span, so it must not block a new one.
    for terminal in (
        RepairTaskStatus.VERIFIED,
        RepairTaskStatus.REJECTED,
        RepairTaskStatus.FAILED,
        RepairTaskStatus.CANCELLED,
    ):
        async with db_factory() as s:
            _book, _chapter, run, scene = await _seed_scene(s)
            await _make_task(s, run, scene, status=terminal)
            newcomer = await _make_task(s, run, scene, status=RepairTaskStatus.QUEUED)

            out = await production.apply_repair_task(s, newcomer.id)

            assert out.status == RepairTaskStatus.RUNNING, f"terminal {terminal} should not block"
            assert await _revision_job_count(s, run) == 1


async def test_non_overlapping_span_does_not_block(db_factory):
    # An open repair on a DIFFERENT span (different quote) never blocks.
    async with db_factory() as s:
        _book, _chapter, run, scene = await _seed_scene(s)
        await _make_task(s, run, scene, status=RepairTaskStatus.WAITING_FOR_HUMAN, quote=OTHER_QUOTE)
        newcomer = await _make_task(s, run, scene, status=RepairTaskStatus.QUEUED, quote=QUOTE)

        out = await production.apply_repair_task(s, newcomer.id)

        assert out.status == RepairTaskStatus.RUNNING
        assert await _revision_job_count(s, run) == 1


# --- contract guard (no DB): the in-flight set stays coherent with the RepairTaskStatus vocabulary --


def test_open_repair_statuses_are_all_real_repair_task_status_members():
    # The exact bug: a foreign literal ("repair_queued", an IssueStatus) slipped into the in-flight
    # set. Every entry must be a genuine RepairTaskStatus member.
    from dominion.workers.production_repair import _OPEN_REPAIR_STATUSES

    valid = set(RepairTaskStatus)
    assert set(_OPEN_REPAIR_STATUSES) <= valid


def test_repair_queued_is_not_a_repair_task_status():
    # Regression anchor for the original defect: "repair_queued" belongs to IssueStatus, not
    # RepairTaskStatus, so it can never match a RepairTask.status filter.
    assert IssueStatus.REPAIR_QUEUED.value == "repair_queued"
    assert "repair_queued" not in {s.value for s in RepairTaskStatus}


def test_every_repair_status_is_classified_open_or_terminal():
    # Future-resistant partition: adding a RepairTaskStatus member forces a conscious open/terminal
    # decision here, instead of silently falling through the conflict gate.
    from dominion.workers.production_repair import (
        _OPEN_REPAIR_STATUSES,
        _TERMINAL_REPAIR_STATUSES,
    )

    open_set = set(_OPEN_REPAIR_STATUSES)
    terminal_set = set(_TERMINAL_REPAIR_STATUSES)
    assert open_set.isdisjoint(terminal_set)
    assert open_set | terminal_set == set(RepairTaskStatus)


def test_conflict_gate_treats_waiting_for_human_as_open():
    # Pin the decision made for audit candidate D1: a human-parked repair is IN-FLIGHT, so an
    # overlapping newcomer must also park rather than auto-apply over an unresolved edit.
    from dominion.workers.production_repair import _OPEN_REPAIR_STATUSES

    assert RepairTaskStatus.WAITING_FOR_HUMAN in _OPEN_REPAIR_STATUSES
