"""Production-run repair orchestration lane.

This module is implementation detail behind ``dominion.workers.production``. It owns issue
triage, issue decisions, repair task creation, repair application, verification, rejection, and
rollback while the public Production Run Facade keeps the external method surface stable.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    AgentRunStatus,
    ArtifactType,
    Decision,
    IssueDecisionKind,
    IssueStatus,
    ProductionRunStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
    RepairVerificationVerdict,
    SceneStatus,
    is_manual_grant,
)
from dominion.shared.models import (
    Approval,
    Artifact,
    Critique,
    DraftAttempt,
    Issue,
    IssueDecision,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
    Scene,
    ScenePacket,
)
from dominion.shared.severity import is_blocking
from dominion.workers import production_support as support
from dominion.workers import repair_triage
from dominion.workers.beat_preservation import (
    SCENE_BREAK,
    beats_preserved,
    ordered_unique,
    required_beats_for_scene,
    required_beats_for_scenes,
)
from dominion.workers.job_scheduler import schedule_revision
from dominion.workers.scene_fidelity.contract import fidelity_contract_fingerprint
from dominion.workers.scene_fidelity.models import ClauseResult, SceneFidelityReport, is_fidelity_active
from dominion.workers.scene_fidelity.payloads import CritiqueProjection, TriageResult
from dominion.workers.scene_fidelity.policy import (
    policy_outcome_for_clause_evaluation,
    project_report_to_critiques,
    report_is_current,
)
from dominion.workers.scene_fidelity.repair_preview import REPAIR_PREVIEW_ARTIFACT_TYPE, build_preview_body

# L6 (run orchestration): pure stage machine -- pinned stage strings + deterministic gates that must
# fail BEFORE any LLM spend. Persistence stays here; decisions live in run_stages (DB-free, tested).
from dominion.workers import run_stages  # isort: skip

# The SceneFidelity report Artifact type, kept as a literal here to avoid importing the evaluator (which
# pulls the LLM stack into this early-imported production module).
_FIDELITY_REPORT_TYPE = ArtifactType.SCENE_FIDELITY_REPORT.value


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:
    from dominion.workers import production_sequence

    return await production_sequence._latest_scene_map(session, chapter_id)


async def assemble_run(session: AsyncSession, run: ProductionRun) -> None:
    from dominion.workers import production_sequence

    return await production_sequence.assemble_run(session, run)


_AUTHORITY_RANK = {
    RepairAuthorityLevel.SPAN_ONLY: 0,
    RepairAuthorityLevel.SCENE_LOCAL: 1,
    RepairAuthorityLevel.SCENE_STRUCTURAL: 2,
    RepairAuthorityLevel.CROSS_SCENE: 3,
    RepairAuthorityLevel.CHAPTER_STRUCTURAL: 4,
    RepairAuthorityLevel.HUMAN_REQUIRED: 5,
}

# Repair statuses that count as an OPEN (in-flight) repair: one that could still mutate a scene span,
# so a new overlapping repair must not auto-apply on top of it (it parks for a human instead). Keyed to
# real RepairTaskStatus members -- the conflict query in apply_repair_task previously filtered on the
# literal "repair_queued", which is an IssueStatus value, NOT a RepairTaskStatus, so that arm was
# silently dead; and it omitted waiting_for_human -- an open repair everywhere else (see
# pipeline_status) and the exact status this gate assigns to a parked conflict.
_OPEN_REPAIR_STATUSES: tuple[RepairTaskStatus, ...] = (
    RepairTaskStatus.QUEUED,
    RepairTaskStatus.RUNNING,
    RepairTaskStatus.WAITING_FOR_HUMAN,
)
# Terminal repair statuses: the repair is settled and can no longer change a span, so it never blocks
# a new overlapping repair. Together with _OPEN_REPAIR_STATUSES this must partition RepairTaskStatus
# (enforced by tests/test_repair_conflict_status.py) so a future status can't silently belong to
# neither set and fall through the conflict gate.
_TERMINAL_REPAIR_STATUSES: tuple[RepairTaskStatus, ...] = (
    RepairTaskStatus.VERIFIED,
    RepairTaskStatus.REJECTED,
    RepairTaskStatus.FAILED,
    RepairTaskStatus.CANCELLED,
)


@dataclass(frozen=True)
class RepairTarget:
    """Normalized representation of a span/quote target for repair.

    Unifies the "items" shape produced by triage and any legacy flat shapes.
    Used by conflict detection, patching, and verification.
    """

    quote: str | None = None
    span_start: int | None = None
    span_end: int | None = None


def _normalized_repair_targets(task: RepairTask, issues: list[Issue] | None = None) -> list[RepairTarget]:
    """Single source of truth for extracting repair targets from task + issues."""
    targets: list[RepairTarget] = []
    ts = task.target_spans or {}

    if isinstance(ts, dict):
        items = ts.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    q = item.get("quote") if isinstance(item.get("quote"), str) else None
                    ss = item.get("span_start")
                    se = item.get("span_end")
                    if q or ss is not None or se is not None:
                        targets.append(RepairTarget(quote=q, span_start=ss, span_end=se))
        else:
            # legacy flat support: e.g. {"quote": , 0: [s,e] , ... }
            q = ts.get("quote") if isinstance(ts.get("quote"), str) else None
            for v in ts.values():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    try:
                        ss, se = int(v[0]), int(v[1])
                        targets.append(RepairTarget(quote=q, span_start=ss, span_end=se))
                    except (ValueError, TypeError):
                        pass
            if q and not any(t.quote for t in targets):
                targets.append(RepairTarget(quote=q))

    if not targets and issues:
        for issue in issues:
            if issue.quote or issue.span_start is not None or issue.span_end is not None:
                targets.append(
                    RepairTarget(
                        quote=issue.quote,
                        span_start=issue.span_start,
                        span_end=issue.span_end,
                    )
                )

    return targets


def _targets_overlap(a: RepairTarget, b: RepairTarget) -> bool:
    """Simple overlap for span or exact quote match."""
    if a.quote and b.quote and a.quote == b.quote:
        return True
    if a.span_start is not None and a.span_end is not None and b.span_start is not None and b.span_end is not None:
        return not (a.span_end <= b.span_start or b.span_end <= a.span_start)
    return False


def _infer_repair_kind(issue: Issue) -> str:
    if issue.issue_kind in {"length", "budget"} or issue.validator == "length":
        return "word_budget"
    if issue.validator == "dialogue":
        return "dialogue"
    if issue.validator == "sensory":
        return "expand"
    if issue.validator == "combat":
        return "combat"
    if issue.validator in {"continuity", "state_drift"}:
        return "continuity"
    if issue.validator == "voice":
        return "style"
    if issue.validator == "pacing":
        return "transition"
    if issue.issue_kind == "missing_scene":
        return "chapter_compression"
    return "reader_context"


def _infer_authority(issue: Issue) -> RepairAuthorityLevel:
    if issue.issue_kind == "missing_scene" or issue.scene_id is None:
        return RepairAuthorityLevel.HUMAN_REQUIRED
    if issue.span_start is not None or issue.quote:
        return RepairAuthorityLevel.SPAN_ONLY
    if issue.validator in {"continuity", "state_drift"}:
        return RepairAuthorityLevel.SCENE_STRUCTURAL
    return RepairAuthorityLevel.SCENE_LOCAL


# repair_kind -> enrichment pass name to re-run for a scene-local repair (None = full scene revision).
# Every value must be a real router.DRAFT_PASSES lane name. Each enrichment lane that also runs as a
# review lane (OPEN-8: combat/sensory/dialogue) must appear here so a lane reviewer's critique is
# repaired by that lane's own pass — combat was silently missing (validator "combat" fell through
# _infer_repair_kind to "reader_context", which maps nowhere -> full revision). Enforced by
# tests/test_repair_lane_routing.py.
_REPAIR_KIND_TO_PASS: dict[str, str | None] = {
    "dialogue": "dialogue",
    "expand": "sensory",
    "combat": "combat",
    "continuity": None,
    "style": None,
    "transition": None,
    "word_budget": None,
}


def _target_pass_for_task(task: RepairTask) -> str | None:
    return _REPAIR_KIND_TO_PASS.get(task.repair_kind)


def _highest_authority(issues: list[Issue]) -> RepairAuthorityLevel:
    authorities = [_infer_authority(issue) for issue in issues]
    return max(authorities, key=lambda authority: _AUTHORITY_RANK.get(authority, -1))


async def _queue_repair_task_from_issues(
    session: AsyncSession,
    *,
    run: ProductionRun,
    issues: list[Issue],
    agent_run_id: uuid.UUID | None = None,
    repair_kind: str | None = None,
    authority_level: RepairAuthorityLevel | None = None,
    chapter_scoped: bool = False,
    instruction_preamble: str | None = None,
) -> tuple[RepairTask, Artifact]:
    first = issues[0]
    repair_kind = repair_kind or _infer_repair_kind(first)
    authority_level = authority_level or _highest_authority(issues)
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        scene_id=None if chapter_scoped else first.scene_id,
        scene_no=None if chapter_scoped else first.scene_no,
        repair_kind=repair_kind,
        authority_level=authority_level,
        status=RepairTaskStatus.WAITING_FOR_HUMAN if is_manual_grant(authority_level) else RepairTaskStatus.QUEUED,
        issue_ids=[str(issue.id) for issue in issues],
        target_spans={
            "items": [
                {
                    "quote": issue.quote,
                    "span_start": issue.span_start,
                    "span_end": issue.span_end,
                }
                for issue in issues
                if issue.quote or issue.span_start is not None or issue.span_end is not None
            ]
        }
        if any(issue.quote or issue.span_start is not None or issue.span_end is not None for issue in issues)
        else None,
        instructions="\n".join(
            [
                *([instruction_preamble] if instruction_preamble else []),
                f"Repair kind: {repair_kind}. Authority: {authority_level}.",
                *[f"- {issue.recommended_action} Claim: {issue.claim}" for issue in issues],
            ]
        ),
        preserve=[
            f"Preserve scene outcome for scene {first.scene_no}."
            if first.scene_no is not None and not chapter_scoped
            else "Preserve chapter outcome."
        ],
        must_change=[issue.claim for issue in issues],
        must_not_change=["Do not contradict the approved scene packet.", "Do not change canon or chapter outcome."],
        allowed_operations=["replace_span", "rewrite_scene"]
        if authority_level
        in {RepairAuthorityLevel.SPAN_ONLY, RepairAuthorityLevel.SCENE_LOCAL, RepairAuthorityLevel.SCENE_STRUCTURAL}
        else ["propose_human_repair"],
        forbidden_operations=["change_canon", "change_chapter_outcome"]
        if not is_manual_grant(authority_level)
        else ["auto_apply"],
        requires_human_approval=authority_level
        in {
            RepairAuthorityLevel.CROSS_SCENE,
            RepairAuthorityLevel.CHAPTER_STRUCTURAL,
            RepairAuthorityLevel.HUMAN_REQUIRED,
        },
    )
    session.add(task)
    await session.flush()
    for issue in issues:
        issue.status = IssueStatus.REPAIR_QUEUED
    artifact = await support.create_artifact(
        session,
        run=run,
        artifact_type="repair_task",
        body={
            "repair_task_id": str(task.id),
            "scene_id": str(task.scene_id) if task.scene_id else None,
            "scene_no": task.scene_no,
            "repair_kind": task.repair_kind,
            "authority_level": task.authority_level,
            "issue_ids": task.issue_ids,
            "instructions": task.instructions,
        },
        created_by_agent_run_id=agent_run_id,
        domain_table="repair_tasks",
        domain_id=task.id,
    )
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_task_created",
        stage=run.current_stage,
        message=f"Repair task queued for scene {task.scene_no or 'chapter'}",
        payload={"repair_task_id": str(task.id), "repair_kind": task.repair_kind},
        agent_run_id=agent_run_id,
    )
    return task, artifact


async def triage_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    proposed = (
        (
            await session.execute(
                select(Issue)
                .where(Issue.production_run_id == run_id, Issue.status == IssueStatus.PROPOSED)
                .order_by(Issue.created_at)
            )
        )
        .scalars()
        .all()
    )
    # Issues accepted by an earlier triage but left untasked (deferred prose_polish
    # or infra_rate_limit retry state). Re-planned so a re-triage after structural
    # repairs resolve can release the deferred prose work.
    deferred = (
        (
            await session.execute(
                select(Issue)
                .where(Issue.production_run_id == run_id, Issue.status == IssueStatus.ACCEPTED)
                .order_by(Issue.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not proposed and not deferred:
        await support.update_run_summary(session, run)
        return run

    run.current_stage = "issue_triage"
    triage = await support.start_agent_run(
        session,
        run=run,
        agent_name="issue_triage_evaluator",
        agent_role="deterministic",
        stage="issue_triage",
        input_artifact_ids=[],
    )
    created_tasks: list[RepairTask] = []
    created_artifacts: list[Artifact] = []
    accepted_new: list[Issue] = []
    for issue in proposed:
        if issue.issue_kind == "missing_scene":
            decision = IssueDecisionKind.ESCALATE
            issue.status = IssueStatus.ESCALATED
            reason = "Missing scenes are structural gaps and require author intervention."
        elif issue.severity == "info":
            decision = IssueDecisionKind.REJECT
            issue.status = IssueStatus.REJECTED
            reason = "Info-level notes stay advisory and do not create repair work; warn/repair/block are accepted."
        else:
            decision = IssueDecisionKind.ACCEPT
            issue.status = IssueStatus.ACCEPTED
            accepted_new.append(issue)
            root_cause = repair_triage.infer_root_cause(issue)
            if root_cause == repair_triage.ROOT_CAUSE_INFRA_RATE_LIMIT:
                reason = "Accepted as infra_rate_limit retry state; provider rate limits never create repair tasks."
            elif root_cause in repair_triage.STRUCTURAL_AUTHORITY:
                reason = (
                    f"Accepted into root-cause cluster '{root_cause}'; "
                    "one chapter-scoped structural repair task covers all member issues."
                )
            else:
                reason = "Accepted as prose_polish for repair task generation."
        session.add(
            IssueDecision(
                issue_id=issue.id,
                decided_by="issue_triage_evaluator",
                decision=decision,
                reason=reason,
                agent_run_id=triage.id,
            )
        )
        await support.record_event(
            session,
            run_id=run.id,
            event_type=(
                "issue_accepted"
                if decision == IssueDecisionKind.ACCEPT
                else "human_action_required"
                if decision == IssueDecisionKind.ESCALATE
                else "issue_rejected"
            ),
            stage="issue_triage",
            message=reason,
            payload={"issue_id": str(issue.id), "decision": str(decision)},
            agent_run_id=triage.id,
        )

    # Root-cause clustering: one chapter-scoped repair task per structural cluster
    # (sequence_entry_state | scene_scope_bleed | budget_mismatch | canon_contract_leak),
    # never a per-scene scatter of symptom repairs.
    plan = repair_triage.plan_repair_tasks([*accepted_new, *deferred])
    for root_cause in repair_triage.STRUCTURAL_ROOT_CAUSES:
        cluster = plan.structural_clusters.get(root_cause)
        if not cluster:
            continue
        task, artifact = await _queue_repair_task_from_issues(
            session,
            run=run,
            issues=cluster,
            agent_run_id=triage.id,
            repair_kind=root_cause,
            authority_level=repair_triage.STRUCTURAL_AUTHORITY[root_cause],
            chapter_scoped=True,
            instruction_preamble=repair_triage.ROOT_CAUSE_INSTRUCTIONS[root_cause],
        )
        created_tasks.append(task)
        created_artifacts.append(artifact)

    # Prose polish stays gated while ANY structural root repair is unresolved —
    # either a cluster planned just now, or a structural task still open in the DB.
    unresolved_structural = (
        (
            await session.execute(
                select(RepairTask).where(
                    RepairTask.production_run_id == run_id,
                    RepairTask.repair_kind.in_(repair_triage.STRUCTURAL_ROOT_CAUSES),
                    RepairTask.status.in_(
                        [
                            RepairTaskStatus.QUEUED,
                            RepairTaskStatus.RUNNING,
                            RepairTaskStatus.WAITING_FOR_HUMAN,
                            RepairTaskStatus.FAILED,
                        ]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    defer_prose = plan.defer_prose or bool(unresolved_structural)
    if plan.prose_issues and not defer_prose:
        grouped: dict[tuple[uuid.UUID | None, int | None, str, str], list[Issue]] = defaultdict(list)
        for issue in plan.prose_issues:
            grouped[(issue.scene_id, issue.scene_no, _infer_repair_kind(issue), _infer_authority(issue))].append(issue)
        for _, grouped_issues in grouped.items():
            task, artifact = await _queue_repair_task_from_issues(
                session,
                run=run,
                issues=grouped_issues,
                agent_run_id=triage.id,
            )
            created_tasks.append(task)
            created_artifacts.append(artifact)
    elif plan.prose_issues:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="repair_deferred",
            stage="issue_triage",
            message=(
                f"{len(plan.prose_issues)} prose_polish issue(s) deferred until structural root-cause repairs resolve."
            ),
            payload={
                "issue_ids": [str(issue.id) for issue in plan.prose_issues],
                "deferred_behind": sorted(
                    {task.repair_kind for task in [*created_tasks, *unresolved_structural]}
                    & set(repair_triage.STRUCTURAL_ROOT_CAUSES)
                ),
            },
            agent_run_id=triage.id,
        )
    if plan.rate_limit_issues:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="rate_limit_retry_state",
            stage="issue_triage",
            message=(
                f"{len(plan.rate_limit_issues)} infra_rate_limit issue(s) recorded as retry state; "
                "no repair tasks created."
            ),
            payload={"issue_ids": [str(issue.id) for issue in plan.rate_limit_issues]},
            agent_run_id=triage.id,
        )

    support.finish_agent_run(
        triage,
        status=AgentRunStatus.COMPLETED,
        output_artifact_ids=[str(artifact.id) for artifact in created_artifacts],
    )
    run.status = ProductionRunStatus.REPAIRING if created_tasks else ProductionRunStatus.WAITING_FOR_HUMAN
    # L5+L6 composition: a structural root-cause cluster (created now, or still open from an
    # earlier triage) parks the run in structural_repair_required — ONE chapter-scoped task per
    # root cause carries the work, and prose repair stays deferred behind it. Symptom-only
    # triage lands in the ordinary repair_queue.
    structural_open = unresolved_structural or [
        task for task in created_tasks if task.repair_kind in repair_triage.STRUCTURAL_ROOT_CAUSES
    ]
    if structural_open:
        run.current_stage = run_stages.STAGE_STRUCTURAL_REPAIR_REQUIRED
    elif created_tasks:
        run.current_stage = "repair_queue"
    # L6: otherwise the run keeps its real stage (chapter_qa after assembly,
    # waiting_for_scene_drafts after an assembly refusal) instead of claiming "chapter_assembly".
    await support.update_run_summary(session, run)
    return run


async def _apply_chapter_scoped_repair(session: AsyncSession, run: ProductionRun, task: RepairTask) -> RepairTask:
    """Fan a chapter-scoped repair task (scene_id=None — structural clusters, human-approved work) out
    into one revision job per member scene. Without this, every requires_human_approval task was a
    dead-end: approval led straight into the 'does not target a concrete scene' refusal."""
    member_issues = [await session.get(Issue, uuid.UUID(issue_id)) for issue_id in task.issue_ids]
    member_issues = [issue for issue in member_issues if issue is not None]
    latest_scenes = await _latest_scene_map(session, run.chapter_id)
    target_nos = sorted(
        {
            issue.scene_no
            for issue in member_issues
            if issue.scene_no is not None and (latest_scenes.get(issue.scene_no) is not None)
        }
    )
    target_scenes = [latest_scenes[no] for no in target_nos if (latest_scenes[no].prose or "").strip()]
    if not target_scenes:
        raise ValueError(
            "repair task has no concrete target scenes — draft the missing scenes first, or fix the "
            "chapter by hand and reject this task"
        )

    target_pass = _target_pass_for_task(task)
    repair_agent = await support.start_agent_run(
        session,
        run=run,
        agent_name="repair_scheduler",
        agent_role="deterministic",
        stage="repair_execution",
        input_artifact_ids=[],
        payload={
            "repair_task_id": str(task.id),
            "mode": "chapter_scoped_fan_out",
            "target_scene_nos": [scene.scene_no for scene in target_scenes],
        },
    )
    latest_attempt_no = int(
        await session.scalar(select(func.max(RepairAttempt.attempt_no)).where(RepairAttempt.repair_task_id == task.id))
        or 0
    )
    job_ids: list[str] = []
    for offset, scene in enumerate(target_scenes, start=1):
        session.add(
            Approval(
                scene_id=scene.id,
                version=scene.version,
                decision=Decision.REVISE,
                target_pass=target_pass,
                feedback=task.instructions,
            )
        )
        job_id = await schedule_revision(
            session, scene, target_pass=target_pass, production_run_id=task.production_run_id
        )
        # A DraftQueueBlocker (contractless scene) is not a queued job — only count real job ids.
        if isinstance(job_id, uuid.UUID):
            job_ids.append(str(job_id))
        session.add(
            RepairAttempt(
                repair_task_id=task.id,
                agent_run_id=repair_agent.id,
                attempt_no=latest_attempt_no + offset,
                model=target_pass or "revision",
                # scene_id/base_version let the fan-out verify pair each attempt with its revised scene.
                patch_json={
                    "repair_kind": task.repair_kind,
                    "authority_level": task.authority_level,
                    "instructions": task.instructions,
                    "applied_via": "revision_job",
                    "scene_id": str(scene.id),
                    "scene_no": scene.scene_no,
                    "base_version": scene.version,
                },
                revised_text=None,
                change_summary=f"Queued a revision job for scene {scene.scene_no} from the chapter-scoped repair task.",
                issues_addressed=list(task.issue_ids),
                new_risks=[],
                word_count_before=scene.word_count,
                word_count_after=None,
            )
        )
    await session.flush()
    support.finish_agent_run(
        repair_agent,
        status=AgentRunStatus.COMPLETED,
        payload={"repair_task_id": str(task.id), "job_ids": job_ids},
    )
    task.status = RepairTaskStatus.RUNNING
    run.status = ProductionRunStatus.REPAIRING
    run.current_stage = "repair_execution"
    for issue in member_issues:
        issue.status = IssueStatus.REPAIR_QUEUED
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_started",
        stage="repair_execution",
        message=f"Queued {len(target_scenes)} scene revision(s) from the chapter-scoped repair task.",
        payload={"repair_task_id": str(task.id), "job_ids": job_ids},
        agent_run_id=repair_agent.id,
    )
    await support.update_run_summary(session, run)
    return task


async def apply_repair_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    autonomous: bool,
    human_approved: bool = False,
    approval_reason: str | None = None,
) -> RepairTask:
    # `autonomous` has NO default: every caller must declare its nature (a defaulted False would let
    # future automation impersonate the manual path by omission). Lock/claim the row AND refresh it before
    # any status read: a sweeper apply and a human "Approve & apply" race cross-session, and the sweeper
    # PRE-LOADS the task into its session. `with_for_update` alone acquires the FOR UPDATE lock but does NOT
    # repopulate an already-identity-mapped instance — the status guard below would read the STALE pre-load
    # and both callers would fan out a revision. `populate_existing=True` forces the locked SELECT to
    # refresh the attributes, so the loser of the race reads the winner's RUNNING status and 409s.
    task = await session.get(RepairTask, task_id, with_for_update=True, populate_existing=True)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    # Status guard: protects the drain-vs-human-click race (whichever loses gets a clean 409, never a
    # double application) and keeps Apply off verified/rejected/cancelled rows.
    if task.status not in (RepairTaskStatus.QUEUED, RepairTaskStatus.WAITING_FOR_HUMAN):
        raise ValueError(f"repair task is {task.status}; only queued or waiting_for_human tasks can be applied")
    # ADR-0031 D16 (A1b): manual-grant work — authority_level == HUMAN_REQUIRED, the temporary A1b
    # compatibility discriminator — needs an explicit HUMAN grant regardless of ceiling. An autonomous
    # caller can NEVER authorize it; refuse here, before any stamp or job scheduling. (A1c replaces this
    # discriminator with a durable Authorization Requirement axis orthogonal to authority_level.)
    if autonomous and is_manual_grant(task.authority_level):
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await support.record_event(
            session,
            run_id=run.id,
            event_type="human_action_required",
            stage="repair_execution",
            message="Manual-grant repair (human_required) needs an explicit human grant — use Approve & apply.",
            payload={"repair_task_id": str(task.id), "authority_level": str(task.authority_level)},
        )
        await support.update_run_summary(session, run)
        return task
    # An approval-gated task proceeds on a human grant (now or already stamped) OR an autonomous
    # authorization (the sweeper, already ceiling-gated; human_required was refused above). A plain manual
    # apply with neither waits for a human.
    authorized = human_approved or task.human_approved_at is not None or autonomous
    if task.requires_human_approval and not authorized:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await support.record_event(
            session,
            run_id=run.id,
            event_type="human_action_required",
            stage="repair_execution",
            message="Repair task requires explicit approval — use Approve & apply.",
            payload={"repair_task_id": str(task.id), "authority_level": str(task.authority_level)},
        )
        await support.update_run_summary(session, run)
        return task
    # human_approved_at is a HUMAN audit stamp — write it ONLY on a real human grant, never for the
    # sweeper's autonomous authorization (the "autonomous sweeper" false-stamp defect, ADR-0031 D16).
    if human_approved and task.human_approved_at is None:
        task.human_approved_at = datetime.now(UTC)
        await support.record_event(
            session,
            run_id=run.id,
            event_type="repair_task_approved",
            stage="repair_execution",
            message="Human approved the repair task for execution.",
            payload={
                "repair_task_id": str(task.id),
                "authority_level": str(task.authority_level),
                "reason": approval_reason,
            },
        )
    if task.scene_id is None:
        return await _apply_chapter_scoped_repair(session, run, task)
    scene = await session.get(Scene, task.scene_id)
    if scene is None:
        raise ValueError("target scene not found")

    # 6. Repair conflict detection + enforcement (using normalized targets)
    conflicts: list[dict[str, Any]] = []
    my_targets = _normalized_repair_targets(task)
    if my_targets:
        overlapping = (
            (
                await session.execute(
                    select(RepairTask).where(
                        RepairTask.chapter_id == task.chapter_id,
                        RepairTask.scene_no == task.scene_no,
                        RepairTask.id != task.id,
                        RepairTask.status.in_(_OPEN_REPAIR_STATUSES),
                    )
                )
            )
            .scalars()
            .all()
        )
        for ot in overlapping:
            ot_targets = _normalized_repair_targets(ot)
            if any(_targets_overlap(mt, ot_t) for mt in my_targets for ot_t in ot_targets):
                conflicts.append({"other_task_id": str(ot.id)})
                await support.record_event(
                    session,
                    run_id=run.id,
                    event_type="repair_conflict_detected",
                    message="Overlapping span repair detected; blocking until resolved.",
                    payload={"task_id": str(task.id), "other_task_id": str(ot.id)},
                )

    if conflicts:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await support.update_run_summary(session, run)
        return task

    target_pass = _target_pass_for_task(task)

    # Span-only AND non-span single-scene tasks both queue a REAL revision job that carries the target
    # span + instructions, so an actual LLM revision changes the scene. Span-only repairs used to be
    # applied inline as a placeholder patch that wrote the span back UNCHANGED — verify's span/quote
    # change check could never pass, so the task looped queued→running→queued forever, churning while
    # doing nothing (D1). Routing them through the revision path makes apply produce a genuine change
    # that verify can then accept honestly.
    patch_json = {
        "repair_kind": task.repair_kind,
        "authority_level": task.authority_level,
        "target_spans": task.target_spans,
        "instructions": task.instructions,
        "preserve": task.preserve,
        "must_change": task.must_change,
        "word_delta_target": task.word_delta_target,
        "applied_via": "revision_job",
    }

    job_id = await schedule_revision(session, scene, target_pass=target_pass, production_run_id=task.production_run_id)
    if not isinstance(job_id, uuid.UUID):
        # Guard: no revision job could be queued — the scene's chapter is gone (None) or it has no
        # approved contract to revise against (DraftQueueBlocker). Either way this task can never produce
        # a changed scene. Escalate to a human instead of marking it RUNNING — otherwise verify would
        # keep raising "no revised scene yet" and the drain would re-apply it forever.
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await support.record_event(
            session,
            run_id=run.id,
            event_type="human_action_required",
            stage="repair_execution",
            message="Could not queue a revision for this repair — it needs a human. No changed scene "
            "can be produced automatically.",
            payload={"repair_task_id": str(task.id), "authority_level": str(task.authority_level)},
        )
        await support.update_run_summary(session, run)
        return task

    approval = Approval(
        scene_id=scene.id,
        version=scene.version,
        decision=Decision.REVISE,
        target_pass=target_pass,
        feedback=task.instructions,
    )
    session.add(approval)
    repair_agent = await support.start_agent_run(
        session,
        run=run,
        agent_name="repair_scheduler",
        agent_role="deterministic",
        stage="repair_execution",
        input_artifact_ids=[],
        payload={"repair_task_id": str(task.id), "target_pass": target_pass},
    )
    # Create the attempt record for the queued revision; the verify step fills in the after-state
    # once the revision job has drafted the new scene version.
    latest_attempt_no = await session.scalar(
        select(func.max(RepairAttempt.attempt_no)).where(RepairAttempt.repair_task_id == task.id)
    )
    attempt = RepairAttempt(
        repair_task_id=task.id,
        agent_run_id=repair_agent.id,
        attempt_no=int(latest_attempt_no or 0) + 1,
        model=target_pass or "revision",
        patch_json=patch_json,
        revised_text=None,
        change_summary="Queued a revision job from the repair task instructions.",
        issues_addressed=list(task.issue_ids),
        new_risks=[],
        word_count_before=scene.word_count,
        word_count_after=None,
    )
    session.add(attempt)
    await session.flush()
    support.finish_agent_run(
        repair_agent,
        status=AgentRunStatus.COMPLETED,
        payload={
            "repair_task_id": str(task.id),
            "job_id": str(job_id) if job_id else None,
            "repair_attempt_id": str(attempt.id),
        },
    )
    task.status = RepairTaskStatus.RUNNING
    run.status = ProductionRunStatus.REPAIRING
    run.current_stage = "repair_execution"
    for issue_id in task.issue_ids:
        issue = await session.get(Issue, uuid.UUID(issue_id))
        if issue is not None:
            issue.status = IssueStatus.REPAIR_QUEUED
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_started",
        stage="repair_execution",
        message="Queued a scene revision from the repair task.",
        payload={"repair_task_id": str(task.id), "job_id": str(job_id) if job_id else None},
        agent_run_id=repair_agent.id,
    )
    await support.update_run_summary(session, run)
    return task


def _critique_matches_issue(issue: Issue, critique: Critique) -> bool:
    payload = critique.payload or {}
    claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
    quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
    return support.issue_signature(
        validator=critique.reviewer,
        issue_kind=str(payload.get("kind") or critique.reviewer),
        claim=claim,
        quote=quote if isinstance(quote, str) else None,
        scene_no=issue.scene_no,
    ) == str((issue.payload_json or {}).get("signature") or "")


async def _verify_chapter_scoped_repair(
    session: AsyncSession, run: ProductionRun, task: RepairTask
) -> RepairVerification:
    """Verify a fan-out apply: every member scene must have a newer version, and the member issues'
    critiques must have disappeared across the union of revised scenes. Span-level direct checks don't
    apply here — a chapter-scoped task owns claims, not character offsets — so acceptance rides on
    critique disappearance alone, same as the non-span single-scene path."""
    attempts = (
        (
            await session.execute(
                select(RepairAttempt)
                .where(RepairAttempt.repair_task_id == task.id, RepairAttempt.revised_text.is_(None))
                .order_by(RepairAttempt.attempt_no)
            )
        )
        .scalars()
        .all()
    )
    attempts = [a for a in attempts if isinstance(a.patch_json, dict) and a.patch_json.get("scene_id")]
    if not attempts:
        raise ValueError("no unverified repair attempt exists for this task — apply it (again) first")

    revised_by_attempt: list[tuple[RepairAttempt, Scene]] = []
    still_drafting: list[int] = []
    for attempt in attempts:
        patch = attempt.patch_json or {}
        scene_no = int(patch.get("scene_no") or 0)
        base_version = int(patch.get("base_version") or 0)
        revised = (
            (
                await session.execute(
                    select(Scene)
                    .where(
                        Scene.chapter_id == task.chapter_id,
                        Scene.scene_no == scene_no,
                        Scene.version > base_version,
                    )
                    .order_by(Scene.version.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if revised is None:
            still_drafting.append(scene_no)
        else:
            revised_by_attempt.append((attempt, revised))
    if still_drafting:
        raise ValueError(
            f"no revised prose yet for scene(s) {still_drafting} — the queued revision jobs may still be "
            "drafting (watch the queue indicator); verify again once every revised scene lands"
        )
    outcome_preserved = True
    revised_regions: list[tuple[int, str, str]] = []  # (scene_no, before_prose, after_prose)
    for attempt, revised in revised_by_attempt:
        attempt.revised_text = revised.prose
        attempt.word_count_after = revised.word_count
        base = await session.get(Scene, uuid.UUID(str((attempt.patch_json or {}).get("scene_id"))))
        if base is not None and revised.scene_packet_id != base.scene_packet_id:
            outcome_preserved = False
        region_scene_no = int((attempt.patch_json or {}).get("scene_no") or (base.scene_no if base else 0))
        revised_regions.append((region_scene_no, (base.prose if base else "") or "", revised.prose or ""))

    revised_scenes = [revised for _, revised in revised_by_attempt]
    scene_no_by_id = {scene.id: scene.scene_no for scene in revised_scenes}
    verifier = await support.start_agent_run(
        session,
        run=run,
        agent_name="repair_verifier",
        agent_role="deterministic",
        stage="repair_verification",
        input_artifact_ids=[],
        payload={
            "repair_task_id": str(task.id),
            "mode": "chapter_scoped_fan_out",
            "revised_scene_ids": [str(scene.id) for scene in revised_scenes],
        },
    )
    new_critiques = (
        (
            await session.execute(
                select(Critique).where(Critique.scene_id.in_(scene_no_by_id.keys())).order_by(Critique.id)
            )
        )
        .scalars()
        .all()
    )
    task_issues = [await session.get(Issue, uuid.UUID(issue_id)) for issue_id in task.issue_ids]
    task_issues = [issue for issue in task_issues if issue is not None]
    remaining = [
        issue for issue in task_issues if any(_critique_matches_issue(issue, critique) for critique in new_critiques)
    ]
    resolved = [issue for issue in task_issues if issue not in remaining]
    known_signatures = {str((issue.payload_json or {}).get("signature") or "") for issue in task_issues}
    created_new_issues: list[Issue] = []
    for critique in new_critiques:
        payload = critique.payload or {}
        claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
        quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
        conf_val = payload.get("confidence")
        critique_scene_no = scene_no_by_id.get(critique.scene_id)
        signature = support.issue_signature(
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            claim=claim,
            quote=quote if isinstance(quote, str) else None,
            scene_no=critique_scene_no,
        )
        if signature in known_signatures:
            continue
        known_signatures.add(signature)
        issue = await support.create_issue(
            session,
            run=run,
            artifact_type="scene_review_report",
            artifact_id=uuid.uuid4(),
            scene_id=critique.scene_id,
            scene_no=critique_scene_no,
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            severity=str(critique.severity),
            quote=quote if isinstance(quote, str) else None,
            span_start=support.critique_span(payload)[0],
            span_end=support.critique_span(payload)[1],
            claim=claim,
            contract_reference=None,
            recommended_action=support.recommended_action_from_critique(critique),
            confidence=float(conf_val) if isinstance(conf_val, (int, float)) else None,
            auto_repair_allowed=not is_blocking(critique.severity),
            payload=payload | {"signature": signature},
        )
        created_new_issues.append(issue)

    no_new_issues = not remaining and not created_new_issues
    verdict = (
        RepairVerificationVerdict.ACCEPT
        if no_new_issues
        else (
            RepairVerificationVerdict.ESCALATE_TO_HUMAN
            if any(is_blocking(issue.severity) for issue in created_new_issues)
            else RepairVerificationVerdict.NEEDS_ANOTHER_REPAIR
        )
    )
    anchor_attempt = revised_by_attempt[-1][0]
    # Required-beat preservation over the whole revised chapter region: concatenate the revised scenes'
    # before/after prose so a beat that legitimately RELOCATED between revised scenes (chapter-scoped
    # repairs restructure scenes) still counts as preserved. Union the scenes' required beats.
    from dominion.workers.production_sequence import latest_chapter_sequence

    chapter_seq = await latest_chapter_sequence(session, task.chapter_id)
    region_scene_nos = [sn for sn, _, _ in revised_regions]
    beats_by_scene = required_beats_for_scenes(chapter_seq, region_scene_nos)
    region_beats = (
        None
        if beats_by_scene is None
        else ordered_unique(beat for sn in region_scene_nos for beat in beats_by_scene.get(sn, []))
    )
    beats_result = beats_preserved(
        SCENE_BREAK.join(before for _, before, _ in revised_regions),
        SCENE_BREAK.join(after for _, _, after in revised_regions),
        region_beats,
    )
    verification = RepairVerification(
        repair_attempt_id=anchor_attempt.id,
        agent_run_id=verifier.id,
        verdict=verdict,
        resolved_issue_ids=[str(issue.id) for issue in resolved],
        remaining_issue_ids=[str(issue.id) for issue in remaining],
        new_issues_json=[
            {
                "id": str(issue.id),
                "validator": issue.validator,
                "issue_kind": issue.issue_kind,
                "claim": issue.claim,
                "severity": issue.severity,
            }
            for issue in created_new_issues
        ]
        or None,
        target_issue_resolved=not remaining,
        canon_preserved=not any(c.reviewer == "continuity" and is_blocking(c.severity) for c in new_critiques),
        scene_outcome_preserved=outcome_preserved,
        voice_preserved=not any(c.reviewer == "voice" and is_blocking(c.severity) for c in new_critiques),
        required_beats_preserved=beats_result.preserved,
        reader_state_preserved=not any(
            c.reviewer in {"continuity", "state_drift"} and is_blocking(c.severity) for c in new_critiques
        ),
        regression_score=float(len(remaining) + len(created_new_issues)),
        reason=(
            "Repair accepted."
            if verdict == RepairVerificationVerdict.ACCEPT
            else "Issues remain after repair verification."
        ),
        payload_json={
            "revised_scene_ids": [str(scene.id) for scene in revised_scenes],
            "new_critique_count": len(new_critiques),
            "mode": "chapter_scoped_fan_out",
            "required_beats_check": {
                "scope": "chapter_scoped",
                "status": beats_result.status,
                "preserved": beats_result.preserved,
                "checked_count": beats_result.checked_count,
                "present_before_count": beats_result.present_before_count,
                "dropped_count": len(beats_result.dropped_beats),
                "dropped_beats": list(beats_result.dropped_beats),
                "reason": beats_result.reason,
                "scene_numbers": region_scene_nos,
            },
        },
    )
    session.add(verification)
    await session.flush()
    support.finish_agent_run(
        verifier,
        status=AgentRunStatus.COMPLETED,
        payload={"repair_verification_id": str(verification.id), "verdict": str(verdict)},
    )
    for issue in resolved:
        issue.status = IssueStatus.VERIFIED
    for issue in remaining:
        issue.status = IssueStatus.ACCEPTED
    if verdict == RepairVerificationVerdict.ACCEPT:
        task.status = RepairTaskStatus.VERIFIED
    elif verdict == RepairVerificationVerdict.ESCALATE_TO_HUMAN:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    else:
        task.status = RepairTaskStatus.QUEUED
        run.status = ProductionRunStatus.REPAIRING
    run.current_stage = "repair_verification"
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_verified",
        stage="repair_verification",
        message=verification.reason,
        payload={"repair_task_id": str(task.id), "verdict": str(verdict)},
        agent_run_id=verifier.id,
    )
    await assemble_run(session, run)
    await support.update_run_summary(session, run)
    return verification


async def verify_repair_task(session: AsyncSession, task_id: uuid.UUID) -> RepairVerification:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    if task.scene_id is None:
        return await _verify_chapter_scoped_repair(session, run, task)
    attempt = (
        (
            await session.execute(
                select(RepairAttempt)
                .where(RepairAttempt.repair_task_id == task.id)
                .order_by(RepairAttempt.attempt_no.desc())
            )
        )
        .scalars()
        .first()
    )
    if attempt is None:
        raise ValueError("no repair attempt exists for this task")
    base_scene = await session.get(Scene, task.scene_id) if task.scene_id else None
    if base_scene is None:
        raise ValueError("repair task has no target scene")
    revised = (
        (
            await session.execute(
                select(Scene)
                .where(
                    Scene.chapter_id == base_scene.chapter_id,
                    Scene.scene_no == base_scene.scene_no,
                    Scene.version > base_scene.version,
                )
                .order_by(Scene.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if revised is None:
        raise ValueError(
            "no revised scene exists for this repair task yet — the queued revision job may still be "
            "drafting (watch the queue indicator); verify again once the revised scene lands"
        )
    attempt.revised_text = revised.prose
    attempt.word_count_after = revised.word_count

    # Direct before/after checks (upgrade: inspect actual repair target for span)
    before_text = base_scene.prose or ""
    after_text = revised.prose or ""
    span_changed = False
    quote_changed = False
    anchors_preserved = True
    wc_delta = (revised.word_count or 0) - (base_scene.word_count or 0)

    # Use the single normalizer
    targets = _normalized_repair_targets(task)
    for t in targets:
        tq = t.quote
        if tq:
            quote_changed = tq not in after_text or (
                tq in before_text and before_text.count(tq) != after_text.count(tq)
            )
            qidx = before_text.find(tq)
            if qidx >= 0:
                before_ctx = before_text[max(0, qidx - 15) : qidx + len(tq) + 15]
                if before_ctx not in after_text:
                    anchors_preserved = False
        if t.span_start is not None and t.span_end is not None:
            st, en = t.span_start, t.span_end
            if 0 <= st < en <= len(before_text):
                original = before_text[st:en]
                current_after = after_text[st:en] if len(after_text) >= en else ""
                span_changed = original != current_after
        if tq or (t.span_start is not None):
            break

    instruction_addressed = bool(task.instructions and after_text)

    # must_change / preserve are issue claims and meta-instructions, not literal prose text
    # to appear in the output. Satisfaction is determined by issue resolution (no remaining
    # matching critiques) and anchor/preserve checks on actual content constraints.
    # Only literal content-like preserve strings (rare) are substring checked.
    must_change_ok = True  # addressed via critique disappearance for the originating issues
    preserve_ok = True
    for pr in task.preserve or []:
        if any(kw in pr for kw in ("Preserve", "Do not", "must not", "Protect")):
            continue  # instruction/constraint, not text expected in prose
        if pr and pr not in (after_text or ""):
            preserve_ok = False

    # Protected preservation checks
    direct_checks = {
        "span_changed": span_changed or quote_changed,
        "quote_changed": quote_changed,
        "anchors_preserved": anchors_preserved,
        "word_count_moved": wc_delta != 0,
        "word_delta_target": task.word_delta_target,
        "instruction_addressed": instruction_addressed,
        "must_change_satisfied": must_change_ok,
        "preserve_satisfied": preserve_ok,
    }

    verifier = await support.start_agent_run(
        session,
        run=run,
        agent_name="repair_verifier",
        agent_role="deterministic",
        stage="repair_verification",
        input_artifact_ids=[],
        payload={"repair_task_id": str(task.id), "repair_attempt_id": str(attempt.id), "direct_checks": direct_checks},
    )
    new_critiques = (
        (await session.execute(select(Critique).where(Critique.scene_id == revised.id).order_by(Critique.id)))
        .scalars()
        .all()
    )
    task_issues = [await session.get(Issue, uuid.UUID(issue_id)) for issue_id in task.issue_ids]
    task_issues = [issue for issue in task_issues if issue is not None]
    remaining = [
        issue for issue in task_issues if any(_critique_matches_issue(issue, critique) for critique in new_critiques)
    ]
    resolved = [issue for issue in task_issues if issue not in remaining]
    known_signatures = {str((issue.payload_json or {}).get("signature") or "") for issue in task_issues}
    created_new_issues: list[Issue] = []
    for critique in new_critiques:
        payload = critique.payload or {}
        claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
        quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
        conf_val = payload.get("confidence")
        signature = support.issue_signature(
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            claim=claim,
            quote=quote if isinstance(quote, str) else None,
            scene_no=revised.scene_no,
        )
        if signature in known_signatures:
            continue
        known_signatures.add(signature)
        issue = await support.create_issue(
            session,
            run=run,
            artifact_type="scene_review_report",
            artifact_id=uuid.uuid4(),
            scene_id=revised.id,
            scene_no=revised.scene_no,
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            severity=str(critique.severity),
            quote=quote if isinstance(quote, str) else None,
            span_start=support.critique_span(payload)[0],
            span_end=support.critique_span(payload)[1],
            claim=claim,
            contract_reference=str(revised.scene_packet_id) if revised.scene_packet_id else None,
            recommended_action=support.recommended_action_from_critique(critique),
            confidence=float(conf_val) if isinstance(conf_val, (int, float)) else None,
            auto_repair_allowed=not is_blocking(critique.severity),
            payload=payload | {"signature": signature},
        )
        created_new_issues.append(issue)
    no_new_issues = not remaining and not created_new_issues
    if task.target_spans:
        accept_cond = (
            no_new_issues
            and (direct_checks.get("anchors_preserved", False) or direct_checks.get("quote_changed", False))
            and direct_checks.get("preserve_satisfied", True)
            and (direct_checks.get("span_changed", False) or direct_checks.get("quote_changed", False))
        )
    else:
        accept_cond = no_new_issues
    verdict = (
        RepairVerificationVerdict.ACCEPT
        if accept_cond
        else (
            RepairVerificationVerdict.ESCALATE_TO_HUMAN
            if any(is_blocking(issue.severity) for issue in created_new_issues)
            else RepairVerificationVerdict.NEEDS_ANOTHER_REPAIR
        )
    )
    # Required-beat preservation delta for this single scene (before-prose vs the revised prose).
    from dominion.workers.production_sequence import latest_chapter_sequence

    single_scene_seq = await latest_chapter_sequence(session, base_scene.chapter_id)
    scene_beats = required_beats_for_scene(single_scene_seq, base_scene.scene_no)
    beats_result = beats_preserved(before_text, after_text, scene_beats)
    verification = RepairVerification(
        repair_attempt_id=attempt.id,
        agent_run_id=verifier.id,
        verdict=verdict,
        resolved_issue_ids=[str(issue.id) for issue in resolved],
        remaining_issue_ids=[str(issue.id) for issue in remaining],
        new_issues_json=[
            {
                "id": str(issue.id),
                "validator": issue.validator,
                "issue_kind": issue.issue_kind,
                "claim": issue.claim,
                "severity": issue.severity,
            }
            for issue in created_new_issues
        ]
        or None,
        target_issue_resolved=not remaining and direct_checks.get("span_changed", True),
        canon_preserved=(
            not any(c.reviewer == "continuity" and is_blocking(c.severity) for c in new_critiques)
            and direct_checks.get("span_changed", True)
        ),
        scene_outcome_preserved=revised.scene_packet_id == base_scene.scene_packet_id,
        voice_preserved=not any(c.reviewer == "voice" and is_blocking(c.severity) for c in new_critiques),
        required_beats_preserved=beats_result.preserved,
        reader_state_preserved=not any(
            c.reviewer in {"continuity", "state_drift"} and is_blocking(c.severity) for c in new_critiques
        ),
        regression_score=float(len(remaining) + len(created_new_issues)),
        reason=(
            "Repair accepted."
            if verdict == RepairVerificationVerdict.ACCEPT
            else "Issues remain after repair verification."
        ),
        payload_json={
            "revised_scene_id": str(revised.id),
            "new_critique_count": len(new_critiques),
            "direct_checks": direct_checks,
            "required_beats_check": {
                "scope": "single_scene",
                "status": beats_result.status,
                "preserved": beats_result.preserved,
                "checked_count": beats_result.checked_count,
                "present_before_count": beats_result.present_before_count,
                "dropped_count": len(beats_result.dropped_beats),
                "dropped_beats": list(beats_result.dropped_beats),
                "reason": beats_result.reason,
                "scene_no": base_scene.scene_no,
            },
        },
    )
    session.add(verification)
    await session.flush()
    support.finish_agent_run(
        verifier,
        status=AgentRunStatus.COMPLETED,
        payload={"repair_verification_id": str(verification.id), "verdict": str(verdict)},
    )
    for issue in resolved:
        issue.status = IssueStatus.VERIFIED
    for issue in remaining:
        issue.status = IssueStatus.ACCEPTED
    if verdict == RepairVerificationVerdict.ACCEPT:
        task.status = RepairTaskStatus.VERIFIED
    elif verdict == RepairVerificationVerdict.ESCALATE_TO_HUMAN:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    else:
        task.status = RepairTaskStatus.QUEUED
        run.status = ProductionRunStatus.REPAIRING
    run.current_stage = "repair_verification"
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_verified",
        stage="repair_verification",
        message=verification.reason,
        payload={"repair_task_id": str(task.id), "verdict": str(verdict)},
        agent_run_id=verifier.id,
    )
    await assemble_run(session, run)
    await support.update_run_summary(session, run)
    return verification


async def _append_merged_issue_to_task(
    session: AsyncSession, run_id: uuid.UUID, target_issue_id: uuid.UUID, issue: Issue
) -> None:
    tasks = (
        (
            await session.execute(
                select(RepairTask).where(RepairTask.production_run_id == run_id).order_by(RepairTask.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for task in tasks:
        if str(target_issue_id) not in task.issue_ids:
            continue
        if str(issue.id) not in task.issue_ids:
            task.issue_ids = [*task.issue_ids, str(issue.id)]
        if issue.claim not in task.must_change:
            task.must_change = [*task.must_change, issue.claim]
            task.instructions = "\n".join([task.instructions, f"- {issue.recommended_action} Claim: {issue.claim}"])
        await session.flush()
        return


async def decide_issue(
    session: AsyncSession,
    issue_id: uuid.UUID,
    *,
    decision: str,
    reason: str | None = None,
    merged_into_issue_id: uuid.UUID | None = None,
) -> Issue:
    issue = await session.get(Issue, issue_id)
    if issue is None:
        raise ValueError("issue not found")
    run = await session.get(ProductionRun, issue.production_run_id)
    if run is None:
        raise ValueError("production run not found")

    if decision == IssueDecisionKind.ACCEPT:
        issue.status = IssueStatus.ACCEPTED
        await _queue_repair_task_from_issues(session, run=run, issues=[issue])
        run.status = ProductionRunStatus.REPAIRING
        run.current_stage = "repair_queue"
        event_type = "issue_accepted"
        message = reason or "Issue accepted and queued for repair."
    elif decision == IssueDecisionKind.REJECT:
        issue.status = IssueStatus.REJECTED
        event_type = "issue_rejected"
        message = reason or "Issue rejected."
    elif decision == IssueDecisionKind.ESCALATE:
        issue.status = IssueStatus.ESCALATED
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        run.current_stage = "issue_triage"
        event_type = "human_action_required"
        message = reason or "Issue escalated for human decision."
    elif decision == IssueDecisionKind.MARK_FALSE_POSITIVE:
        issue.status = IssueStatus.FALSE_POSITIVE
        event_type = "issue_rejected"
        message = reason or "Issue marked false positive."
    elif decision == IssueDecisionKind.MERGE:
        if merged_into_issue_id is None:
            raise ValueError("merged target issue id is required")
        target_issue = await session.get(Issue, merged_into_issue_id)
        if target_issue is None or target_issue.production_run_id != run.id:
            raise ValueError("merge target issue not found in this production run")
        issue.status = IssueStatus.MERGED
        await _append_merged_issue_to_task(session, run.id, target_issue.id, issue)
        event_type = "issue_accepted"
        message = reason or f"Issue merged into {target_issue.id}."
    else:
        raise ValueError("unsupported issue decision")

    session.add(
        IssueDecision(
            issue_id=issue.id,
            decided_by="human",
            decision=decision,
            reason=message,
        )
    )
    await support.record_event(
        session,
        run_id=run.id,
        event_type=event_type,
        stage=run.current_stage,
        message=message,
        payload={"issue_id": str(issue.id), "decision": str(decision)},
    )
    await support.update_run_summary(session, run)
    return issue


async def production_run_repair_tasks(session: AsyncSession, run_id: uuid.UUID) -> list[RepairTask]:
    rows = (
        (
            await session.execute(
                select(RepairTask).where(RepairTask.production_run_id == run_id).order_by(RepairTask.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def reject_repair_task(session: AsyncSession, task_id: uuid.UUID, reason: str | None = None) -> RepairTask:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    task.status = RepairTaskStatus.WAITING_FOR_HUMAN
    run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    run.current_stage = "repair_queue"
    for issue_id in task.issue_ids:
        issue = await session.get(Issue, uuid.UUID(issue_id))
        if issue is not None and issue.status == IssueStatus.REPAIR_QUEUED:
            issue.status = IssueStatus.ACCEPTED
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_rejected",
        stage=run.current_stage,
        message=reason or "Repair task rejected.",
        payload={"repair_task_id": str(task.id)},
    )
    await support.update_run_summary(session, run)
    return task


async def rollback_repair_task(session: AsyncSession, task_id: uuid.UUID, reason: str | None = None) -> RepairTask:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    if task.scene_id is None:
        raise ValueError("repair task does not target a concrete scene")
    base_scene = await session.get(Scene, task.scene_id)
    if base_scene is None:
        raise ValueError("target scene not found")
    revised = (
        (
            await session.execute(
                select(Scene)
                .where(
                    Scene.chapter_id == base_scene.chapter_id,
                    Scene.scene_no == base_scene.scene_no,
                    Scene.version > base_scene.version,
                    Scene.status != SceneStatus.SUPERSEDED,
                )
                .order_by(Scene.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if revised is None:
        raise ValueError("no applied repair revision exists to roll back")
    revised.status = SceneStatus.SUPERSEDED
    task.status = RepairTaskStatus.WAITING_FOR_HUMAN
    run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    run.current_stage = "repair_rollback"
    for issue_id in task.issue_ids:
        issue = await session.get(Issue, uuid.UUID(issue_id))
        if issue is not None:
            issue.status = IssueStatus.ACCEPTED
    await support.record_event(
        session,
        run_id=run.id,
        event_type="repair_rejected",
        stage=run.current_stage,
        message=reason or "Rolled back the latest repair revision.",
        payload={"repair_task_id": str(task.id), "scene_id": str(revised.id)},
    )
    await assemble_run(session, run)
    await support.update_run_summary(session, run)
    return task


# --------------------------------------------------------------------------------------------------
# SceneFidelity production triage (Lane 5). Before a run completes, materialize CURRENT, unresolved,
# repair-eligible fidelity Critiques into run-owned HUMAN_REQUIRED Issues; treat missing / stale /
# incomplete evaluation as operational holds, never prose failures (ADR 0010/0018/0019/0020).
# --------------------------------------------------------------------------------------------------

_OPEN_FIDELITY_STATUSES: frozenset[str] = frozenset(
    {
        IssueStatus.PROPOSED.value,
        IssueStatus.ACCEPTED.value,
        IssueStatus.MERGED.value,
        IssueStatus.REPAIR_QUEUED.value,
        IssueStatus.ESCALATED.value,
    }
)


async def _latest_final_draft_attempt(session: AsyncSession, scene_id: uuid.UUID) -> DraftAttempt | None:
    return (
        (
            await session.execute(
                select(DraftAttempt)
                .where(DraftAttempt.scene_id == scene_id, DraftAttempt.stage == "final_rendered")
                .order_by(DraftAttempt.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _latest_fidelity_report(session: AsyncSession, draft_attempt_id: uuid.UUID) -> Artifact | None:
    return (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.artifact_type == _FIDELITY_REPORT_TYPE, Artifact.domain_id == draft_attempt_id)
                .order_by(Artifact.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _open_fidelity_issues_for_clause(
    session: AsyncSession, *, scene_id: uuid.UUID, clause_id: str
) -> list[Issue]:
    rows = (
        (await session.execute(select(Issue).where(Issue.validator == "scene_fidelity", Issue.scene_id == scene_id)))
        .scalars()
        .all()
    )
    return [
        issue
        for issue in rows
        if (issue.payload_json or {}).get("clause_id") == clause_id and issue.status in _OPEN_FIDELITY_STATUSES
    ]


async def _fidelity_issue_exists(session: AsyncSession, *, run: ProductionRun, critique_id: uuid.UUID) -> bool:
    rows = (
        (
            await session.execute(
                select(Issue).where(Issue.production_run_id == run.id, Issue.validator == "scene_fidelity")
            )
        )
        .scalars()
        .all()
    )
    return any((issue.payload_json or {}).get("fidelity_critique_id") == str(critique_id) for issue in rows)


async def _persist_fidelity_critique(
    session: AsyncSession, *, scene: Scene, report_artifact: Artifact, projection: CritiqueProjection
) -> tuple[Critique, bool]:
    """Persist one projected fidelity Critique, idempotent by the report-projection unique index
    (reviewer, source_artifact_id, finding_signature). Returns (critique, created)."""
    existing = (
        (
            await session.execute(
                select(Critique).where(
                    Critique.reviewer == "scene_fidelity",
                    Critique.source_artifact_id == report_artifact.id,
                    Critique.finding_signature == projection.finding_signature,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing, False
    critique = Critique(
        scene_id=scene.id,
        scene_packet_id=scene.scene_packet_id,
        reviewer="scene_fidelity",
        severity=projection.severity,
        note=projection.note,
        payload=projection.payload,
        draft_attempt_id=uuid.UUID(projection.payload["draft_attempt_id"]),
        source_artifact_id=report_artifact.id,
        finding_signature=projection.finding_signature,
    )
    session.add(critique)
    await session.flush()
    return critique, True


async def _materialize_fidelity_issue(
    session: AsyncSession,
    *,
    run: ProductionRun,
    scene: Scene,
    scene_no: int,
    report_artifact: Artifact,
    critique: Critique,
    projection: CritiqueProjection,
) -> Issue:
    """Create a run-owned Issue + a HUMAN_REQUIRED RepairTask for one repair-eligible fidelity Critique,
    and supersede any prior open Issue for the same scene+clause (ADR 0018/0020). Fidelity repair is
    ALWAYS human-required — the scheduler must never turn an export hold into an autonomous rewrite."""
    payload = projection.payload
    clause_id = payload["clause_id"]
    prior_open = await _open_fidelity_issues_for_clause(session, scene_id=scene.id, clause_id=clause_id)

    issue = await support.create_issue(
        session,
        run=run,
        artifact_type=_FIDELITY_REPORT_TYPE,
        artifact_id=report_artifact.id,
        scene_id=scene.id,
        scene_no=scene_no,
        validator="scene_fidelity",
        issue_kind=str(payload["mode"]),
        severity="repair",
        quote=None,
        span_start=None,
        span_end=None,
        claim=projection.note,
        contract_reference=clause_id,
        recommended_action="Author-required fidelity repair: resolve the lost clause via an author-controlled preview.",
        confidence=None,
        auto_repair_allowed=False,
        payload={
            "fidelity_critique_id": str(critique.id),
            "finding_signature": projection.finding_signature,
            "requirement_id": payload["requirement_id"],
            "clause_id": clause_id,
            "mode": payload["mode"],
            "source_artifact_id": str(report_artifact.id),
        },
    )
    await _queue_repair_task_from_issues(
        session,
        run=run,
        issues=[issue],
        authority_level=RepairAuthorityLevel.HUMAN_REQUIRED,
        repair_kind="fidelity",
        instruction_preamble="SceneFidelity export hold — author-controlled repair required.",
    )
    for prior in prior_open:
        prior.status = IssueStatus.SUPERSEDED.value
        prior.payload_json = {**(prior.payload_json or {}), "successor_issue_id": str(issue.id)}
    return issue


async def _verify_satisfied_clauses(session: AsyncSession, *, scene: Scene, report: SceneFidelityReport) -> None:
    """A prior open fidelity Issue is VERIFIED only by a CURRENT satisfied evaluation of its hard clause
    with positive evidence — never by the mere absence of a complaint (ADR 0020/0022)."""
    satisfied = {
        ev.clause_id for ev in report.clause_evaluations if ev.result == ClauseResult.SATISFIED and ev.evidence_valid
    }
    for clause_id in satisfied:
        for issue in await _open_fidelity_issues_for_clause(session, scene_id=scene.id, clause_id=clause_id):
            issue.status = IssueStatus.VERIFIED.value


async def triage_scene_fidelity_for_production(session: AsyncSession, *, run: ProductionRun) -> TriageResult:
    """Materialize a run's CURRENT repair-eligible fidelity findings into run-owned Issues, verify clauses
    that now pass, and collect operational holds. Idempotent: keyed by (production_run_id,
    fidelity_critique_id), a re-run creates no duplicate Issue. Legacy/inert packets are skipped."""
    created_issue_ids: list[uuid.UUID] = []
    operational_holds: list[str] = []
    latest_scenes = await _latest_scene_map(session, run.chapter_id)

    for scene_no, scene in sorted(latest_scenes.items()):
        if scene.scene_packet_id is None:
            continue
        packet = await session.get(ScenePacket, scene.scene_packet_id)
        if packet is None or not is_fidelity_active(dict(packet.body or {})):
            continue  # no active fidelity contract — nothing to triage (forward-only, ADR 0025)

        final_attempt = await _latest_final_draft_attempt(session, scene.id)
        if final_attempt is None:
            operational_holds.append(f"scene {scene_no}: no draft attempt to evaluate")
            continue
        report_artifact = await _latest_fidelity_report(session, final_attempt.id)
        if report_artifact is None:
            operational_holds.append(f"scene {scene_no}: no fidelity evaluation report")
            continue

        current, reason = report_is_current(
            report_artifact.body or {},
            scene_packet_id=packet.id,
            packet_fingerprint=fidelity_contract_fingerprint(dict(packet.body or {})),
            draft_attempt_id=final_attempt.id,
            prose=final_attempt.prose or scene.prose or "",
        )
        if not current:
            operational_holds.append(f"scene {scene_no}: stale evaluation ({reason})")
            continue

        report = SceneFidelityReport.model_validate(report_artifact.body or {})
        for evaluation in report.clause_evaluations:
            if policy_outcome_for_clause_evaluation(evaluation).kind == "operational_hold":
                operational_holds.append(
                    f"scene {scene_no}: clause {evaluation.clause_id} incomplete ({evaluation.result.value})"
                )

        await _verify_satisfied_clauses(session, scene=scene, report=report)

        for projection in project_report_to_critiques(report, source_artifact_id=report_artifact.id):
            critique, _created = await _persist_fidelity_critique(
                session, scene=scene, report_artifact=report_artifact, projection=projection
            )
            if projection.severity != "repair":
                continue  # advisory warnings never become run Issues
            if await _fidelity_issue_exists(session, run=run, critique_id=critique.id):
                continue  # idempotent by (production_run_id, fidelity_critique_id)
            issue = await _materialize_fidelity_issue(
                session,
                run=run,
                scene=scene,
                scene_no=scene_no,
                report_artifact=report_artifact,
                critique=critique,
                projection=projection,
            )
            created_issue_ids.append(issue.id)

    await session.flush()  # persist lifecycle transitions (VERIFIED/SUPERSEDED) within the run txn
    return TriageResult(created_issue_ids=created_issue_ids, operational_holds=operational_holds)


# --------------------------------------------------------------------------------------------------
# SceneFidelity repair previews (Lane 6). A preview is an immutable, bounded proposal tied to one
# actionable fidelity Issue; it never changes the current Scene. Only the author, by accepting or
# editing it, materializes a NEW author-visible revision (ADR 0017). Its Artifact BODY is immutable —
# lifecycle rides the Artifact.status column, not a body edit.
# --------------------------------------------------------------------------------------------------


async def create_repair_preview(
    session: AsyncSession, *, issue: Issue, candidate_prose: str, rationale: str, edited: bool = False
) -> Artifact:
    """Create an immutable RepairPreview Artifact for one repair-eligible fidelity Issue, carrying the
    diff, evidence window, and preservation boundary. Does NOT touch the current Scene (ADR 0017)."""
    ipayload = issue.payload_json or {}
    critique = None
    if ipayload.get("fidelity_critique_id"):
        critique = await session.get(Critique, uuid.UUID(ipayload["fidelity_critique_id"]))
    cpayload = (critique.payload if critique else {}) or {}
    scene = await session.get(Scene, issue.scene_id) if issue.scene_id else None
    old_prose = (scene.prose if scene else "") or ""

    body = build_preview_body(
        source_issue_id=str(issue.id),
        source_critique_id=str(critique.id) if critique else "",
        source_report_artifact_id=str(ipayload.get("source_artifact_id") or ""),
        source_draft_attempt_id=str(cpayload.get("draft_attempt_id") or ""),
        scene_id=str(issue.scene_id or ""),
        prose_hash=str(cpayload.get("prose_hash") or ""),
        packet_fingerprint=str(cpayload.get("packet_contract_fingerprint") or ""),
        clause_ids=[ipayload["clause_id"]] if ipayload.get("clause_id") else [],
        anchors=cpayload.get("evidence_anchors") or [],
        old_prose=old_prose,
        candidate_prose=candidate_prose,
        rationale=rationale,
        edited=edited,
    )
    run = await session.get(ProductionRun, issue.production_run_id)
    assert run is not None  # an Issue always belongs to a run
    return await support.create_artifact(
        session,
        run=run,
        artifact_type=REPAIR_PREVIEW_ARTIFACT_TYPE,
        body=body,
        domain_table="issues",
        domain_id=issue.id,
    )


async def accept_repair_preview(
    session: AsyncSession, *, preview_artifact_id: uuid.UUID, edited_prose: str | None = None
) -> Scene:
    """Materialize an accepted (or edited) preview into a NEW author-visible Scene revision, supersede the
    old revision, stale the source evidence, and schedule fresh evaluation. Accept and edit both create a
    NORMAL new revision — only the prose_source differs (ADR 0017)."""
    preview = await session.get(Artifact, preview_artifact_id)
    assert preview is not None
    body = preview.body or {}
    scene = await session.get(Scene, uuid.UUID(body["scene_id"]))
    assert scene is not None
    new_prose = edited_prose if edited_prose is not None else (body.get("candidate_prose") or "")

    new_scene = Scene(
        chapter_id=scene.chapter_id,
        scene_no=scene.scene_no,
        version=scene.version + 1,
        parent_scene_id=scene.id,
        status=SceneStatus.PENDING_REVIEW,
        scene_packet_id=scene.scene_packet_id,
        prose=new_prose,
        prose_source="agent+human_edit" if edited_prose is not None else "agent",
        word_count=len((new_prose or "").split()),
    )
    session.add(new_scene)
    scene.status = SceneStatus.SUPERSEDED
    await session.flush()

    # A final DraftAttempt so fresh fidelity evaluation of the new revision has a target (the actual
    # re-evaluation is scheduled/deferred — never inline here).
    session.add(
        DraftAttempt(
            scene_id=new_scene.id,
            scene_packet_id=new_scene.scene_packet_id,
            stage="final_rendered",
            prose=new_prose,
            model="human_repair_preview",
        )
    )
    # The source Issue's evidence is now stale; mark it REPAIRED (fresh evaluation VERIFIES or re-opens it).
    if body.get("source_issue_id"):
        source_issue = await session.get(Issue, uuid.UUID(body["source_issue_id"]))
        if source_issue is not None:
            source_issue.status = IssueStatus.REPAIRED.value
    preview.status = "materialized"  # body stays immutable; status marks the lifecycle
    await session.flush()

    run = await session.get(ProductionRun, preview.production_run_id) if preview.production_run_id else None
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_repair_accepted",
            stage=run.current_stage,
            message="Author accepted a fidelity repair preview; new revision scheduled for re-evaluation",
            payload={
                "scene_id": str(new_scene.id),
                "preview_artifact_id": str(preview.id),
                "edited": edited_prose is not None,
            },
        )
    return new_scene


async def override_fidelity_issue(
    session: AsyncSession, *, issue: Issue, reason: str, overridden_by: str = "author"
) -> Issue:
    """Author override of a fidelity repair Issue (ADR 0009): record the reason + affected clause, mark
    the Issue OVERRIDDEN, and CANCEL its human-required RepairTask. The override does not mutate the
    report and never inherits to later drafts — a fresh loss on a new draft materializes a NEW Issue."""
    issue.status = IssueStatus.OVERRIDDEN.value
    ipayload = issue.payload_json or {}
    issue.payload_json = {
        **ipayload,
        "override": {"reason": reason, "by": overridden_by, "clause_id": ipayload.get("clause_id")},
    }
    tasks = (
        (await session.execute(select(RepairTask).where(RepairTask.production_run_id == issue.production_run_id)))
        .scalars()
        .all()
    )
    for task in tasks:
        if str(issue.id) in (task.issue_ids or []) and task.status not in (
            RepairTaskStatus.VERIFIED,
            RepairTaskStatus.CANCELLED,
        ):
            task.status = RepairTaskStatus.CANCELLED
    await session.flush()
    run = await session.get(ProductionRun, issue.production_run_id)
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_issue_overridden",
            stage=run.current_stage,
            message="Author overrode a fidelity repair Issue",
            payload={"issue_id": str(issue.id), "reason": reason},
        )
    return issue


async def reject_repair_preview(
    session: AsyncSession, *, preview_artifact_id: uuid.UUID, reason: str | None = None
) -> Artifact:
    """Reject a preview. The Critique and Issue stay intact and the current Scene is untouched (ADR 0017)."""
    preview = await session.get(Artifact, preview_artifact_id)
    assert preview is not None
    preview.status = "rejected"
    await session.flush()
    run = await session.get(ProductionRun, preview.production_run_id) if preview.production_run_id else None
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_repair_rejected",
            stage=run.current_stage,
            message="Author rejected a fidelity repair preview",
            payload={"preview_artifact_id": str(preview.id), "reason": reason},
        )
    return preview
