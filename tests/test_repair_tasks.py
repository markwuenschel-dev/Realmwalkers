"""RepairTask lifecycle: the approval gate, Approve & apply fan-out, fan-out verify, status guard.

Chapter-scoped tasks (scene_id=None — structural clusters, human-approval work) were a dead-end
before the fan-out apply: approval led straight into the "does not target a concrete scene" refusal.
These tests pin the whole unlock: park + event without approval, fan-out per member scene with it,
verification across the union of revised scenes, and the drain-vs-human-click status guard.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from dominion.shared.enums import (
    BeatStatus,
    IssueStatus,
    JobKind,
    RepairAuthorityLevel,
    RepairTaskStatus,
    RepairVerificationVerdict,
)
from dominion.shared.models import (
    AgentEvent,
    Beat,
    Book,
    Chapter,
    Issue,
    Job,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    Scene,
)
from dominion.workers import production
from tests.conftest import seed_scene_packet


async def _seed(s, *, scene_count: int = 2, with_prose: bool = True):
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=7, pov="Mara", title="Signal Fire")
    s.add(chapter)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="repairing")
    s.add(run)
    await s.flush()
    scenes = []
    for scene_no in range(1, scene_count + 1):
        scene = Scene(
            chapter_id=chapter.id,
            scene_no=scene_no,
            version=1,
            status="pending_review",
            word_count=40 if with_prose else None,
            prose=f"Scene {scene_no} prose. Mara moves through the breach." if with_prose else "",
            prose_source="agent",
        )
        s.add(scene)
        scenes.append(scene)
    await s.flush()
    # schedule_revision now refuses to queue a revision for a scene without an approved contract, so
    # give every member scene a real one: an APPROVED beat backed by an approved ScenePacket.
    for scene in scenes:
        beat = Beat(
            chapter_id=chapter.id,
            scene_no=scene.scene_no,
            status=BeatStatus.APPROVED,
            beat_text=f"Scene {scene.scene_no}: Mara moves through the breach.",
        )
        s.add(beat)
        await s.flush()
        await seed_scene_packet(s, chapter=chapter, beat=beat)
    return book, chapter, run, scenes


async def _chapter_task(s, run, scenes, *, requires_approval: bool = True):
    issues = []
    for scene in scenes:
        issue = Issue(
            production_run_id=run.id,
            chapter_id=run.chapter_id,
            artifact_type="chapter_draft_qa",
            artifact_id=uuid.uuid4(),
            scene_id=scene.id,
            scene_no=scene.scene_no,
            validator="scene_scope",
            issue_kind="scene_scope_bleed",
            severity="repair",
            claim=f"Scene {scene.scene_no} stages a beat it does not own.",
            recommended_action="Cut the leaked beat; only its owning scene may stage it.",
            status=IssueStatus.ACCEPTED,
            payload_json={"signature": f"sig-{scene.scene_no}"},
        )
        s.add(issue)
        issues.append(issue)
    await s.flush()
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        scene_id=None,
        scene_no=None,
        repair_kind="structural_rewrite",
        authority_level=RepairAuthorityLevel.CHAPTER_STRUCTURAL,
        status=RepairTaskStatus.WAITING_FOR_HUMAN if requires_approval else RepairTaskStatus.QUEUED,
        issue_ids=[str(issue.id) for issue in issues],
        instructions="Repair kind: structural_rewrite. Keep each beat in its owning scene.",
        preserve=["Preserve chapter outcome."],
        must_change=[issue.claim for issue in issues],
        must_not_change=["Do not change canon or chapter outcome."],
        allowed_operations=["propose_human_repair"],
        forbidden_operations=["auto_apply"],
        requires_human_approval=requires_approval,
    )
    s.add(task)
    await s.flush()
    return task, issues


async def _events(s, run_id, event_type: str) -> list[AgentEvent]:
    rows = await s.execute(
        select(AgentEvent).where(AgentEvent.production_run_id == run_id, AgentEvent.event_type == event_type)
    )
    return list(rows.scalars().all())


async def test_plain_apply_parks_approval_task_and_explains_why(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _issues = await _chapter_task(s, run, scenes)

        out = await production.apply_repair_task(s, task.id, autonomous=False)

        assert out.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert out.human_approved_at is None
        run = await s.get(ProductionRun, run.id)
        assert run.status == "waiting_for_human"
        # The timeline explains the hold instead of silently parking the task.
        events = await _events(s, run.id, "human_action_required")
        assert events and "Approve & apply" in (events[0].message or "")


async def test_approve_apply_fans_out_one_revision_per_member_scene(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s, scene_count=2)
        task, issues = await _chapter_task(s, run, scenes)

        out = await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)

        assert out.status == RepairTaskStatus.RUNNING
        assert out.human_approved_at is not None
        attempts = (
            (await s.execute(select(RepairAttempt).where(RepairAttempt.repair_task_id == task.id))).scalars().all()
        )
        assert len(attempts) == 2
        assert {a.patch_json["scene_no"] for a in attempts} == {1, 2}
        assert all(
            a.patch_json["base_version"] == 1 and a.patch_json["applied_via"] == "revision_job" for a in attempts
        )
        jobs = (
            (await s.execute(select(Job).where(Job.kind.in_([JobKind.REVISE_FULL, JobKind.REVISE_PASS]))))
            .scalars()
            .all()
        )
        assert len(jobs) == 2
        assert all(job.production_run_id == run.id for job in jobs)
        for issue in issues:
            await s.refresh(issue)
            assert issue.status == IssueStatus.REPAIR_QUEUED
        assert await _events(s, run.id, "repair_task_approved")
        assert await _events(s, run.id, "repair_started")


async def test_approved_once_covers_requeued_attempts(db_factory):
    # Verify can re-queue an approved task (NEEDS_ANOTHER_REPAIR); the original approval covers the
    # whole repair loop, so a plain apply proceeds instead of re-parking.
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _issues = await _chapter_task(s, run, scenes)
        await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)
        task.status = RepairTaskStatus.QUEUED  # as verify's needs_another_repair transition would

        out = await production.apply_repair_task(s, task.id, autonomous=False)

        assert out.status == RepairTaskStatus.RUNNING


async def test_approve_apply_without_target_scenes_is_a_clean_refusal(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s, with_prose=False)
        task, _issues = await _chapter_task(s, run, scenes)

        with pytest.raises(ValueError, match="no concrete target scenes"):
            await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)


async def test_status_guard_rejects_terminal_states(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _issues = await _chapter_task(s, run, scenes)
        task.status = RepairTaskStatus.VERIFIED
        await s.flush()

        with pytest.raises(ValueError, match="only queued or waiting_for_human"):
            await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)


async def test_fanout_verify_names_still_drafting_scenes(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _issues = await _chapter_task(s, run, scenes)
        await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)

        with pytest.raises(ValueError, match="still be"):
            await production.verify_repair_task(s, task.id)


async def test_apply_all_counts_and_schedules_the_shared_drain(db_factory):
    from fastapi import BackgroundTasks

    from dominion.api.routers import production as production_router
    from dominion.workers import background_work

    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        await _chapter_task(s, run, scenes, requires_approval=False)  # eligible: queued, no approval
        await _chapter_task(s, run, scenes, requires_approval=True)  # counted, never auto-applied
        await s.flush()

        background = BackgroundTasks()
        out = await production_router.apply_all_repair_tasks(run.id, s, background)

        assert out.queued == 1
        assert out.requires_approval == 1
        assert out.scheduled is True and out.running is True
        assert any(t.func is background_work.drain_queued_repair_tasks for t in background.tasks)


async def test_fanout_verify_accepts_when_every_scene_revised_clean(db_factory):
    async with db_factory() as s:
        _book, chapter, run, scenes = await _seed(s)
        task, issues = await _chapter_task(s, run, scenes)
        await production.apply_repair_task(s, task.id, autonomous=False, human_approved=True)
        for scene in scenes:  # the revision jobs "land": a newer version per scene, no new critiques
            s.add(
                Scene(
                    chapter_id=chapter.id,
                    scene_no=scene.scene_no,
                    version=2,
                    status="pending_review",
                    word_count=42,
                    prose=f"Scene {scene.scene_no} revised. Each beat stays in its owning scene.",
                    prose_source="agent",
                )
            )
        await s.flush()

        verification = await production.verify_repair_task(s, task.id)

        assert verification.verdict == RepairVerificationVerdict.ACCEPT
        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.VERIFIED
        attempts = (
            (await s.execute(select(RepairAttempt).where(RepairAttempt.repair_task_id == task.id))).scalars().all()
        )
        assert all(a.revised_text and "revised" in a.revised_text for a in attempts)
        for issue in issues:
            await s.refresh(issue)
            assert issue.status == IssueStatus.VERIFIED
