"""Shared persistence primitives for production-run lanes.

This module is intentionally lower-level than the run lifecycle owner. It owns reusable
event, artifact, issue, agent-run, hashing, and summary helpers used by production lane modules.
It does not own higher-level run lifecycle, sequence, repair, read-model, or finalization behavior.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import AgentRunStatus
from dominion.shared.models import (
    AgentEvent,
    AgentRun,
    Artifact,
    ArtifactDependency,
    ChapterPacket,
    Critique,
    Issue,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
)
from dominion.workers import activity


def hash_payload(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


async def _next_artifact_version(session: AsyncSession, run_id: uuid.UUID, artifact_type: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.production_run_id == run_id, Artifact.artifact_type == artifact_type)
    )
    return int(count or 0) + 1


async def record_event(
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


async def start_agent_run(
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
        started_at=now(),
    )
    session.add(agent_run)
    await session.flush()
    return agent_run


def finish_agent_run(
    agent_run: AgentRun,
    *,
    status: str,
    output_artifact_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    started = agent_run.started_at or agent_run.created_at
    finished = now()
    agent_run.status = status
    agent_run.output_artifact_ids = output_artifact_ids
    agent_run.payload_json = payload if payload is not None else agent_run.payload_json
    agent_run.error = error
    agent_run.completed_at = finished
    if started is not None:
        agent_run.duration_ms = int((finished - started).total_seconds() * 1000)


async def create_artifact(
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
        content_hash=hash_payload(body),
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
    await record_event(
        session,
        run_id=run.id,
        event_type="artifact_created",
        stage=run.current_stage,
        message=f"{artifact_type} artifact created",
        payload={"artifact_id": str(artifact.id), "artifact_type": artifact_type, "version": artifact.version},
        agent_run_id=created_by_agent_run_id,
    )
    return artifact


async def latest_approved_packet(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterPacket | None:
    return (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == "approved")
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def recommended_action_from_critique(critique: Critique) -> str:
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


def critique_span(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    span = payload.get("span")
    if isinstance(span, (list, tuple)) and len(span) == 2:
        start = span[0] if isinstance(span[0], int) else None
        end = span[1] if isinstance(span[1], int) else None
        return start, end
    start = payload.get("span_start")
    end = payload.get("span_end")
    return (start if isinstance(start, int) else None, end if isinstance(end, int) else None)


def issue_signature(*, validator: str, issue_kind: str, claim: str, quote: str | None, scene_no: int | None) -> str:
    return hash_payload(
        {
            "validator": validator,
            "issue_kind": issue_kind,
            "claim": claim.strip(),
            "quote": (quote or "").strip(),
            "scene_no": scene_no,
        }
    )


async def create_issue(
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
    await record_event(
        session,
        run_id=run.id,
        event_type="issue_created",
        stage=run.current_stage,
        message=f"{validator} raised {issue_kind}",
        payload={"issue_id": str(issue.id), "scene_no": scene_no, "severity": severity},
    )
    return issue


async def update_run_summary(session: AsyncSession, run: ProductionRun) -> None:
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
