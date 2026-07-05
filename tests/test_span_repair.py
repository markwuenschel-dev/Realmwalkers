"""D1 regression: SPAN_ONLY repairs must queue a REAL revision job, not an inline no-op patch.

Span-only repair tasks used to be "applied" by ``_apply_real_span_patch``, which wrote the target
span back UNCHANGED. ``verify_repair_task``'s acceptance requires ``span_changed or quote_changed``,
which was therefore always False, so the verdict was NEEDS_ANOTHER_REPAIR, the task went back to
QUEUED, and the drain re-applied the same no-op forever (queued->running->queued churn while doing
nothing). The fix routes span-only tasks through the same ``schedule_revision`` path the non-span
single-scene branch uses, so an actual LLM revision changes the scene and verify can ACCEPT honestly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared.enums import (
    IssueStatus,
    JobKind,
    RepairAuthorityLevel,
    RepairTaskStatus,
    RepairVerificationVerdict,
)
from dominion.shared.models import (
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

QUOTE = "Mara moves through the breach"


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
        word_count=8,
        prose=f"{QUOTE}. The signal fire gutters low.",
        prose_source="agent",
    )
    s.add(scene)
    await s.flush()
    return book, chapter, run, scene


async def _span_task(s, run, scene):
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
        quote=QUOTE,
        claim="This line reads flat; sharpen the sensory beat.",
        recommended_action="Rework the flagged span.",
        status=IssueStatus.ACCEPTED,
        payload_json={"signature": "sig-span-1"},
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
        status=RepairTaskStatus.QUEUED,
        issue_ids=[str(issue.id)],
        target_spans={"items": [{"quote": QUOTE}]},
        instructions="Repair kind: expand. Rework the flagged span with a sharper sensory beat.",
        preserve=["Preserve scene outcome."],
        must_change=[QUOTE],
        allowed_operations=["replace_span", "rewrite_scene"],
        forbidden_operations=["change_canon", "change_chapter_outcome"],
        requires_human_approval=False,
    )
    s.add(task)
    await s.flush()
    return task, issue


async def test_span_only_apply_queues_revision_job_not_inline_noop(db_factory):
    # The bug: apply used to mint a no-op scene version inline. Now it must queue a real revision job.
    async with db_factory() as s:
        _book, _chapter, run, scene = await _seed_scene(s)
        task, issue = await _span_task(s, run, scene)

        out = await production.apply_repair_task(s, task.id)

        # In flight against a queued revision job — not marked done off an inline no-op patch.
        assert out.status == RepairTaskStatus.RUNNING
        run = await s.get(ProductionRun, run.id)
        assert run.status == "repairing"

        # A real revision Job was queued for the target scene ...
        jobs = (
            (await s.execute(select(Job).where(Job.kind.in_([JobKind.REVISE_FULL, JobKind.REVISE_PASS]))))
            .scalars()
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].target_scene_id == scene.id
        assert jobs[0].production_run_id == run.id

        # ... and apply did NOT patch the scene inline: no new scene version was minted by apply itself.
        scene_versions = (
            (await s.execute(select(Scene).where(Scene.chapter_id == run.chapter_id, Scene.scene_no == 1)))
            .scalars()
            .all()
        )
        assert sorted(sc.version for sc in scene_versions) == [1]

        # The attempt records the queued revision (verify fills in revised_text once it lands).
        attempts = (
            (await s.execute(select(RepairAttempt).where(RepairAttempt.repair_task_id == task.id))).scalars().all()
        )
        assert len(attempts) == 1
        assert attempts[0].patch_json["applied_via"] == "revision_job"
        assert attempts[0].revised_text is None

        await s.refresh(issue)
        assert issue.status == IssueStatus.REPAIR_QUEUED


async def test_span_only_verify_accepts_after_revised_scene_lands(db_factory):
    # With a genuinely changed scene from the revision job, verify ACCEPTs instead of looping.
    async with db_factory() as s:
        _book, chapter, run, scene = await _seed_scene(s)
        task, issue = await _span_task(s, run, scene)
        await production.apply_repair_task(s, task.id)

        # The queued revision job "lands": a newer scene version whose prose reworks the flagged span
        # (the quote is gone), with no new critiques on it.
        s.add(
            Scene(
                chapter_id=chapter.id,
                scene_no=1,
                version=2,
                status="pending_review",
                word_count=14,
                prose="Mara slips between the breach's seams, ash stinging her throat. The signal fire gutters low.",
                prose_source="agent",
            )
        )
        await s.flush()

        verification = await production.verify_repair_task(s, task.id)

        assert verification.verdict == RepairVerificationVerdict.ACCEPT
        task = await s.get(RepairTask, task.id)
        assert task.status == RepairTaskStatus.VERIFIED
        await s.refresh(issue)
        assert issue.status == IssueStatus.VERIFIED
