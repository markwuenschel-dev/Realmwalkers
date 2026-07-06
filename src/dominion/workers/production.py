"""Durable editorial production orchestration built on top of the contract-first draft system.

The existing draft queue, scene-packet contract, and reviewer pipeline stay authoritative for scene
generation. This module adds a chapter-level production spine around them: durable run state,
structured issues, repair tasks, verification, chapter assembly, and an auditable event trail.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    AgentRunStatus,
    Decision,
    IssueDecisionKind,
    IssueStatus,
    ProductionRunStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
    RepairVerificationVerdict,
    SceneStatus,
)
from dominion.shared.models import (
    AgentEvent,
    AgentRun,
    Approval,
    Artifact,
    ArtifactDependency,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    Critique,
    DraftRunTimeline,
    Issue,
    IssueDecision,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
    Scene,
    ScenePacket,
)
from dominion.workers import activity, repair_triage
from dominion.workers.job_scheduler import schedule_revision

# L6 (run orchestration): pure stage machine — pinned stage strings + deterministic gates that must
# fail BEFORE any LLM spend. Persistence stays here; decisions live in run_stages (DB-free, tested).
from dominion.workers import run_stages  # isort: skip


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


_AUTHORITY_RANK = {
    RepairAuthorityLevel.SPAN_ONLY: 0,
    RepairAuthorityLevel.SCENE_LOCAL: 1,
    RepairAuthorityLevel.SCENE_STRUCTURAL: 2,
    RepairAuthorityLevel.CROSS_SCENE: 3,
    RepairAuthorityLevel.CHAPTER_STRUCTURAL: 4,
    RepairAuthorityLevel.HUMAN_REQUIRED: 5,
}


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


async def _next_artifact_version(session: AsyncSession, run_id: uuid.UUID, artifact_type: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.production_run_id == run_id, Artifact.artifact_type == artifact_type)
    )
    return int(count or 0) + 1


async def _record_event(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    event_type: str,
    stage: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    agent_run_id: uuid.UUID | None = None,
) -> AgentEvent:
    event = AgentEvent(
        production_run_id=run_id,
        agent_run_id=agent_run_id,
        event_type=event_type,
        stage=stage,
        message=message,
        payload_json=payload,
    )
    session.add(event)
    await session.flush()
    # Mirror every production event into the central Activity feed so it lands in the Activity drawer
    # alongside draft jobs and review actions (best-effort; a feed failure never breaks the pipeline).
    await activity.record_from_production_event(
        session, run_id=run_id, event_type=event_type, stage=stage, message=message, payload=payload
    )
    return event


async def _start_agent_run(
    session: AsyncSession,
    *,
    run: ProductionRun,
    agent_name: str,
    agent_role: str,
    stage: str,
    model: str | None = None,
    input_artifact_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> AgentRun:
    agent_run = AgentRun(
        production_run_id=run.id,
        agent_name=agent_name,
        agent_role=agent_role,
        model=model,
        status=AgentRunStatus.RUNNING,
        stage=stage,
        input_artifact_ids=input_artifact_ids or [],
        payload_json=payload,
        started_at=_now(),
    )
    session.add(agent_run)
    await session.flush()
    return agent_run


def _finish_agent_run(
    agent_run: AgentRun,
    *,
    status: str,
    output_artifact_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    started = agent_run.started_at or agent_run.created_at
    finished = _now()
    agent_run.status = status
    agent_run.output_artifact_ids = output_artifact_ids
    agent_run.payload_json = payload if payload is not None else agent_run.payload_json
    agent_run.error = error
    agent_run.completed_at = finished
    if started is not None:
        agent_run.duration_ms = int((finished - started).total_seconds() * 1000)


async def _create_artifact(
    session: AsyncSession,
    *,
    run: ProductionRun,
    artifact_type: str,
    body: dict[str, Any],
    created_by_agent_run_id: uuid.UUID | None = None,
    domain_table: str | None = None,
    domain_id: uuid.UUID | None = None,
    status: str = "active",
    dependencies: list[tuple[uuid.UUID, str, str | None]] | None = None,
) -> Artifact:
    artifact = Artifact(
        production_run_id=run.id,
        artifact_type=artifact_type,
        domain_table=domain_table,
        domain_id=domain_id,
        version=await _next_artifact_version(session, run.id, artifact_type),
        status=status,
        body=body,
        content_hash=_hash_payload(body),
        created_by_agent_run_id=created_by_agent_run_id,
    )
    session.add(artifact)
    await session.flush()
    if dependencies:
        for dep_id, dep_kind, dep_hash in dependencies:
            session.add(
                ArtifactDependency(
                    artifact_id=artifact.id,
                    depends_on_artifact_id=dep_id,
                    dependency_kind=dep_kind,
                    dependency_hash=dep_hash,
                )
            )
    await _record_event(
        session,
        run_id=run.id,
        event_type="artifact_created",
        stage=run.current_stage,
        message=f"{artifact_type} artifact created",
        payload={"artifact_id": str(artifact.id), "artifact_type": artifact_type, "version": artifact.version},
        agent_run_id=created_by_agent_run_id,
    )
    return artifact


async def _latest_approved_packet(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterPacket | None:
    return (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == "approved")
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_chapter_sequence(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterSequence | None:
    from dominion.workers import production_sequence

    return await production_sequence.latest_chapter_sequence(session, chapter_id)


async def latest_draft_timeline(session: AsyncSession, production_run_id: uuid.UUID) -> DraftRunTimeline | None:
    from dominion.workers import production_sequence

    return await production_sequence.latest_draft_timeline(session, production_run_id)


def _contract_item(
    *,
    text: str,
    classification: str,
    blocks_drafting: bool,
    reader_visibility: str,
    drafting_rule: str,
    source_reference: str,
    confidence: float = 1.0,
) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence._contract_item(
        text=text,
        classification=classification,
        blocks_drafting=blocks_drafting,
        reader_visibility=reader_visibility,
        drafting_rule=drafting_rule,
        source_reference=source_reference,
        confidence=confidence,
    )


def derive_contract_classification(
    packet_body: dict[str, Any], open_questions: dict[str, Any] | None
) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence.derive_contract_classification(packet_body, open_questions)


def derive_chapter_sequence(packet_body: dict[str, Any]) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence.derive_chapter_sequence(packet_body)


def chain_scene_entry_states(body: dict[str, Any]) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence.chain_scene_entry_states(body)


def _int_or_none(value: Any) -> int | None:
    from dominion.workers import production_sequence

    return production_sequence._int_or_none(value)


# Articles/particles that would make a whole-word visibility match meaningless ("The Broker" must
# match on "Broker", never on "The").
_ROSTER_NAME_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of"})


def _roster_name_tokens(entry: str) -> list[str]:
    from dominion.workers import production_sequence

    return production_sequence._roster_name_tokens(entry)


def run_chapter_draft_qa(
    sequence_body: dict[str, Any] | None,
    scene_rows: list[dict[str, Any]],
    full_prose: str,
    packet_body: dict[str, Any] | None = None,
    open_questions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence.run_chapter_draft_qa(sequence_body, scene_rows, full_prose, packet_body, open_questions)


def evaluate_chapter_sequence(body: dict[str, Any]) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return production_sequence.evaluate_chapter_sequence(body)


async def ensure_chapter_sequence(session: AsyncSession, packet: ChapterPacket) -> ChapterSequence:
    from dominion.workers import production_sequence

    return await production_sequence.ensure_chapter_sequence(session, packet)


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:
    from dominion.workers import production_sequence

    return await production_sequence._latest_scene_map(session, chapter_id)


async def _scene_packet_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, ScenePacket]:
    from dominion.workers import production_sequence

    return await production_sequence._scene_packet_map(session, chapter_id)


def _recommended_action_from_critique(critique: Critique) -> str:
    if critique.reviewer in {"continuity", "state_drift"}:
        return "Revise the prose so it matches canon, scene packet locks, and declared state changes."
    if critique.reviewer == "dialogue":
        return "Revise the scene's dialogue while preserving the scene outcome and voice."
    if critique.reviewer == "voice":
        return "Revise the prose style while preserving events and information order."
    if critique.reviewer == "pacing":
        return "Adjust pacing and transitions without changing the scene outcome."
    if critique.reviewer == "sensory":
        return "Strengthen grounded physical detail and concrete description."
    if critique.reviewer == "length":
        return "Adjust length toward the approved scene budget."
    return "Revise the scene locally to resolve the validator claim."


def _critique_span(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    span = payload.get("span")
    if isinstance(span, (list, tuple)) and len(span) == 2:
        start = span[0] if isinstance(span[0], int) else None
        end = span[1] if isinstance(span[1], int) else None
        return start, end
    start = payload.get("span_start")
    end = payload.get("span_end")
    return (start if isinstance(start, int) else None, end if isinstance(end, int) else None)


def _issue_signature(*, validator: str, issue_kind: str, claim: str, quote: str | None, scene_no: int | None) -> str:
    return _hash_payload(
        {
            "validator": validator,
            "issue_kind": issue_kind,
            "claim": claim.strip(),
            "quote": (quote or "").strip(),
            "scene_no": scene_no,
        }
    )


def _infer_repair_kind(issue: Issue) -> str:
    if issue.issue_kind in {"length", "budget"} or issue.validator == "length":
        return "word_budget"
    if issue.validator == "dialogue":
        return "dialogue"
    if issue.validator == "sensory":
        return "expand"
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


def _target_pass_for_task(task: RepairTask) -> str | None:
    mapping = {
        "dialogue": "dialogue",
        "expand": "sensory",
        "continuity": None,
        "style": None,
        "transition": None,
        "word_budget": None,
    }
    return mapping.get(task.repair_kind)


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
        status=RepairTaskStatus.WAITING_FOR_HUMAN
        if authority_level == RepairAuthorityLevel.HUMAN_REQUIRED
        else RepairTaskStatus.QUEUED,
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
        if authority_level != RepairAuthorityLevel.HUMAN_REQUIRED
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
    artifact = await _create_artifact(
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_task_created",
        stage=run.current_stage,
        message=f"Repair task queued for scene {task.scene_no or 'chapter'}",
        payload={"repair_task_id": str(task.id), "repair_kind": task.repair_kind},
        agent_run_id=agent_run_id,
    )
    return task, artifact


async def _create_issue(
    session: AsyncSession,
    *,
    run: ProductionRun,
    artifact_type: str,
    artifact_id: uuid.UUID,
    scene_id: uuid.UUID | None,
    scene_no: int | None,
    validator: str,
    issue_kind: str,
    severity: str,
    quote: str | None,
    span_start: int | None,
    span_end: int | None,
    claim: str,
    contract_reference: str | None,
    recommended_action: str,
    confidence: float | None,
    auto_repair_allowed: bool,
    payload: dict[str, Any] | None = None,
) -> Issue:
    issue = Issue(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        scene_id=scene_id,
        scene_no=scene_no,
        validator=validator,
        issue_kind=issue_kind,
        severity=severity,
        quote=quote,
        span_start=span_start,
        span_end=span_end,
        claim=claim,
        contract_reference=contract_reference,
        recommended_action=recommended_action,
        confidence=confidence,
        auto_repair_allowed=auto_repair_allowed,
        payload_json=payload,
    )
    session.add(issue)
    await session.flush()
    await _record_event(
        session,
        run_id=run.id,
        event_type="issue_created",
        stage=run.current_stage,
        message=f"{validator} raised {issue_kind}",
        payload={"issue_id": str(issue.id), "scene_no": scene_no, "severity": severity},
    )
    return issue


async def assemble_run(session: AsyncSession, run: ProductionRun) -> None:
    from dominion.workers import production_sequence

    return await production_sequence.assemble_run(session, run)


async def queue_draft_jobs_for_missing_sequence_scenes(session: AsyncSession, run: ProductionRun) -> list[uuid.UUID]:
    from dominion.workers import production_sequence

    return await production_sequence.queue_draft_jobs_for_missing_sequence_scenes(session, run)


async def ensure_draft_run_timeline(session: AsyncSession, run: ProductionRun) -> DraftRunTimeline:
    from dominion.workers import production_sequence

    return await production_sequence.ensure_draft_run_timeline(session, run)


async def update_timeline_after_scene(
    session: AsyncSession, production_run_id: uuid.UUID | None, scene: Scene
) -> DraftRunTimeline | None:
    from dominion.workers import production_sequence

    return await production_sequence.update_timeline_after_scene(session, production_run_id, scene)


async def _block_production_on_timeline_failure(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> None:
    from dominion.workers import production_sequence

    return await production_sequence._block_production_on_timeline_failure(session, production_run_id, error)


async def mark_run_provider_rate_limited(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> ProductionRun | None:
    from dominion.workers import production_sequence

    return await production_sequence.mark_run_provider_rate_limited(session, production_run_id, error)


async def create_production_run(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    mode: str = "full_chapter",
    target_words: int | None = None,
    hard_max_words: int | None = None,
    auto_triage: bool = True,
) -> ProductionRun:
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("chapter not found")
    packet = await _latest_approved_packet(session, chapter_id)
    if packet is None:
        raise ValueError("no approved chapter packet for this chapter")
    run = ProductionRun(
        book_id=chapter.book_id,
        chapter_id=chapter_id,
        status=ProductionRunStatus.RUNNING,
        mode=mode,
        target_words=target_words,
        hard_max_words=hard_max_words,
        current_stage="contract_classification",
        settings_json={"auto_triage": auto_triage},
        source_hash=_hash_payload(
            {"chapter_packet_id": str(packet.id), "packet_hash": _hash_payload(packet.body or {})}
        ),
    )
    session.add(run)
    await session.flush()
    await _record_event(
        session,
        run_id=run.id,
        event_type="run_started",
        stage=run.current_stage,
        message="Production run created.",
        payload={"chapter_id": str(chapter_id), "chapter_no": chapter.chapter_no},
    )

    packet_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_packet",
        body={
            "chapter_packet_id": str(packet.id),
            "status": packet.status,
            "body": packet.body,
            "open_questions": packet.open_questions,
        },
        domain_table="chapter_packets",
        domain_id=packet.id,
    )

    classifier = await _start_agent_run(
        session,
        run=run,
        agent_name="contract_classifier",
        agent_role="deterministic",
        stage="contract_classification",
        input_artifact_ids=[str(packet_artifact.id)],
    )
    contract_body = derive_contract_classification(packet.body or {}, packet.open_questions)
    contract_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="contract_classification",
        body=contract_body,
        created_by_agent_run_id=classifier.id,
        domain_table="chapter_packets",
        domain_id=packet.id,
        dependencies=[(packet_artifact.id, "contract", packet_artifact.content_hash)],
    )
    _finish_agent_run(classifier, status=AgentRunStatus.COMPLETED, output_artifact_ids=[str(contract_artifact.id)])
    await _record_event(
        session,
        run_id=run.id,
        event_type="stage_completed",
        stage="contract_classification",
        message="Contract classification completed.",
        payload={"artifact_id": str(contract_artifact.id)},
        agent_run_id=classifier.id,
    )

    run.current_stage = "chapter_sequence"
    planner = await _start_agent_run(
        session,
        run=run,
        agent_name="chapter_sequence_planner",
        agent_role="deterministic",
        stage="chapter_sequence",
        input_artifact_ids=[str(packet_artifact.id), str(contract_artifact.id)],
    )
    sequence = await ensure_chapter_sequence(session, packet)
    sequence_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_sequence",
        body={
            "chapter_sequence_id": str(sequence.id),
            "status": sequence.status,
            "body": sequence.body,
            "qa_verdict": sequence.qa_verdict,
            "qa_warnings": sequence.qa_warnings,
        },
        created_by_agent_run_id=planner.id,
        domain_table="chapter_sequences",
        domain_id=sequence.id,
        dependencies=[
            (packet_artifact.id, "source", packet_artifact.content_hash),
            (contract_artifact.id, "contract", contract_artifact.content_hash),
        ],
    )
    _finish_agent_run(planner, status=AgentRunStatus.COMPLETED, output_artifact_ids=[str(sequence_artifact.id)])

    # Initialize the active DraftRunTimeline (live memory) right after sequence.
    await ensure_draft_run_timeline(session, run)

    scene_packets = await _scene_packet_map(session, chapter_id)
    scene_packet_artifacts: dict[int, Artifact] = {}
    seq_body = sequence.body or {} if sequence else {}
    seq_by_no = {int(s.get("scene_no") or 0): s for s in (seq_body.get("scenes") or []) if isinstance(s, dict)}
    for scene_no, scene_packet in sorted(scene_packets.items()):
        # Production refactor: ScenePacket view inherits key planning fields from ChapterSequence
        # (entry/exit, owned_beats, word_budget already planned, must_not etc.)
        sp_body = dict(scene_packet.body or {})
        seq_item = seq_by_no.get(scene_no, {})
        # The sequence is the authority for a dependent scene's opening state: its chained
        # entry_state overwrites any stale one the packet body carries (an independent scene, or a
        # scene missing from the sequence, keeps the packet's own value).
        if seq_item.get("entry_state") and not seq_item.get("independent_draft_allowed"):
            sp_body["entry_state"] = seq_item.get("entry_state")
        else:
            sp_body.setdefault("entry_state", seq_item.get("entry_state"))
        sp_body.setdefault("exit_state", seq_item.get("exit_state"))
        sp_body.setdefault("owned_beats", seq_item.get("owned_beats") or seq_item.get("required_beats"))
        sp_body.setdefault("word_budget", seq_item.get("word_budget") or sp_body.get("word_budget"))
        sp_body["sequence_scene_function"] = seq_item.get("scene_function")
        scene_packet_artifacts[scene_no] = await _create_artifact(
            session,
            run=run,
            artifact_type="scene_packet",
            body={
                "scene_packet_id": str(scene_packet.id),
                "scene_no": scene_no,
                "status": scene_packet.status,
                "qa_verdict": scene_packet.qa_verdict,
                "body": sp_body,
            },
            domain_table="scene_packets",
            domain_id=scene_packet.id,
            dependencies=[(sequence_artifact.id, "source", sequence_artifact.content_hash)],
        )

    latest_scenes = await _latest_scene_map(session, chapter_id)
    scene_artifacts: dict[int, Artifact] = {}
    review_artifacts: dict[int, Artifact] = {}
    issues: list[Issue] = []
    existing_signatures: set[str] = set()
    run.current_stage = "issue_snapshot"
    reviewer = await _start_agent_run(
        session,
        run=run,
        agent_name="issue_normalizer",
        agent_role="deterministic",
        stage="issue_snapshot",
        input_artifact_ids=[str(sequence_artifact.id)],
    )
    for scene_no, scene in sorted(latest_scenes.items()):
        scene_artifacts[scene_no] = await _create_artifact(
            session,
            run=run,
            artifact_type="scene_draft",
            body={
                "scene_id": str(scene.id),
                "scene_no": scene.scene_no,
                "version": scene.version,
                "status": scene.status,
                "scene_packet_id": str(scene.scene_packet_id) if scene.scene_packet_id else None,
                "word_count": scene.word_count,
                "prose": scene.prose or "",
            },
            domain_table="scenes",
            domain_id=scene.id,
            dependencies=[
                (
                    scene_packet_artifacts[scene_no].id,
                    "contract",
                    scene_packet_artifacts[scene_no].content_hash,
                )
            ]
            if scene_no in scene_packet_artifacts
            else None,
        )
        critiques = (
            (await session.execute(select(Critique).where(Critique.scene_id == scene.id).order_by(Critique.id)))
            .scalars()
            .all()
        )
        if critiques:
            review_artifacts[scene_no] = await _create_artifact(
                session,
                run=run,
                artifact_type="scene_review_report",
                body={
                    "scene_id": str(scene.id),
                    "scene_no": scene.scene_no,
                    "version": scene.version,
                    "critiques": [
                        {
                            "id": str(critique.id),
                            "reviewer": critique.reviewer,
                            "severity": critique.severity,
                            "note": critique.note,
                            "payload": critique.payload,
                        }
                        for critique in critiques
                    ],
                },
                domain_table="scenes",
                domain_id=scene.id,
                dependencies=[(scene_artifacts[scene_no].id, "source", scene_artifacts[scene_no].content_hash)],
            )
        for critique in critiques:
            payload = critique.payload or {}
            span_start, span_end = _critique_span(payload)
            quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
            claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
            signature = _issue_signature(
                validator=critique.reviewer,
                issue_kind=str(payload.get("kind") or critique.reviewer),
                claim=claim,
                quote=quote if isinstance(quote, str) else None,
                scene_no=scene.scene_no,
            )
            if signature in existing_signatures:
                continue
            existing_signatures.add(signature)
            issue = await _create_issue(
                session,
                run=run,
                artifact_type="scene_review_report",
                artifact_id=review_artifacts[scene_no].id
                if scene_no in review_artifacts
                else scene_artifacts[scene_no].id,
                scene_id=scene.id,
                scene_no=scene.scene_no,
                validator=critique.reviewer,
                issue_kind=str(payload.get("kind") or critique.reviewer),
                severity=str(critique.severity),
                quote=quote if isinstance(quote, str) else None,
                span_start=span_start,
                span_end=span_end,
                claim=claim,
                contract_reference=str(scene.scene_packet_id) if scene.scene_packet_id else None,
                recommended_action=_recommended_action_from_critique(critique),
                confidence=(lambda v: float(v) if isinstance(v, (int, float)) else None)(payload.get("confidence")),
                auto_repair_allowed=scene.id is not None and critique.severity not in ("hard", "block"),
                payload=payload | {"signature": signature},
            )
            issues.append(issue)

    sequence_scenes = (sequence.body or {}).get("scenes") or []
    for item in sequence_scenes:
        if not isinstance(item, dict):
            continue
        scene_no = int(item.get("scene_no") or 0)
        if scene_no and scene_no not in latest_scenes:
            issue = await _create_issue(
                session,
                run=run,
                artifact_type="chapter_sequence",
                artifact_id=sequence_artifact.id,
                scene_id=None,
                scene_no=scene_no,
                validator="chapter_assembly",
                issue_kind="missing_scene",
                severity="block",
                quote=None,
                span_start=None,
                span_end=None,
                claim=f"Scene {scene_no} is required by the chapter sequence but has no current prose draft.",
                contract_reference=str(sequence.id),
                recommended_action="Draft or write the missing scene before final assembly.",
                confidence=1.0,
                auto_repair_allowed=False,
                payload={"scene_no": scene_no, "signature": f"missing_scene:{scene_no}"},
            )
            issues.append(issue)

    timeline = await _create_artifact(
        session,
        run=run,
        artifact_type="draft_run_timeline",
        body={
            "chapter_id": str(chapter_id),
            "scene_order": [
                {
                    "scene_no": item.get("scene_no"),
                    "sequence_function": item.get("scene_function"),
                    "draft_status": (
                        lambda k: (lambda s: str(s.status) if s is not None else "missing")(latest_scenes.get(k))
                    )(int(item.get("scene_no") or 0)),
                    "scene_id": (lambda k: (lambda s: str(s.id) if s is not None else None)(latest_scenes.get(k)))(
                        int(item.get("scene_no") or 0)
                    ),
                }
                for item in sequence_scenes
                if isinstance(item, dict)
            ],
        },
        created_by_agent_run_id=reviewer.id,
        dependencies=[
            (sequence_artifact.id, "source", sequence_artifact.content_hash),
            *[(artifact.id, "prior_scene", artifact.content_hash) for artifact in scene_artifacts.values()],
        ],
    )
    issue_set = await _create_artifact(
        session,
        run=run,
        artifact_type="issue_set",
        body={
            "issue_ids": [str(issue.id) for issue in issues],
            "issue_count": len(issues),
            "timeline_artifact_id": str(timeline.id),
        },
        created_by_agent_run_id=reviewer.id,
        dependencies=[(timeline.id, "source", timeline.content_hash)],
    )
    _finish_agent_run(
        reviewer,
        status=AgentRunStatus.COMPLETED,
        output_artifact_ids=[str(timeline.id), str(issue_set.id)],
        payload={"issue_count": len(issues)},
    )

    await assemble_run(session, run)
    if auto_triage:
        await triage_production_run(session, run.id)
    await _update_run_summary(session, run)
    return run


async def _update_run_summary(session: AsyncSession, run: ProductionRun) -> None:
    issues = (await session.execute(select(Issue).where(Issue.production_run_id == run.id))).scalars().all()
    tasks = (await session.execute(select(RepairTask).where(RepairTask.production_run_id == run.id))).scalars().all()
    verifications = (
        (
            await session.execute(
                select(RepairVerification)
                .join(RepairAttempt, RepairAttempt.id == RepairVerification.repair_attempt_id)
                .join(RepairTask, RepairTask.id == RepairAttempt.repair_task_id)
                .where(RepairTask.production_run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    run.summary_json = {
        "issue_count": len(issues),
        "issues_by_status": dict(Counter(issue.status for issue in issues)),
        "repair_task_count": len(tasks),
        "repair_tasks_by_status": dict(Counter(task.status for task in tasks)),
        "verification_count": len(verifications),
    }


async def list_production_runs(session: AsyncSession, chapter_id: uuid.UUID) -> list[ProductionRun]:
    rows = (
        await session.execute(
            select(ProductionRun)
            .where(ProductionRun.chapter_id == chapter_id)
            .order_by(ProductionRun.created_at.desc())
        )
    ).scalars()
    return list(rows)


async def list_book_production_runs(session: AsyncSession, book_id: uuid.UUID) -> list[ProductionRun]:
    """Every production run in a book, newest first — the book-wide read the Pipeline dashboard fans
    out from (list_production_runs above stays chapter-scoped for the Production tab)."""
    rows = (
        await session.execute(
            select(ProductionRun).where(ProductionRun.book_id == book_id).order_by(ProductionRun.created_at.desc())
        )
    ).scalars()
    return list(rows)


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
        await _update_run_summary(session, run)
        return run

    run.current_stage = "issue_triage"
    triage = await _start_agent_run(
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
        await _record_event(
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
        await _record_event(
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
        await _record_event(
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

    _finish_agent_run(
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
    await _update_run_summary(session, run)
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
    repair_agent = await _start_agent_run(
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
        if job_id is not None:
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
    _finish_agent_run(
        repair_agent,
        status=AgentRunStatus.COMPLETED,
        payload={"repair_task_id": str(task.id), "job_ids": job_ids},
    )
    task.status = RepairTaskStatus.RUNNING
    run.status = ProductionRunStatus.REPAIRING
    run.current_stage = "repair_execution"
    for issue in member_issues:
        issue.status = IssueStatus.REPAIR_QUEUED
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_started",
        stage="repair_execution",
        message=f"Queued {len(target_scenes)} scene revision(s) from the chapter-scoped repair task.",
        payload={"repair_task_id": str(task.id), "job_ids": job_ids},
        agent_run_id=repair_agent.id,
    )
    await _update_run_summary(session, run)
    return task


async def apply_repair_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    human_approved: bool = False,
    approval_reason: str | None = None,
) -> RepairTask:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    # Status guard: protects the drain-vs-human-click race (whichever loses gets a clean 409, never a
    # double application) and keeps Apply off verified/rejected/cancelled rows.
    if task.status not in (RepairTaskStatus.QUEUED, RepairTaskStatus.WAITING_FOR_HUMAN):
        raise ValueError(f"repair task is {task.status}; only queued or waiting_for_human tasks can be applied")
    if task.requires_human_approval and not human_approved and task.human_approved_at is None:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await _record_event(
            session,
            run_id=run.id,
            event_type="human_action_required",
            stage="repair_execution",
            message="Repair task requires explicit approval — use Approve & apply.",
            payload={"repair_task_id": str(task.id), "authority_level": str(task.authority_level)},
        )
        await _update_run_summary(session, run)
        return task
    if human_approved and task.human_approved_at is None:
        task.human_approved_at = datetime.now(UTC)
        await _record_event(
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
                        RepairTask.status.in_(["queued", "running", "repair_queued"]),
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
                await _record_event(
                    session,
                    run_id=run.id,
                    event_type="repair_conflict_detected",
                    message="Overlapping span repair detected; blocking until resolved.",
                    payload={"task_id": str(task.id), "other_task_id": str(ot.id)},
                )

    if conflicts:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await _update_run_summary(session, run)
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
    if job_id is None:
        # Guard: no revision job could be queued (e.g. the scene's chapter is gone), so this task can
        # never produce a changed scene. Escalate to a human instead of marking it RUNNING — otherwise
        # verify would keep raising "no revised scene yet" and the drain would re-apply it forever.
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await _record_event(
            session,
            run_id=run.id,
            event_type="human_action_required",
            stage="repair_execution",
            message="Could not queue a revision for this repair — it needs a human. No changed scene "
            "can be produced automatically.",
            payload={"repair_task_id": str(task.id), "authority_level": str(task.authority_level)},
        )
        await _update_run_summary(session, run)
        return task

    approval = Approval(
        scene_id=scene.id,
        version=scene.version,
        decision=Decision.REVISE,
        target_pass=target_pass,
        feedback=task.instructions,
    )
    session.add(approval)
    repair_agent = await _start_agent_run(
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
    _finish_agent_run(
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_started",
        stage="repair_execution",
        message="Queued a scene revision from the repair task.",
        payload={"repair_task_id": str(task.id), "job_id": str(job_id) if job_id else None},
        agent_run_id=repair_agent.id,
    )
    await _update_run_summary(session, run)
    return task


def _critique_matches_issue(issue: Issue, critique: Critique) -> bool:
    payload = critique.payload or {}
    claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
    quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
    return _issue_signature(
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
    for attempt, revised in revised_by_attempt:
        attempt.revised_text = revised.prose
        attempt.word_count_after = revised.word_count
        base = await session.get(Scene, uuid.UUID(str((attempt.patch_json or {}).get("scene_id"))))
        if base is not None and revised.scene_packet_id != base.scene_packet_id:
            outcome_preserved = False

    revised_scenes = [revised for _, revised in revised_by_attempt]
    scene_no_by_id = {scene.id: scene.scene_no for scene in revised_scenes}
    verifier = await _start_agent_run(
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
        signature = _issue_signature(
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            claim=claim,
            quote=quote if isinstance(quote, str) else None,
            scene_no=critique_scene_no,
        )
        if signature in known_signatures:
            continue
        known_signatures.add(signature)
        issue = await _create_issue(
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
            span_start=_critique_span(payload)[0],
            span_end=_critique_span(payload)[1],
            claim=claim,
            contract_reference=None,
            recommended_action=_recommended_action_from_critique(critique),
            confidence=float(conf_val) if isinstance(conf_val, (int, float)) else None,
            auto_repair_allowed=critique.severity not in ("hard", "block"),
            payload=payload | {"signature": signature},
        )
        created_new_issues.append(issue)

    no_new_issues = not remaining and not created_new_issues
    verdict = (
        RepairVerificationVerdict.ACCEPT
        if no_new_issues
        else (
            RepairVerificationVerdict.ESCALATE_TO_HUMAN
            if any(issue.severity in ("hard", "block") for issue in created_new_issues)
            else RepairVerificationVerdict.NEEDS_ANOTHER_REPAIR
        )
    )
    anchor_attempt = revised_by_attempt[-1][0]
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
        canon_preserved=not any(c.reviewer == "continuity" and c.severity in ("hard", "block") for c in new_critiques),
        scene_outcome_preserved=outcome_preserved,
        voice_preserved=not any(c.reviewer == "voice" and c.severity in ("hard", "block") for c in new_critiques),
        required_beats_preserved=bool(task.instructions),
        reader_state_preserved=not any(
            c.reviewer in {"continuity", "state_drift"} and c.severity in ("hard", "block") for c in new_critiques
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
        },
    )
    session.add(verification)
    await session.flush()
    _finish_agent_run(
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_verified",
        stage="repair_verification",
        message=verification.reason,
        payload={"repair_task_id": str(task.id), "verdict": str(verdict)},
        agent_run_id=verifier.id,
    )
    await assemble_run(session, run)
    await _update_run_summary(session, run)
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

    verifier = await _start_agent_run(
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
        signature = _issue_signature(
            validator=critique.reviewer,
            issue_kind=str(payload.get("kind") or critique.reviewer),
            claim=claim,
            quote=quote if isinstance(quote, str) else None,
            scene_no=revised.scene_no,
        )
        if signature in known_signatures:
            continue
        known_signatures.add(signature)
        issue = await _create_issue(
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
            span_start=_critique_span(payload)[0],
            span_end=_critique_span(payload)[1],
            claim=claim,
            contract_reference=str(revised.scene_packet_id) if revised.scene_packet_id else None,
            recommended_action=_recommended_action_from_critique(critique),
            confidence=float(conf_val) if isinstance(conf_val, (int, float)) else None,
            auto_repair_allowed=critique.severity not in ("hard", "block"),
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
            if any(issue.severity in ("hard", "block") for issue in created_new_issues)
            else RepairVerificationVerdict.NEEDS_ANOTHER_REPAIR
        )
    )
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
            not any(c.reviewer == "continuity" and c.severity in ("hard", "block") for c in new_critiques)
            and direct_checks.get("span_changed", True)
        ),
        scene_outcome_preserved=revised.scene_packet_id == base_scene.scene_packet_id,
        voice_preserved=not any(c.reviewer == "voice" and c.severity in ("hard", "block") for c in new_critiques),
        required_beats_preserved=(
            revised.scene_packet_id == base_scene.scene_packet_id
            and bool(direct_checks.get("instruction_addressed", True))
        ),
        reader_state_preserved=not any(
            c.reviewer in {"continuity", "state_drift"} and c.severity in ("hard", "block") for c in new_critiques
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
        },
    )
    session.add(verification)
    await session.flush()
    _finish_agent_run(
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_verified",
        stage="repair_verification",
        message=verification.reason,
        payload={"repair_task_id": str(task.id), "verdict": str(verdict)},
        agent_run_id=verifier.id,
    )
    await assemble_run(session, run)
    await _update_run_summary(session, run)
    return verification


async def production_run_detail(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    artifacts = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.production_run_id == run_id)
                .order_by(Artifact.created_at, Artifact.version)
            )
        )
        .scalars()
        .all()
    )
    dependencies = (
        (
            await session.execute(
                select(ArtifactDependency)
                .join(Artifact, Artifact.id == ArtifactDependency.artifact_id)
                .where(Artifact.production_run_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    agent_runs = (
        (
            await session.execute(
                select(AgentRun).where(AgentRun.production_run_id == run_id).order_by(AgentRun.created_at)
            )
        )
        .scalars()
        .all()
    )
    events = (
        (
            await session.execute(
                select(AgentEvent).where(AgentEvent.production_run_id == run_id).order_by(AgentEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    issues = (
        (await session.execute(select(Issue).where(Issue.production_run_id == run_id).order_by(Issue.created_at)))
        .scalars()
        .all()
    )
    issue_decisions = (
        (
            await session.execute(
                select(IssueDecision)
                .join(Issue, Issue.id == IssueDecision.issue_id)
                .where(Issue.production_run_id == run_id)
                .order_by(IssueDecision.created_at)
            )
        )
        .scalars()
        .all()
    )
    repair_tasks = (
        (
            await session.execute(
                select(RepairTask).where(RepairTask.production_run_id == run_id).order_by(RepairTask.created_at)
            )
        )
        .scalars()
        .all()
    )
    repair_attempts = (
        (
            await session.execute(
                select(RepairAttempt)
                .join(RepairTask, RepairTask.id == RepairAttempt.repair_task_id)
                .where(RepairTask.production_run_id == run_id)
                .order_by(RepairAttempt.created_at)
            )
        )
        .scalars()
        .all()
    )
    repair_verifications = (
        (
            await session.execute(
                select(RepairVerification)
                .join(RepairAttempt, RepairAttempt.id == RepairVerification.repair_attempt_id)
                .join(RepairTask, RepairTask.id == RepairAttempt.repair_task_id)
                .where(RepairTask.production_run_id == run_id)
                .order_by(RepairVerification.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "run": run,
        "chapter_sequence": sequence,
        "artifacts": artifacts,
        "dependencies": dependencies,
        "agent_runs": agent_runs,
        "events": events,
        "issues": issues,
        "issue_decisions": issue_decisions,
        "repair_tasks": repair_tasks,
        "repair_attempts": repair_attempts,
        "repair_verifications": repair_verifications,
    }


async def derive_chapter_sequence_for_chapter(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterSequence:
    from dominion.workers import production_sequence

    return await production_sequence.derive_chapter_sequence_for_chapter(session, chapter_id)


async def chapter_sequence_qa(session: AsyncSession, sequence_id: uuid.UUID) -> dict[str, Any]:
    from dominion.workers import production_sequence

    return await production_sequence.chapter_sequence_qa(session, sequence_id)


async def update_chapter_sequence(
    session: AsyncSession, sequence_id: uuid.UUID, body: dict[str, Any], reason: str | None = None
) -> ChapterSequence:
    from dominion.workers import production_sequence

    return await production_sequence.update_chapter_sequence(session, sequence_id, body, reason)


async def align_sequence_scene_count(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:
    from dominion.workers import production_sequence

    return await production_sequence.align_sequence_scene_count(session, sequence_id)


async def approve_chapter_sequence(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:
    from dominion.workers import production_sequence

    return await production_sequence.approve_chapter_sequence(session, sequence_id)


async def production_run_events(session: AsyncSession, run_id: uuid.UUID) -> list[AgentEvent]:
    rows = (
        (
            await session.execute(
                select(AgentEvent).where(AgentEvent.production_run_id == run_id).order_by(AgentEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def production_run_artifacts(session: AsyncSession, run_id: uuid.UUID) -> list[Artifact]:
    rows = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.production_run_id == run_id)
                .order_by(Artifact.created_at, Artifact.version)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def artifact_detail(session: AsyncSession, artifact_id: uuid.UUID) -> Artifact:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise ValueError("artifact not found")
    return artifact


async def artifact_dependency_list(session: AsyncSession, artifact_id: uuid.UUID) -> list[ArtifactDependency]:
    rows = (
        (
            await session.execute(
                select(ArtifactDependency)
                .where(ArtifactDependency.artifact_id == artifact_id)
                .order_by(ArtifactDependency.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def production_run_issues(session: AsyncSession, run_id: uuid.UUID) -> list[Issue]:
    rows = (
        (await session.execute(select(Issue).where(Issue.production_run_id == run_id).order_by(Issue.created_at)))
        .scalars()
        .all()
    )
    return list(rows)


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
    await _record_event(
        session,
        run_id=run.id,
        event_type=event_type,
        stage=run.current_stage,
        message=message,
        payload={"issue_id": str(issue.id), "decision": str(decision)},
    )
    await _update_run_summary(session, run)
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_rejected",
        stage=run.current_stage,
        message=reason or "Repair task rejected.",
        payload={"repair_task_id": str(task.id)},
    )
    await _update_run_summary(session, run)
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="repair_rejected",
        stage=run.current_stage,
        message=reason or "Rolled back the latest repair revision.",
        payload={"repair_task_id": str(task.id), "scene_id": str(revised.id)},
    )
    await assemble_run(session, run)
    await _update_run_summary(session, run)
    return task


async def latest_final_chapter(session: AsyncSession, run_id: uuid.UUID) -> Artifact | None:
    return (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.production_run_id == run_id, Artifact.artifact_type == "final_chapter")
                .order_by(Artifact.version.desc(), Artifact.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def latest_chapter_draft_qa(session: AsyncSession, run_id: uuid.UUID) -> Artifact | None:
    return (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.production_run_id == run_id, Artifact.artifact_type == "chapter_draft_qa")
                .order_by(Artifact.version.desc(), Artifact.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def run_final_qa(session: AsyncSession, run_id: uuid.UUID) -> Artifact:
    detail = await production_run_detail(session, run_id)
    run = detail["run"]
    await assemble_run(session, run)
    qa_artifact = await latest_chapter_draft_qa(session, run_id)
    if qa_artifact is None:
        # L6: assembly refused (structured event recorded) — surface the parked stage, not a dump.
        raise ValueError(f"chapter QA unavailable: assembly refused, run is parked in {run.current_stage!r}")
    return qa_artifact


async def approve_final_chapter(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    artifact = await latest_final_chapter(session, run_id)
    if artifact is None:
        raise ValueError("final chapter is not ready")
    # Upgrade explicit final status per production engine rules. Fail-closed: if the artifact can't be
    # stamped (and its hash kept consistent), let it propagate rather than marking the run COMPLETED
    # with an unmarked artifact carrying a stale hash. The stamp is a dict copy + json-backed hash over
    # a JSON body, so it does not raise on valid data — but silence here is a correctness hole (C9).
    body = dict(artifact.body or {})
    body["final_chapter_status"] = "approved_by_human"
    artifact.body = body
    artifact.content_hash = _hash_payload(body)
    run.status = ProductionRunStatus.COMPLETED
    run.current_stage = "final_ready"
    await _record_event(
        session,
        run_id=run.id,
        event_type="run_completed",
        stage=run.current_stage,
        message="Final chapter approved.",
        payload={"artifact_id": str(artifact.id)},
    )
    await _update_run_summary(session, run)
    return run


async def cancel_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    run.status = ProductionRunStatus.CANCELLED
    await _record_event(
        session,
        run_id=run.id,
        event_type="stage_blocked",
        stage=run.current_stage,
        message="Production run cancelled.",
    )
    await _update_run_summary(session, run)
    return run


async def resume_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    has_pending_repairs = any(
        task.status in {RepairTaskStatus.QUEUED, RepairTaskStatus.RUNNING}
        for task in await production_run_repair_tasks(session, run_id)
    )
    run.status = ProductionRunStatus.REPAIRING if has_pending_repairs else ProductionRunStatus.RUNNING
    # L6: resuming a rate-limited run re-enters the ordered flow at the drafting boundary.
    if run.current_stage == run_stages.STAGE_PROVIDER_RATE_LIMITED:
        run.current_stage = run_stages.STAGE_WAITING_FOR_SCENE_DRAFTS
    await _record_event(
        session,
        run_id=run.id,
        event_type="stage_started",
        stage=run.current_stage,
        message="Production run resumed.",
    )
    await _update_run_summary(session, run)
    return run
