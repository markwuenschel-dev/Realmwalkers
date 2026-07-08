"""N1 red-capable coverage: RepairTask mutation endpoints must return a well-formed RepairTaskOut on
the success path, not a `MissingGreenlet` 500.

apply / approve-apply / reject / rollback each transition the task's status, commit, then serialize via
`RepairTaskOut.model_validate(task)`. Without a post-commit `session.refresh(task)` the server-side
`updated_at` (onupdate) is expired at flush and the serialize triggers a sync lazy-load on the async
session → MissingGreenlet. Red on the unpatched routers, green with the refresh.

`/verify` is intentionally NOT covered here: it serializes a freshly-INSERTed RepairVerification (INSERT
server-defaults return via RETURNING → loaded/safe), not the mutated RepairTask.

See docs/plans/n1-greenlet-enrich-after-commit-contract.md (candidate N1).
"""

from __future__ import annotations

from fastapi import BackgroundTasks

# Reuse the existing repair-task seeders — no parallel harness.
from test_repair_tasks import _chapter_task, _seed  # noqa: E402

from dominion.api.routers import production as production_router
from dominion.shared.enums import RepairAuthorityLevel, RepairTaskStatus
from dominion.shared.models import RepairTask, Scene
from dominion.shared.schemas import RepairTaskOut


def _assert_task_out(out) -> None:
    assert isinstance(out, RepairTaskOut)
    assert out.updated_at is not None  # the column that greenlet-500s when unrefreshed


async def test_apply_repair_task_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        # QUEUED + no approval gate → apply fans out and transitions the task to RUNNING (a real UPDATE).
        task, _issues = await _chapter_task(s, run, scenes, requires_approval=False)
        out = await production_router.apply_repair_task(task.id, s, BackgroundTasks())
        _assert_task_out(out)


async def test_approve_and_apply_repair_task_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        task, _issues = await _chapter_task(s, run, scenes, requires_approval=True)
        out = await production_router.approve_and_apply_repair_task(task.id, s, BackgroundTasks())
        _assert_task_out(out)


async def test_reject_repair_task_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, scenes = await _seed(s)
        # QUEUED → reject transitions the task to WAITING_FOR_HUMAN (a real UPDATE on the task row).
        task, _issues = await _chapter_task(s, run, scenes, requires_approval=False)
        out = await production_router.reject_repair_task(task.id, s)
        _assert_task_out(out)


async def test_rollback_repair_task_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, chapter, run, scenes = await _seed(s)
        base = scenes[0]
        # A landed revision to roll back: a newer version of the same scene.
        revised = Scene(
            chapter_id=chapter.id,
            scene_no=base.scene_no,
            version=2,
            status="pending_review",
            word_count=42,
            prose="Scene 1 revised. Mara moves through the breach.",
            prose_source="agent",
        )
        s.add(revised)
        await s.flush()
        # Scene-targeted, QUEUED task → rollback supersedes the revision and transitions the task.
        task = RepairTask(
            production_run_id=run.id,
            chapter_id=chapter.id,
            scene_id=base.id,
            scene_no=base.scene_no,
            repair_kind="span_patch",
            authority_level=RepairAuthorityLevel.SCENE_LOCAL,
            status=RepairTaskStatus.QUEUED,
            issue_ids=[],
            instructions="Roll back the latest revision.",
        )
        s.add(task)
        await s.flush()

        out = await production_router.rollback_repair_task(task.id, s)
        _assert_task_out(out)
