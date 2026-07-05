"""Hard-delete a production run and all of its children, in strict FK order.

No FK in this schema declares ON DELETE CASCADE, so a run can't be removed without deleting its
descendants first. This mirrors api/telemetry_delete.py / scene_delete.py but lives in workers/ so the
router AND the retention sweep (workers/retention.py) can both call it without an api→worker import.

Order matters — a row may not be deleted while another still references it. The reference graph under
a production run:
    production_run ← agent_runs, artifacts, agent_events, issues, repair_tasks, draft_run_timelines, jobs*
    agent_runs     ← artifacts.created_by, agent_events.agent_run, issue_decisions.agent_run,
                     repair_attempts.agent_run, repair_verifications.agent_run
    artifacts      ← artifact_dependencies (both endpoints)
    issues         ← issue_decisions
    repair_tasks   ← repair_attempts ← repair_verifications
(* jobs.production_run_id is nullable — detach, don't delete: the drafted scenes outlive the run.)
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import (
    Activity,
    AgentEvent,
    AgentRun,
    Artifact,
    ArtifactDependency,
    DraftRunTimeline,
    Issue,
    IssueDecision,
    Job,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
)
from dominion.workers import activity


async def delete_production_run(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """Delete one run + all children. Returns False if the run doesn't exist (caller maps to 404).
    Does not commit — the caller's transaction does, so a bulk clear is one atomic unit."""
    run = await session.get(ProductionRun, run_id)
    if run is None:
        return False
    book_id, chapter_id = run.book_id, run.chapter_id

    artifact_ids = select(Artifact.id).where(Artifact.production_run_id == run_id)
    issue_ids = select(Issue.id).where(Issue.production_run_id == run_id)
    task_ids = select(RepairTask.id).where(RepairTask.production_run_id == run_id)
    attempt_ids = select(RepairAttempt.id).where(RepairAttempt.repair_task_id.in_(task_ids))

    # Deepest children first.
    await session.execute(delete(RepairVerification).where(RepairVerification.repair_attempt_id.in_(attempt_ids)))
    await session.execute(delete(RepairAttempt).where(RepairAttempt.repair_task_id.in_(task_ids)))
    await session.execute(delete(ArtifactDependency).where(ArtifactDependency.artifact_id.in_(artifact_ids)))
    await session.execute(delete(ArtifactDependency).where(ArtifactDependency.depends_on_artifact_id.in_(artifact_ids)))
    await session.execute(delete(IssueDecision).where(IssueDecision.issue_id.in_(issue_ids)))
    # Everything that references agent_runs is now gone → agent_events/artifacts can go, then agent_runs.
    await session.execute(delete(AgentEvent).where(AgentEvent.production_run_id == run_id))
    await session.execute(delete(Artifact).where(Artifact.production_run_id == run_id))
    await session.execute(delete(RepairTask).where(RepairTask.production_run_id == run_id))
    await session.execute(delete(Issue).where(Issue.production_run_id == run_id))
    await session.execute(delete(DraftRunTimeline).where(DraftRunTimeline.production_run_id == run_id))
    await session.execute(delete(AgentRun).where(AgentRun.production_run_id == run_id))
    # Detach draft/revision jobs (their scenes persist) rather than deleting job history.
    await session.execute(update(Job).where(Job.production_run_id == run_id).values(production_run_id=None))
    # Drop this run's granular activity rows, then leave a single "run deleted" marker in the feed.
    await session.execute(delete(Activity).where(Activity.production_run_id == run_id))
    await activity.record_activity(
        session,
        kind="run_deleted",
        title="Production run deleted",
        source="production",
        severity="info",
        book_id=book_id,
        chapter_id=chapter_id,
        production_run_id=run_id,
    )
    await session.execute(delete(ProductionRun).where(ProductionRun.id == run_id))
    return True
