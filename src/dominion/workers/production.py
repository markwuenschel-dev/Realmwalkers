"""Durable editorial production orchestration built on top of the contract-first draft system.

The existing draft queue, scene-packet contract, and reviewer pipeline stay authoritative for scene
generation. This module adds a chapter-level production spine around them: durable run state,
structured issues, repair tasks, verification, chapter assembly, and an auditable event trail.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    AgentRunStatus,
    ProductionRunStatus,
    RepairTaskStatus,
)
from dominion.shared.models import (
    AgentEvent,
    AgentRun,
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

# Hoisted to module scope: production_sequence / production_repair do NOT import production back (nor does
# anything in their import closure), so the old circular-import guard — a local re-import inside every
# delegating function — was unnecessary. tests/test_production_import.py pins that this stays acyclic.
from dominion.workers import production_repair, production_sequence
from dominion.workers import production_support as support

# L6 (run orchestration): pure stage machine — pinned stage strings + deterministic gates that must
# fail BEFORE any LLM spend. Persistence stays here; decisions live in run_stages (DB-free, tested).
from dominion.workers import run_stages  # isort: skip


async def latest_chapter_sequence(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterSequence | None:

    return await production_sequence.latest_chapter_sequence(session, chapter_id)


async def latest_draft_timeline(session: AsyncSession, production_run_id: uuid.UUID) -> DraftRunTimeline | None:

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

    return production_sequence.derive_contract_classification(packet_body, open_questions)


def derive_chapter_sequence(packet_body: dict[str, Any]) -> dict[str, Any]:

    return production_sequence.derive_chapter_sequence(packet_body)


def chain_scene_entry_states(body: dict[str, Any]) -> dict[str, Any]:

    return production_sequence.chain_scene_entry_states(body)


def _int_or_none(value: Any) -> int | None:

    return production_sequence._int_or_none(value)


# Articles/particles that would make a whole-word visibility match meaningless ("The Broker" must
# match on "Broker", never on "The").
_ROSTER_NAME_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of"})


def _roster_name_tokens(entry: str) -> list[str]:

    return production_sequence._roster_name_tokens(entry)


def run_chapter_draft_qa(
    sequence_body: dict[str, Any] | None,
    scene_rows: list[dict[str, Any]],
    full_prose: str,
    packet_body: dict[str, Any] | None = None,
    open_questions: dict[str, Any] | None = None,
) -> dict[str, Any]:

    return production_sequence.run_chapter_draft_qa(sequence_body, scene_rows, full_prose, packet_body, open_questions)


def evaluate_chapter_sequence(body: dict[str, Any]) -> dict[str, Any]:

    return production_sequence.evaluate_chapter_sequence(body)


async def ensure_chapter_sequence(session: AsyncSession, packet: ChapterPacket) -> ChapterSequence:

    return await production_sequence.ensure_chapter_sequence(session, packet)


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:

    return await production_sequence._latest_scene_map(session, chapter_id)


async def _scene_packet_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, ScenePacket]:

    return await production_sequence._scene_packet_map(session, chapter_id)


async def assemble_run(session: AsyncSession, run: ProductionRun) -> None:

    return await production_sequence.assemble_run(session, run)


async def queue_draft_jobs_for_missing_sequence_scenes(session: AsyncSession, run: ProductionRun) -> list[uuid.UUID]:

    return await production_sequence.queue_draft_jobs_for_missing_sequence_scenes(session, run)


async def ensure_draft_run_timeline(session: AsyncSession, run: ProductionRun) -> DraftRunTimeline:

    return await production_sequence.ensure_draft_run_timeline(session, run)


async def update_timeline_after_scene(
    session: AsyncSession, production_run_id: uuid.UUID | None, scene: Scene
) -> DraftRunTimeline | None:

    return await production_sequence.update_timeline_after_scene(session, production_run_id, scene)


async def _block_production_on_timeline_failure(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> None:

    return await production_sequence._block_production_on_timeline_failure(session, production_run_id, error)


async def mark_run_provider_rate_limited(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> ProductionRun | None:

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
    packet = await support.latest_approved_packet(session, chapter_id)
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
        source_hash=support.hash_payload(
            {"chapter_packet_id": str(packet.id), "packet_hash": support.hash_payload(packet.body or {})}
        ),
    )
    session.add(run)
    await session.flush()
    await support.record_event(
        session,
        run_id=run.id,
        event_type="run_started",
        stage=run.current_stage,
        message="Production run created.",
        payload={"chapter_id": str(chapter_id), "chapter_no": chapter.chapter_no},
    )

    packet_artifact = await support.create_artifact(
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

    classifier = await support.start_agent_run(
        session,
        run=run,
        agent_name="contract_classifier",
        agent_role="deterministic",
        stage="contract_classification",
        input_artifact_ids=[str(packet_artifact.id)],
    )
    contract_body = derive_contract_classification(packet.body or {}, packet.open_questions)
    contract_artifact = await support.create_artifact(
        session,
        run=run,
        artifact_type="contract_classification",
        body=contract_body,
        created_by_agent_run_id=classifier.id,
        domain_table="chapter_packets",
        domain_id=packet.id,
        dependencies=[(packet_artifact.id, "contract", packet_artifact.content_hash)],
    )
    support.finish_agent_run(
        classifier, status=AgentRunStatus.COMPLETED, output_artifact_ids=[str(contract_artifact.id)]
    )
    await support.record_event(
        session,
        run_id=run.id,
        event_type="stage_completed",
        stage="contract_classification",
        message="Contract classification completed.",
        payload={"artifact_id": str(contract_artifact.id)},
        agent_run_id=classifier.id,
    )

    run.current_stage = "chapter_sequence"
    planner = await support.start_agent_run(
        session,
        run=run,
        agent_name="chapter_sequence_planner",
        agent_role="deterministic",
        stage="chapter_sequence",
        input_artifact_ids=[str(packet_artifact.id), str(contract_artifact.id)],
    )
    sequence = await ensure_chapter_sequence(session, packet)
    sequence_artifact = await support.create_artifact(
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
    support.finish_agent_run(planner, status=AgentRunStatus.COMPLETED, output_artifact_ids=[str(sequence_artifact.id)])

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
        scene_packet_artifacts[scene_no] = await support.create_artifact(
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
    reviewer = await support.start_agent_run(
        session,
        run=run,
        agent_name="issue_normalizer",
        agent_role="deterministic",
        stage="issue_snapshot",
        input_artifact_ids=[str(sequence_artifact.id)],
    )
    for scene_no, scene in sorted(latest_scenes.items()):
        scene_artifacts[scene_no] = await support.create_artifact(
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
            review_artifacts[scene_no] = await support.create_artifact(
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
            span_start, span_end = support.critique_span(payload)
            quote = payload.get("quote") if isinstance(payload.get("quote"), str) else payload.get("context_sentence")
            claim = critique.note or str(payload.get("claim") or f"{critique.reviewer} issue")
            signature = support.issue_signature(
                validator=critique.reviewer,
                issue_kind=str(payload.get("kind") or critique.reviewer),
                claim=claim,
                quote=quote if isinstance(quote, str) else None,
                scene_no=scene.scene_no,
            )
            if signature in existing_signatures:
                continue
            existing_signatures.add(signature)
            issue = await support.create_issue(
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
                recommended_action=support.recommended_action_from_critique(critique),
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
            issue = await support.create_issue(
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

    timeline = await support.create_artifact(
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
    issue_set = await support.create_artifact(
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
    support.finish_agent_run(
        reviewer,
        status=AgentRunStatus.COMPLETED,
        output_artifact_ids=[str(timeline.id), str(issue_set.id)],
        payload={"issue_count": len(issues)},
    )

    await assemble_run(session, run)
    if auto_triage:
        await production_repair.triage_production_run(session, run.id)
    await support.update_run_summary(session, run)
    return run


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

    return await production_repair.triage_production_run(session, run_id)


async def apply_repair_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    human_approved: bool = False,
    approval_reason: str | None = None,
) -> RepairTask:

    return await production_repair.apply_repair_task(
        session, task_id, human_approved=human_approved, approval_reason=approval_reason
    )


async def verify_repair_task(session: AsyncSession, task_id: uuid.UUID) -> RepairVerification:

    return await production_repair.verify_repair_task(session, task_id)


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

    return await production_sequence.derive_chapter_sequence_for_chapter(session, chapter_id)


async def chapter_sequence_qa(session: AsyncSession, sequence_id: uuid.UUID) -> dict[str, Any]:

    return await production_sequence.chapter_sequence_qa(session, sequence_id)


async def update_chapter_sequence(
    session: AsyncSession, sequence_id: uuid.UUID, body: dict[str, Any], reason: str | None = None
) -> ChapterSequence:

    return await production_sequence.update_chapter_sequence(session, sequence_id, body, reason)


async def align_sequence_scene_count(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:

    return await production_sequence.align_sequence_scene_count(session, sequence_id)


async def approve_chapter_sequence(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:

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


async def decide_issue(
    session: AsyncSession,
    issue_id: uuid.UUID,
    *,
    decision: str,
    reason: str | None = None,
    merged_into_issue_id: uuid.UUID | None = None,
) -> Issue:

    return await production_repair.decide_issue(
        session,
        issue_id,
        decision=decision,
        reason=reason,
        merged_into_issue_id=merged_into_issue_id,
    )


async def production_run_repair_tasks(session: AsyncSession, run_id: uuid.UUID) -> list[RepairTask]:

    return await production_repair.production_run_repair_tasks(session, run_id)


async def reject_repair_task(session: AsyncSession, task_id: uuid.UUID, reason: str | None = None) -> RepairTask:

    return await production_repair.reject_repair_task(session, task_id, reason)


async def rollback_repair_task(session: AsyncSession, task_id: uuid.UUID, reason: str | None = None) -> RepairTask:

    return await production_repair.rollback_repair_task(session, task_id, reason)


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
    artifact.content_hash = support.hash_payload(body)
    run.status = ProductionRunStatus.COMPLETED
    run.current_stage = "final_ready"
    await support.record_event(
        session,
        run_id=run.id,
        event_type="run_completed",
        stage=run.current_stage,
        message="Final chapter approved.",
        payload={"artifact_id": str(artifact.id)},
    )
    await support.update_run_summary(session, run)
    return run


async def cancel_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    run.status = ProductionRunStatus.CANCELLED
    await support.record_event(
        session,
        run_id=run.id,
        event_type="stage_blocked",
        stage=run.current_stage,
        message="Production run cancelled.",
    )
    await support.update_run_summary(session, run)
    return run


async def resume_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")

    has_pending_repairs = any(
        task.status in {RepairTaskStatus.QUEUED, RepairTaskStatus.RUNNING}
        for task in await production_repair.production_run_repair_tasks(session, run_id)
    )
    run.status = ProductionRunStatus.REPAIRING if has_pending_repairs else ProductionRunStatus.RUNNING
    # L6: resuming a rate-limited run re-enters the ordered flow at the drafting boundary.
    if run.current_stage == run_stages.STAGE_PROVIDER_RATE_LIMITED:
        run.current_stage = run_stages.STAGE_WAITING_FOR_SCENE_DRAFTS
    await support.record_event(
        session,
        run_id=run.id,
        event_type="stage_started",
        stage=run.current_stage,
        message="Production run resumed.",
    )
    await support.update_run_summary(session, run)
    return run
