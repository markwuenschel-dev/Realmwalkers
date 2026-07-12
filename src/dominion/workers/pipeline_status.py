"""Live pipeline status — one fan-out of reads describing everything the production pipeline is doing.

The system records rich, structured state everywhere (jobs, production runs, repair tasks, issues,
agent events, the sweeper heartbeat) and historically surfaced almost none of it. This module gathers
it into a single `PipelineStatusOut` for the Desk's Pipeline dashboard: what's running NOW, what's
QUEUED (and that it runs one-at-a-time), what's WAITING on a human (and exactly why), what's BLOCKED
(and how to unblock it), what's COMPLETED, and whether the autonomous SWEEPER is alive.

Every human-facing `reason`/`suggested_action` string is pre-computed here (server-side) so the
frontend stays thin. The pipeline never assigns blocked/failed/rejected/cancelled to a run/task —
parking is always `waiting_for_human` + a stage string + an event reason — so we don't model those.

All reads are best-effort snapshots. Live phase/elapsed/cache for a RUNNING job come from the
in-process `progress` registry and are absent when a non-drain process serves the request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    AgentRunStatus,
    IssueStatus,
    JobStatus,
    ProductionRunStatus,
    RepairTaskStatus,
)
from dominion.shared.job_policy import scope_jobs_to_book
from dominion.shared.models import (
    AgentEvent,
    AgentRun,
    Artifact,
    Chapter,
    ChapterSequence,
    DraftRunTimeline,
    Issue,
    Job,
    ProductionRun,
    RepairTask,
)
from dominion.shared.schemas import (
    PipelineAgentRunOut,
    PipelineBlockedOut,
    PipelineCompletedOut,
    PipelineCompletedRef,
    PipelineIssueRef,
    PipelineJobOut,
    PipelineNowOut,
    PipelineQueueOut,
    PipelineRepairTaskRef,
    PipelineRunRef,
    PipelineStatusOut,
    PipelineWaitingOut,
    SweeperStatusOut,
)
from dominion.workers import background_work, production, progress, run_stages, sweeper

# Stages a run parks in that mean "stuck on a fault", not "waiting on plain review" — these route to
# the Blocked section (with an unblock action) rather than Waiting-on-you.
_BLOCKED_STAGES = {
    run_stages.STAGE_STRUCTURAL_REPAIR_REQUIRED,  # "structural_repair_required"
    run_stages.STAGE_PROVIDER_RATE_LIMITED,  # "provider_rate_limited"
    "timeline_failed",
}

# stage -> (default_reason, action_label, action_kind) for a parked run. The default reason is used
# only when the latest AgentEvent for the run carries no message of its own.
_STAGE_ACTION: dict[str, tuple[str, str, str]] = {
    "structural_repair_required": (
        "Chapter QA found structural blocking issues — prose repair is gated until the structure is fixed.",
        "Align plan to seeded scenes",
        "align_scene_count",
    ),
    "provider_rate_limited": (
        "The model provider rate-limited (429) past automatic retries — transient; retry when it clears.",
        "Retry drafting",
        "retry",
    ),
    "timeline_failed": (
        "The drafting timeline failed to advance — the run needs your review.",
        "Resume",
        "resume",
    ),
    "waiting_for_scene_drafts": (
        "Some sequence scenes still have no prose — assembly is waiting on their drafts.",
        "Draft missing scenes",
        "draft_missing",
    ),
}

# stage -> human label for a run that is actively RUNNING/REPAIRING right now.
_NOW_STAGE_LABEL = {
    "drafting_scenes": "Drafting scenes",
    "scene_qa": "Reviewing drafted scenes",
    "assembling_chapter": "Assembling the chapter",
    "chapter_qa": "Running chapter QA",
    "repair_execution": "Applying a repair",
    "repair_verification": "Verifying a repair",
}

_COMPLETED_LIMIT = 12  # newest completed runs to surface


def _repair_reason_action(task: RepairTask) -> tuple[str, str, str]:
    """Pre-computed (reason, suggested_action, action_kind) for a queued/parked repair task."""
    status = str(task.status)
    if status == RepairTaskStatus.QUEUED and task.requires_human_approval:
        return ("This repair needs your approval before it runs.", "Approve & apply", "approve_apply")
    if task.human_approved_at is not None:
        return ("The revision has been applied — verify it resolved the issue.", "Verify", "verify")
    if task.requires_human_approval:
        return ("This repair needs your approval before it runs.", "Approve & apply", "approve_apply")
    return ("This repair was parked for your review.", "Verify", "verify")


def _issue_reason(status: str) -> str:
    if status == IssueStatus.ESCALATED:
        return "This finding was escalated for your judgment."
    return "This finding needs a decision — accept it into a repair or reject it."


async def build_pipeline_status(session: AsyncSession, book_id: uuid.UUID) -> PipelineStatusOut:
    """Fan out one snapshot of the whole pipeline for a book (caller has already 404'd a missing book)."""
    now = datetime.now(UTC)

    runs = await production.list_book_production_runs(session, book_id)
    all_run_ids = [run.id for run in runs]

    # chapter_no lookup for every chapter in the book (labels the refs).
    chapter_no: dict[uuid.UUID, int] = {
        cid: no
        for cid, no in (
            await session.execute(select(Chapter.id, Chapter.chapter_no).where(Chapter.book_id == book_id))
        ).all()
    }

    # Book-scope a Job query via the single-key shared helper (ADR 0027).
    def _scope(stmt):
        return scope_jobs_to_book(stmt, book_id)

    # --- NOW: running jobs (+ live phase/elapsed/cache), running agent runs -----------------------
    running_rows = (
        await session.execute(
            _scope(
                select(Job.id, Job.kind, Job.chapter_no, Job.scene_no, Job.claimed_at).where(
                    Job.status == JobStatus.RUNNING
                )
            )
            .order_by(Job.claimed_at.desc())
            .limit(10)
        )
    ).all()
    now_jobs: list[PipelineJobOut] = []
    for jid, kind, ch, sc, claimed in running_rows:
        phase, elapsed_s = progress.get(str(jid))
        cache = progress.get_cache_stats(str(jid))
        now_jobs.append(
            PipelineJobOut(
                id=jid,
                kind=kind,
                status="running",
                chapter_no=ch,
                scene_no=sc,
                phase=phase,
                elapsed_s=elapsed_s,
                cache_hit_ratio=cache["cache_hit_ratio"] if cache else None,
                claimed_at=claimed,
            )
        )

    now_agent_runs: list[PipelineAgentRunOut] = []
    if all_run_ids:
        ar_rows = (
            await session.execute(
                select(
                    AgentRun.id,
                    AgentRun.production_run_id,
                    AgentRun.agent_name,
                    AgentRun.stage,
                    AgentRun.started_at,
                )
                .where(
                    AgentRun.production_run_id.in_(all_run_ids),
                    AgentRun.status == AgentRunStatus.RUNNING,
                )
                .order_by(AgentRun.started_at.desc())
                .limit(10)
            )
        ).all()
        now_agent_runs = [
            PipelineAgentRunOut(id=aid, production_run_id=prid, agent_name=an, stage=st, started_at=start)
            for aid, prid, an, st, start in ar_rows
        ]

    # --- QUEUE: ordered queued jobs, queued depth, failed jobs ------------------------------------
    queued_rows = (
        await session.execute(
            _scope(
                select(Job.id, Job.kind, Job.chapter_no, Job.scene_no, Job.created_at).where(
                    Job.status == JobStatus.QUEUED
                )
            )
            .order_by(Job.created_at)
            .limit(50)
        )
    ).all()
    queue_jobs = [
        PipelineJobOut(
            id=jid, kind=kind, status="queued", chapter_no=ch, scene_no=sc, position=i + 1, created_at=created
        )
        for i, (jid, kind, ch, sc, created) in enumerate(queued_rows)
    ]
    jobs_queued = int(
        (
            await session.execute(_scope(select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED)))
        ).scalar_one()
    )
    failed_rows = (
        await session.execute(
            _scope(
                select(Job.id, Job.kind, Job.chapter_no, Job.scene_no, Job.last_error).where(
                    Job.status == JobStatus.FAILED
                )
            )
            .order_by(Job.chapter_no, Job.scene_no)
            .limit(50)
        )
    ).all()
    failed_jobs = [
        PipelineJobOut(id=jid, kind=kind, status="failed", chapter_no=ch, scene_no=sc, last_error=err)
        for jid, kind, ch, sc, err in failed_rows
    ]

    # --- drafting progress inputs (timelines + latest sequence per chapter) ------------------------
    timelines: dict[uuid.UUID, DraftRunTimeline] = {}
    if all_run_ids:
        for tl in (
            (await session.execute(select(DraftRunTimeline).where(DraftRunTimeline.production_run_id.in_(all_run_ids))))
            .scalars()
            .all()
        ):
            timelines[tl.production_run_id] = tl
    sequences: dict[uuid.UUID, ChapterSequence] = {}
    seq_chapter_ids = {run.chapter_id for run in runs}
    if seq_chapter_ids:
        for seq in (
            (
                await session.execute(
                    select(ChapterSequence)
                    .where(ChapterSequence.chapter_id.in_(seq_chapter_ids))
                    .order_by(ChapterSequence.chapter_id, ChapterSequence.created_at.desc())
                )
            )
            .scalars()
            .all()
        ):
            sequences.setdefault(seq.chapter_id, seq)

    def _progress(run: ProductionRun) -> tuple[int | None, int | None]:
        tl = timelines.get(run.id)
        seq = sequences.get(run.chapter_id)
        drafted = len(tl.drafted_scenes or []) if tl is not None else None
        expected = len(run_stages.expected_scene_nos(seq.body)) if seq is not None else None
        return drafted, expected

    # --- categorize production runs ---------------------------------------------------------------
    now_runs: list[ProductionRun] = []
    queued_runs: list[ProductionRun] = []
    waiting_runs: list[ProductionRun] = []
    blocked_runs: list[ProductionRun] = []
    completed_runs: list[ProductionRun] = []
    for run in runs:
        status = str(run.status)
        stage = run.current_stage or ""
        if status == ProductionRunStatus.COMPLETED or stage == "final_ready":
            completed_runs.append(run)
        elif status in (ProductionRunStatus.RUNNING, ProductionRunStatus.REPAIRING):
            now_runs.append(run)
        elif status == ProductionRunStatus.QUEUED:
            queued_runs.append(run)
        elif stage in _BLOCKED_STAGES:
            blocked_runs.append(run)
        elif status == ProductionRunStatus.WAITING_FOR_HUMAN:
            waiting_runs.append(run)
        # else: a stray failed/cancelled/blocked run — the pipeline never parks there; not surfaced.

    # Latest AgentEvent per parked run — its `message` is the real, recorded reason the run parked.
    parked_ids = [run.id for run in waiting_runs] + [run.id for run in blocked_runs]
    latest_events: dict[uuid.UUID, AgentEvent] = {}
    if parked_ids:
        for ev in (
            (
                await session.execute(
                    select(AgentEvent)
                    .where(AgentEvent.production_run_id.in_(parked_ids))
                    .order_by(AgentEvent.production_run_id, AgentEvent.created_at.desc())
                    .distinct(AgentEvent.production_run_id)
                )
            )
            .scalars()
            .all()
        ):
            latest_events[ev.production_run_id] = ev

    def _parked_ref(run: ProductionRun) -> PipelineRunRef:
        stage = run.current_stage or ""
        default, label, kind = _STAGE_ACTION.get(
            stage, ("This run is waiting for you to review and resume.", "Resume", "resume")
        )
        ev = latest_events.get(run.id)
        reason = (ev.message if ev is not None and ev.message else None) or default
        drafted, expected = _progress(run)
        return PipelineRunRef(
            run_id=run.id,
            chapter_id=run.chapter_id,
            chapter_no=chapter_no.get(run.chapter_id),
            status=str(run.status),
            current_stage=run.current_stage,
            updated_at=run.updated_at,
            reason=reason,
            suggested_action=label,
            action_kind=kind,
            scenes_drafted=drafted,
            scenes_expected=expected,
        )

    def _now_ref(run: ProductionRun) -> PipelineRunRef:
        stage = run.current_stage or ""
        label = _NOW_STAGE_LABEL.get(stage) or (stage.replace("_", " ").capitalize() if stage else "Running")
        drafted, expected = _progress(run)
        return PipelineRunRef(
            run_id=run.id,
            chapter_id=run.chapter_id,
            chapter_no=chapter_no.get(run.chapter_id),
            status=str(run.status),
            current_stage=run.current_stage,
            updated_at=run.updated_at,
            reason=label,
            suggested_action=None,
            action_kind="none",
            scenes_drafted=drafted,
            scenes_expected=expected,
        )

    def _queued_ref(run: ProductionRun) -> PipelineRunRef:
        return PipelineRunRef(
            run_id=run.id,
            chapter_id=run.chapter_id,
            chapter_no=chapter_no.get(run.chapter_id),
            status=str(run.status),
            current_stage=run.current_stage,
            updated_at=run.updated_at,
            reason="Queued — will start when the run ahead of it finishes.",
            suggested_action=None,
            action_kind="none",
        )

    # --- COMPLETED: final-chapter status (latest final_chapter artifact per run) -------------------
    completed_runs = completed_runs[:_COMPLETED_LIMIT]
    final_status: dict[uuid.UUID, str | None] = {}
    completed_ids = [run.id for run in completed_runs]
    if completed_ids:
        for art in (
            (
                await session.execute(
                    select(Artifact)
                    .where(Artifact.production_run_id.in_(completed_ids), Artifact.artifact_type == "final_chapter")
                    .order_by(Artifact.production_run_id, Artifact.version.desc(), Artifact.created_at.desc())
                    .distinct(Artifact.production_run_id)
                )
            )
            .scalars()
            .all()
        ):
            if art.production_run_id is None:
                continue
            body = art.body if isinstance(art.body, dict) else {}
            final_status[art.production_run_id] = body.get("final_chapter_status")

    def _completed_ref(run: ProductionRun) -> PipelineCompletedRef:
        drafted, expected = _progress(run)
        return PipelineCompletedRef(
            run_id=run.id,
            chapter_id=run.chapter_id,
            chapter_no=chapter_no.get(run.chapter_id),
            status=str(run.status),
            current_stage=run.current_stage,
            updated_at=run.updated_at,
            final_chapter_status=final_status.get(run.id),
            scenes_drafted=drafted,
            scenes_expected=expected,
        )

    # --- repair tasks (queue auto/approval + waiting) ---------------------------------------------
    repair_rows: list[RepairTask] = []
    if all_run_ids:
        repair_rows = list(
            (
                await session.execute(
                    select(RepairTask)
                    .where(
                        RepairTask.production_run_id.in_(all_run_ids),
                        RepairTask.status.in_((RepairTaskStatus.QUEUED, RepairTaskStatus.WAITING_FOR_HUMAN)),
                    )
                    .order_by(RepairTask.created_at)
                )
            )
            .scalars()
            .all()
        )

    def _repair_ref(task: RepairTask) -> PipelineRepairTaskRef:
        reason, label, kind = _repair_reason_action(task)
        return PipelineRepairTaskRef(
            task_id=task.id,
            production_run_id=task.production_run_id,
            chapter_id=task.chapter_id,
            chapter_no=chapter_no.get(task.chapter_id),
            scene_no=task.scene_no,
            repair_kind=task.repair_kind,
            authority_level=task.authority_level,
            status=str(task.status),
            requires_human_approval=task.requires_human_approval,
            reason=reason,
            suggested_action=label,
            action_kind=kind,
        )

    queue_auto = [
        _repair_ref(t)
        for t in repair_rows
        if str(t.status) == RepairTaskStatus.QUEUED and not t.requires_human_approval
    ]
    queue_approval = [
        _repair_ref(t) for t in repair_rows if str(t.status) == RepairTaskStatus.QUEUED and t.requires_human_approval
    ]
    waiting_tasks = [
        _repair_ref(t)
        for t in repair_rows
        if str(t.status) == RepairTaskStatus.WAITING_FOR_HUMAN
        or (t.requires_human_approval and str(t.status) == RepairTaskStatus.QUEUED)
    ]

    # --- issues waiting on a triage decision ------------------------------------------------------
    waiting_issues: list[PipelineIssueRef] = []
    if all_run_ids:
        for issue in (
            (
                await session.execute(
                    select(Issue)
                    .where(
                        Issue.production_run_id.in_(all_run_ids),
                        Issue.status.in_((IssueStatus.PROPOSED, IssueStatus.ESCALATED)),
                    )
                    .order_by(Issue.created_at)
                    .limit(50)
                )
            )
            .scalars()
            .all()
        ):
            waiting_issues.append(
                PipelineIssueRef(
                    issue_id=issue.id,
                    production_run_id=issue.production_run_id,
                    chapter_id=issue.chapter_id,
                    chapter_no=chapter_no.get(issue.chapter_id),
                    scene_no=issue.scene_no,
                    issue_kind=issue.issue_kind,
                    severity=issue.severity,
                    status=str(issue.status),
                    claim=issue.claim,
                    reason=_issue_reason(str(issue.status)),
                    suggested_action="Decide",
                    action_kind="decide_issue",
                )
            )

    sweeper_out = SweeperStatusOut(**await sweeper.sweeper_status(session))
    queue_paused = background_work.queue_paused()

    return PipelineStatusOut(
        book_id=book_id,
        generated_at=now,
        now=PipelineNowOut(
            jobs=now_jobs,
            agent_runs=now_agent_runs,
            runs=[_now_ref(r) for r in now_runs],
            drain_locked=background_work.drain_locked(),
            repair_drain_locked=background_work.repair_drain_locked(),
        ),
        queue=PipelineQueueOut(
            queue_paused=queue_paused,
            jobs_queued=jobs_queued,
            jobs=queue_jobs,
            repair_tasks_auto=queue_auto,
            repair_tasks_approval=queue_approval,
            runs_queued=[_queued_ref(r) for r in queued_runs],
        ),
        waiting_on_human=PipelineWaitingOut(
            runs=[_parked_ref(r) for r in waiting_runs],
            repair_tasks=waiting_tasks,
            issues=waiting_issues,
        ),
        blocked=PipelineBlockedOut(
            runs=[_parked_ref(r) for r in blocked_runs],
            failed_jobs=failed_jobs,
            queue_paused=queue_paused,
        ),
        completed=PipelineCompletedOut(runs=[_completed_ref(r) for r in completed_runs]),
        sweeper=sweeper_out,
    )
