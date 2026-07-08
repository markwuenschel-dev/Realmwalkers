"""Autonomous sweeper: auto-approve within the ceiling, never above it.

The sweeper is what makes stalled runs self-repair — each tick it auto-approves approval-gated repairs
up to a configured authority ceiling and leaves human_required (and above-ceiling) work for a human,
recording every move in the Activity feed. These pin that boundary, which is the core guardrail
against runaway autonomy, plus the cascade delete that clears a run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from dominion.api.routers import activity as activity_router
from dominion.shared.enums import IssueStatus, RepairAuthorityLevel, RepairTaskStatus
from dominion.shared.models import Activity, Book, Chapter, Issue, Job, ProductionRun, RepairTask, Scene
from dominion.workers import production_delete, retention, sweeper


def _cfg(ceiling: str = RepairAuthorityLevel.CHAPTER_STRUCTURAL.value, max_attempts: int = 3) -> sweeper.SweeperConfig:
    return sweeper.SweeperConfig(
        autonomy_enabled=True,
        interval_s=120,
        stale_window_s=0,
        ceiling=ceiling,
        max_attempts=max_attempts,
        retention_days=0,
    )


async def _seed_run(s, *, scene_count: int = 2):
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=5, pov="Mara", title="Signal")
    s.add(chapter)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="repairing")
    s.add(run)
    await s.flush()
    scenes = []
    for n in range(1, scene_count + 1):
        sc = Scene(
            chapter_id=chapter.id,
            scene_no=n,
            version=1,
            status="pending_review",
            word_count=40,
            prose=f"Scene {n} prose. Mara moves through the breach.",
            prose_source="agent",
        )
        s.add(sc)
        scenes.append(sc)
    await s.flush()
    return book, chapter, run, scenes


async def _approval_task(s, run, scenes, *, authority):
    # Issues pre-set to REPAIR_QUEUED so the sweeper's re-triage is a strict no-op (it only re-plans
    # proposed/accepted issues) — the test isolates the auto-approve decision, not triage.
    issues = []
    for sc in scenes:
        iss = Issue(
            production_run_id=run.id,
            chapter_id=run.chapter_id,
            artifact_type="chapter_draft_qa",
            artifact_id=uuid.uuid4(),
            scene_id=sc.id,
            scene_no=sc.scene_no,
            validator="scene_scope",
            issue_kind="scene_scope_bleed",
            severity="repair",
            claim=f"Scene {sc.scene_no} stages a beat it does not own.",
            recommended_action="Cut the leaked beat.",
            status=IssueStatus.REPAIR_QUEUED,
            payload_json={"signature": f"sig-{sc.scene_no}"},
        )
        s.add(iss)
        issues.append(iss)
    await s.flush()
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        scene_id=None,
        scene_no=None,
        repair_kind="structural_rewrite",
        authority_level=authority,
        status=RepairTaskStatus.WAITING_FOR_HUMAN,
        issue_ids=[str(i.id) for i in issues],
        instructions="Keep each beat in its owning scene.",
        requires_human_approval=True,
    )
    s.add(task)
    await s.flush()
    return task


async def test_sweeper_auto_approves_within_ceiling(db_factory):
    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)

        await sweeper._sweep_one_run(s, run.id, _cfg())
        await s.commit()

        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.RUNNING  # auto-approved + applied (fanned out)
        assert task.human_approved_at is not None
        acts = await activity_router.list_activity(s, book_id=book.id)
        assert any(a.kind == "sweeper_repair" and a.source == "sweeper" for a in acts)


async def test_sweeper_gates_human_required_at_default_ceiling(db_factory):
    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.HUMAN_REQUIRED)

        await sweeper._sweep_one_run(s, run.id, _cfg())  # default ceiling = chapter_structural
        await s.commit()

        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.WAITING_FOR_HUMAN  # untouched — above the default ceiling
        assert task.human_approved_at is None
        acts = await activity_router.list_activity(s, book_id=book.id)
        assert any(a.kind == "run_blocked" and a.severity == "warn" for a in acts)
        assert not any(a.kind == "sweeper_repair" for a in acts)


async def test_sweeper_approves_human_required_when_ceiling_raised(db_factory):
    # Raising the ceiling to human_required opts into full autonomy — the sweeper then drives even the
    # highest-authority repairs (the honest-ceiling fix; previously human_required was hard-blocked).
    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.HUMAN_REQUIRED)

        await sweeper._sweep_one_run(s, run.id, _cfg(ceiling=RepairAuthorityLevel.HUMAN_REQUIRED.value))
        await s.commit()

        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.RUNNING
        assert task.human_approved_at is not None
        acts = await activity_router.list_activity(s, book_id=book.id)
        assert any(a.kind == "sweeper_repair" for a in acts)


async def test_sweeper_respects_ceiling_below_task_authority(db_factory):
    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)

        # Ceiling below the task's authority → the sweeper must leave it for a human.
        await sweeper._sweep_one_run(s, run.id, _cfg(ceiling=RepairAuthorityLevel.SCENE_LOCAL.value))
        await s.commit()

        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert task.human_approved_at is None


async def test_delete_production_run_cascades(db_factory):
    from sqlalchemy import func, select

    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed_run(s)
        task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)
        run_id, task_id = run.id, task.id
        await s.commit()

        deleted = await production_delete.delete_production_run(s, run_id)
        await s.commit()

        assert deleted is True
        assert await s.get(ProductionRun, run_id) is None
        assert await s.get(RepairTask, task_id) is None  # child repair task cascaded
        n_issues = await s.scalar(select(func.count()).select_from(Issue).where(Issue.production_run_id == run_id))
        assert n_issues == 0  # child issues cascaded
        # A single "run deleted" marker lands in the feed.
        acts = await activity_router.list_activity(s)
        assert any(a.kind == "run_deleted" for a in acts)


async def test_sweeper_triage_realwork_no_greenlet(db_factory):
    # Repro for the prod greenlet_spawn error: the sweeper's other tests seed issues as REPAIR_QUEUED,
    # which makes triage a no-op — so they never exercise triage doing real work (accept → cluster →
    # create repair task -> support.record_event -> activity mirror). A PROPOSED issue forces that path.
    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        _book, chapter, run, scenes = await _seed_run(s)
        s.add(
            Issue(
                production_run_id=run.id,
                chapter_id=chapter.id,
                artifact_type="chapter_draft_qa",
                artifact_id=uuid.uuid4(),
                scene_id=scenes[0].id,
                scene_no=1,
                validator="scene_scope",
                issue_kind="scene_scope_bleed",
                severity="repair",
                claim="Scene 1 stages a beat it does not own.",
                recommended_action="Cut the leaked beat.",
                status=IssueStatus.PROPOSED,
                payload_json={"signature": "sig-1"},
            )
        )
        await s.flush()

        # Must not raise greenlet_spawn / MissingGreenlet.
        await sweeper._sweep_one_run(s, run.id, _cfg())
        await s.commit()


async def test_sweeper_apply_raises_midmutation_records_blocked_without_greenlet(db_factory, monkeypatch):
    # C1 regression: when apply_repair_task raises AFTER mutating the task inside the sweeper's
    # begin_nested() savepoint, the rollback expires the mutated ORM attributes. The except branch must
    # build its "sweeper_blocked" activity from primitives captured before the savepoint — never by
    # reading the expired ORM object (a sync lazy-load on the async session -> MissingGreenlet).
    # Observed RED at sweeper.py:266 (str(task.id) in the except-ValueError record_activity payload);
    # green after the primitive-capture fix.
    from dominion.workers import production

    sweeper._attempts.clear()
    sweeper._warned_human.clear()
    async with db_factory() as s:
        book, _chapter, run, scenes = await _seed_run(s)
        _task = await _approval_task(s, run, scenes, authority=RepairAuthorityLevel.CHAPTER_STRUCTURAL)

        async def boom(session, task_id, **kwargs):
            # Faithful to apply_repair_task: mutate the live session-identity row, flush it into the
            # savepoint, then raise so the sweeper's savepoint rolls back and expires those attributes.
            t = await session.get(RepairTask, task_id)
            t.human_approved_at = datetime.now(UTC)
            t.status = RepairTaskStatus.RUNNING
            await session.flush()
            raise ValueError("draft the missing scenes first")

        monkeypatch.setattr(production, "apply_repair_task", boom)

        # Must not raise MissingGreenlet / greenlet_spawn.
        await sweeper._sweep_one_run(s, run.id, _cfg())
        await s.commit()

        acts = await activity_router.list_activity(s, book_id=book.id)
        assert any(a.kind == "sweeper_blocked" and a.severity == "warn" for a in acts)


async def test_retention_prunes_aged_exhaust_but_keeps_completed_runs(db_factory):
    old = datetime.now(UTC) - timedelta(days=60)
    async with db_factory() as s:
        book = Book(title="Realmwalkers")
        s.add(book)
        await s.flush()
        chapter = Chapter(book_id=book.id, chapter_no=9, pov="Mara", title="Late")
        s.add(chapter)
        await s.flush()
        cancelled = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="cancelled")
        completed = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="completed")
        s.add_all([cancelled, completed])
        await s.flush()
        s.add(Activity(kind="draft_done", title="old", source="jobs", book_id=book.id, created_at=old))
        s.add(Job(kind="draft", status="done", token_budget=1000, finished_at=old, chapter_id=chapter.id))
        await s.flush()
        # updated_at has onupdate=now(); force both terminal runs to look aged.
        await s.execute(
            update(ProductionRun).where(ProductionRun.id.in_([cancelled.id, completed.id])).values(updated_at=old)
        )
        await s.commit()

        counts = await retention.run_retention(s, days=30)
        await s.commit()

        assert counts["activities"] >= 1 and counts["jobs"] >= 1 and counts["runs"] >= 1
        assert await s.get(ProductionRun, cancelled.id) is None  # abandoned run pruned
        assert await s.get(ProductionRun, completed.id) is not None  # completed run KEPT (manual-only)
