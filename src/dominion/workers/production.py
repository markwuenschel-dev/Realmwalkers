"""Durable editorial production orchestration built on top of the contract-first draft system.

The existing draft queue, scene-packet contract, and reviewer pipeline stay authoritative for scene
generation. This module adds a chapter-level production spine around them: durable run state,
structured issues, repair tasks, verification, chapter assembly, and an auditable event trail.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    AgentRunStatus,
    ChapterSequenceStatus,
    Decision,
    IssueDecisionKind,
    IssueStatus,
    JobKind,
    JobStatus,
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
    Beat,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    Critique,
    DraftRunTimeline,
    Issue,
    IssueDecision,
    Job,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
    Scene,
    ScenePacket,
)
from dominion.shared.severity import issue_gates
from dominion.shared.text_match import as_str_list, names_present
from dominion.workers.draft_queue import schedule_contract_first_draft_jobs
from dominion.workers.job_scheduler import schedule_revision
from dominion.workers.length import planner as length_planner
from dominion.workers.packet import latest_approved as latest_approved_chapter_packet
from dominion.workers.packet import master as packet_master
from dominion.workers.packet.validation import leading_roster_name
from dominion.workers.scene_packet import inputs as scene_packet_inputs
from dominion.workers.scene_scope import DUPLICATE_IRREVERSIBLE_BEAT, SCENE_SCOPE_BLEED, evaluate_scene_scope


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
    return (
        (
            await session.execute(
                select(ChapterSequence)
                .where(ChapterSequence.chapter_id == chapter_id)
                .order_by(ChapterSequence.updated_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def latest_draft_timeline(session: AsyncSession, production_run_id: uuid.UUID) -> DraftRunTimeline | None:
    return (
        (
            await session.execute(
                select(DraftRunTimeline)
                .where(DraftRunTimeline.production_run_id == production_run_id)
                .order_by(DraftRunTimeline.updated_at.desc())
            )
        )
        .scalars()
        .first()
    )


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
    return {
        "text": text,
        "classification": classification,
        "blocks_drafting": blocks_drafting,
        "reader_visibility": reader_visibility,
        "drafting_rule": drafting_rule,
        "source_reference": source_reference,
        "confidence": confidence,
    }


def derive_contract_classification(
    packet_body: dict[str, Any], open_questions: dict[str, Any] | None
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    human_decisions = [q for q in as_str_list((open_questions or {}).get("items")) if q]
    intentional_mysteries = [q for q in as_str_list(packet_body.get("required_unanswered_questions")) if q]
    author_only_facts = [q for q in as_str_list(packet_body.get("forbidden_knowledge")) if q]
    forbidden_on_page_facts = [q for q in as_str_list(packet_body.get("forbidden_reveals")) if q]
    surface_mechanisms = [q for q in as_str_list(packet_body.get("canon_locks")) if q]
    deep_mechanisms_withheld = [q for q in as_str_list(packet_body.get("allowed_knowledge")) if q]
    character_behavior_locks = [q for q in as_str_list(packet_body.get("relationship_locks")) if q]
    reader_knowledge_limits = [q for q in as_str_list(packet_body.get("timeline_locks")) if q]
    roster_locks = [
        q
        for q in [*as_str_list(packet_body.get("roster_locks")), *as_str_list(packet_body.get("characters_forbidden"))]
        if q
    ]
    style_constraints = [
        q
        for q in [packet_body.get("emotional_spine"), packet_body.get("one_sentence_spine")]
        if isinstance(q, str) and q.strip()
    ]

    items.extend(
        _contract_item(
            text=q,
            classification="HUMAN_DECISION_REQUIRED",
            blocks_drafting=True,
            reader_visibility="blocked",
            drafting_rule="Do not draft past this unresolved author decision.",
            source_reference="chapter_packet.open_questions",
        )
        for q in human_decisions
    )
    items.extend(
        _contract_item(
            text=q,
            classification="INTENTIONAL_MYSTERY",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Preserve as an intentional mystery; do not resolve it on page.",
            source_reference="chapter_packet.required_unanswered_questions",
        )
        for q in intentional_mysteries
    )
    items.extend(
        _contract_item(
            text=q,
            classification="AUTHOR_ONLY_FACT",
            blocks_drafting=False,
            reader_visibility="author_only",
            drafting_rule="Treat as true internally but keep it off the page.",
            source_reference="chapter_packet.forbidden_knowledge",
        )
        for q in author_only_facts
    )
    items.extend(
        _contract_item(
            text=q,
            classification="FORBIDDEN_ON_PAGE_FACT",
            blocks_drafting=False,
            reader_visibility="forbidden",
            drafting_rule="Do not reveal this fact on page.",
            source_reference="chapter_packet.forbidden_reveals",
        )
        for q in forbidden_on_page_facts
    )
    items.extend(
        _contract_item(
            text=q,
            classification="SURFACE_MECHANISM_LOCKED",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Use this as a locked surface rule; do not improvise around it.",
            source_reference="chapter_packet.canon_locks",
        )
        for q in surface_mechanisms
    )
    items.extend(
        _contract_item(
            text=q,
            classification="DEEP_MECHANISM_WITHHELD",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Allow surface effects only; keep the deep mechanism withheld.",
            source_reference="chapter_packet.allowed_knowledge",
        )
        for q in deep_mechanisms_withheld
    )
    items.extend(
        _contract_item(
            text=q,
            classification="CHARACTER_BEHAVIOR_LOCK",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Do not change the underlying relationship or behavior lock.",
            source_reference="chapter_packet.relationship_locks",
        )
        for q in character_behavior_locks
    )
    items.extend(
        _contract_item(
            text=q,
            classification="READER_KNOWLEDGE_LIMIT",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Keep the reader's knowledge bounded to this limit.",
            source_reference="chapter_packet.timeline_locks",
        )
        for q in reader_knowledge_limits
    )
    items.extend(
        _contract_item(
            text=q,
            classification="ROSTER_LOCK",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Respect the roster lock; do not add or reintroduce blocked participants.",
            source_reference="chapter_packet.roster_locks",
        )
        for q in roster_locks
    )
    items.extend(
        _contract_item(
            text=q,
            classification="STYLE_CONSTRAINT",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Preserve this high-level chapter style constraint while repairing prose.",
            source_reference="chapter_packet.style",
        )
        for q in style_constraints
    )

    return {
        "items": items,
        "human_decisions_required": human_decisions,
        "intentional_mysteries": intentional_mysteries,
        "author_only_facts": author_only_facts,
        "forbidden_on_page_facts": forbidden_on_page_facts,
        "surface_mechanisms": surface_mechanisms,
        "deep_mechanisms_withheld": deep_mechanisms_withheld,
        "character_behavior_locks": character_behavior_locks,
        "reader_knowledge_limits": reader_knowledge_limits,
        "style_constraints": style_constraints,
        "roster_locks": roster_locks,
    }


def derive_chapter_sequence(packet_body: dict[str, Any]) -> dict[str, Any]:
    seeds = [s for s in (packet_body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    chapter_target, chapter_max = scene_packet_inputs.chapter_targets(packet_body, seeds)
    budgets = length_planner.plan_word_budgets(
        chapter_target_words=chapter_target,
        chapter_max_words=chapter_max,
        scene_seeds=seeds,
        chapter_packet_body=packet_body,
    )
    scenes: list[dict[str, Any]] = []
    beat_ownership: dict[str, int] = {}
    duplicates: list[str] = []
    scene_numbers = [int(s.get("scene_no") or 0) for s in seeds if isinstance(s.get("scene_no"), int)]
    ordered = sorted(seeds, key=lambda s: (int(s.get("scene_no") or 0), str(s.get("seed_id"))))
    for index, seed in enumerate(ordered):
        scene_no = int(seed.get("scene_no") or 0)
        seed_id = str(seed.get("seed_id"))
        required = [x for x in as_str_list(seed.get("required_beats")) if x]
        forbidden = [x for x in as_str_list(seed.get("forbidden_beats")) if x]
        for beat in required:
            if beat in beat_ownership and beat_ownership[beat] != scene_no:
                duplicates.append(beat)
            else:
                beat_ownership[beat] = scene_no
        entry = {
            "seed_id": seed_id,
            "scene_no": scene_no,
            "scene_function": str(seed.get("scene_job") or ""),
            "scene_type": str(seed.get("scene_type") or ""),
            "entry_state": str(seed.get("entry_state") or packet_body.get("entry_state") or ""),
            "exit_state": str(seed.get("exit_state") or ""),
            "owned_beats": required,
            "required_beats": required,
            "forbidden_beats": forbidden,
            "reader_knows_at_start": [],
            "reader_learns": [],
            "reader_may_infer_only": [],
            "reader_must_not_know": [],
            "pov_knows_at_start": [],
            "pov_must_not_know": [],
            "must_not_repeat": [],
            "forbidden_restarts": [],
            "word_budget": budgets.get(seed_id, seed.get("word_budget") or {}),
            "depends_on_scene_no": scene_numbers[index - 1] if index > 0 else None,
            "unlocks_scene_no": scene_numbers[index + 1] if index + 1 < len(scene_numbers) else None,
            "independent_draft_allowed": False,
        }
        scenes.append(entry)

    # Compute disciplined scene counts. Never default hard_max_scene_count to len(seeds).
    # Prefer explicit packet composition policy, else estimate from target words (avg ~1200 words/scene).
    explicit_target = packet_body.get("target_scene_count")
    explicit_hard_max = packet_body.get("hard_max_scene_count")
    avg_scene_words = 1200
    estimated_target = max(1, round(chapter_target / avg_scene_words)) if chapter_target else len(seeds)
    # Allow modest headroom; hard cap should come from settings/user or policy, fallback conservatively.
    estimated_hard = max(estimated_target + 2, round(estimated_target * 1.6)) if estimated_target else len(seeds)

    target_scene_count = (
        int(explicit_target) if isinstance(explicit_target, int) and explicit_target > 0 else estimated_target
    )
    hard_max_scene_count = (
        int(explicit_hard_max) if isinstance(explicit_hard_max, int) and explicit_hard_max > 0 else estimated_hard
    )

    # Do NOT inflate hard_max with len(scenes). If the seeds are bloated, the hard_max (derived from
    # words or explicit policy) is the authority; excess scenes must trigger merge/cut required actions.
    target_scene_count = max(target_scene_count, 0)
    hard_max_scene_count = max(hard_max_scene_count, target_scene_count)

    return {
        "chapter_no": packet_body.get("chapter_no"),
        "chapter_job": packet_body.get("chapter_job") or "",
        "chapter_spine": packet_body.get("one_sentence_spine") or "",
        "target_words": chapter_target,
        "max_words": chapter_max or chapter_target,
        "hard_max_words": chapter_max or chapter_target,
        "target_scene_count": target_scene_count,
        "hard_max_scene_count": hard_max_scene_count,
        "global_entry_state": packet_body.get("entry_state") or "",
        "global_exit_state": packet_body.get("exit_state") or "",
        "scenes": scenes,
        "beat_ownership": beat_ownership,
        "forbidden_duplicate_functions": sorted(set(duplicates)),
        "composition_notes": {"must_merge": [], "must_cut": [], "must_expand": []},
    }


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# Articles/particles that would make a whole-word visibility match meaningless ("The Broker" must
# match on "Broker", never on "The").
_ROSTER_NAME_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of"})


def _roster_name_tokens(entry: str) -> list[str]:
    """Substantive name tokens of a roster entry's leading identifier — the whole-word candidates a
    prose visibility check may match on. "Serra Hawthorne (Dead Hand rogue)" -> ["Serra", "Hawthorne"];
    "The Broker" -> ["Broker"]. A match on ANY token counts as a named reference."""
    lead = leading_roster_name(entry)
    return [t for t in re.findall(r"\w+", lead) if len(t) >= 3 and t.lower() not in _ROSTER_NAME_STOPWORDS]


def run_chapter_draft_qa(
    sequence_body: dict[str, Any] | None,
    scene_rows: list[dict[str, Any]],
    full_prose: str,
    packet_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structural ChapterDraftQA performed after chapter assembly.

    Checks for one timeline, duplicate functions/starts, entry/exit continuity,
    repeated onboarding signals, required beats presence (via sequence), forbidden reveals,
    chapter word budget, and (when the chapter packet is supplied) that every characters_present
    roster entry is actually visible in the prose. This is the gate that can block final_chapter
    status. Findings carry `severity` + the derived `blocks_*` facts: "block" gates the final
    chapter, "repair" gates final export only (drafting and human review proceed), "warn" is
    advisory.
    """
    findings: list[dict[str, Any]] = []
    verdict = "pass"

    # Duplicate scene functions among drafted
    func_count: dict[str, list[int]] = defaultdict(list)
    for row in scene_rows:
        fn = str(row.get("scene_function") or row.get("function") or "").strip().lower()
        if fn:
            func_count[fn].append(int(row.get("scene_no") or 0))
    for fn, nos in func_count.items():
        if len(nos) > 1:
            findings.append(
                {
                    "kind": "duplicate_scene_function",
                    "scene_nos": sorted(set(nos)),
                    "function": fn,
                    "severity": "block",
                    **issue_gates("block"),
                }
            )
            verdict = "block"

    # Entry/exit continuity (re-check at assembly time)
    ordered = sorted(scene_rows, key=lambda r: int(r.get("scene_no") or 0))
    for i in range(1, len(ordered)):
        prev_exit = str(ordered[i - 1].get("exit_state") or "").strip()
        this_entry = str(ordered[i].get("entry_state") or "").strip()
        if prev_exit and this_entry and prev_exit != this_entry:
            findings.append(
                {
                    "kind": "entry_exit_mismatch",
                    "from_scene": ordered[i - 1].get("scene_no"),
                    "to_scene": ordered[i].get("scene_no"),
                    "severity": "warn",
                    **issue_gates("warn"),
                }
            )
            if verdict != "block":
                verdict = "warn"

    # Budget
    total_words = sum(int(r.get("word_count") or 0) for r in scene_rows)
    hard_max = (sequence_body or {}).get("hard_max_words")
    if isinstance(hard_max, int) and hard_max > 0 and total_words > hard_max:
        findings.append(
            {
                "kind": "word_budget_exceeded",
                "total": total_words,
                "hard_max": hard_max,
                "severity": "block",
                **issue_gates("block"),
            }
        )
        verdict = "block"

    # Very rough duplicate start / repeated onboarding detection (string level)
    starts = [((r.get("prose") or "")[:120].strip().lower()) for r in scene_rows if (r.get("prose") or "").strip()]
    if len(starts) != len(set(starts)) and len(starts) > 1:
        findings.append(
            {
                "kind": "similar_scene_openings",
                "count": len(starts),
                "severity": "warn",
                **issue_gates("warn"),
            }
        )
        if verdict == "pass":
            verdict = "warn"

    # PRESENT_CHARACTER_NOT_VISIBLE (draft-time positive check): the chapter contract lists a character
    # as present, but the assembled prose never names them — no visible evidence the reader can see.
    # Deterministic proxy for evidence: an exact whole-word reference to any substantive token of the
    # roster entry's leading name (no fuzzy NER). Repair-level: the fix is routed back to the drafter,
    # so it gates final export only — the verdict escalates at most to "warn", never "block".
    present = as_str_list((packet_body or {}).get("characters_present"))
    if present and full_prose.strip():
        for entry in present:
            display = leading_roster_name(entry) or entry
            tokens = _roster_name_tokens(entry)
            if not tokens or names_present([full_prose], tokens):
                continue
            findings.append(
                {
                    "kind": "PRESENT_CHARACTER_NOT_VISIBLE",
                    "character": display,
                    "detail": (
                        f"{display!r} is listed in characters_present but never visibly appears in the "
                        "assembled prose (no named reference found) — add visible evidence or move them "
                        "out of characters_present"
                    ),
                    "severity": "repair",
                    **issue_gates("repair"),
                }
            )
            if verdict == "pass":
                verdict = "warn"

    # Beat-ownership scope guards (recovery L2): deterministic keyword detection derived from the
    # sequence body's beat_ownership. A scene performing a LATER scene's owned beat is
    # scene_scope_bleed; an irreversible beat staged in more than one scene is
    # duplicate_irreversible_beat. Both severities come from scene_scope ("block" for irreversible
    # leaks/duplicates, "repair" otherwise — deterministic checks may block, per shared/severity.py).
    if sequence_body:
        prose_by_no = {int(r.get("scene_no") or 0): str(r.get("prose") or "") for r in scene_rows}
        for scope_issue in evaluate_scene_scope(prose_by_no, sequence_body):
            severity = str(scope_issue.get("severity") or "repair")
            findings.append({**scope_issue, **issue_gates(severity)})
            if severity == "block":
                verdict = "block"
            elif verdict == "pass":
                verdict = "warn"

    return {
        "verdict": verdict,
        "findings": findings,
        "total_words": total_words,
        "scene_count": len(scene_rows),
    }


def evaluate_chapter_sequence(body: dict[str, Any]) -> dict[str, Any]:
    scenes = sorted(
        [scene for scene in (body.get("scenes") or []) if isinstance(scene, dict)],
        key=lambda scene: (int(scene.get("scene_no") or 0), str(scene.get("seed_id") or "")),
    )
    beat_owners: dict[str, list[int]] = defaultdict(list)
    function_owners: dict[str, list[int]] = defaultdict(list)
    function_labels: dict[str, str] = {}
    entry_exit_mismatches: list[dict[str, Any]] = []
    planned_total_words = 0
    planned_max_words = 0
    planned_hard_max_words = 0

    for index, scene in enumerate(scenes):
        scene_no = _int_or_none(scene.get("scene_no"))
        function = str(scene.get("scene_function") or "").strip()
        if function and scene_no is not None:
            key = function.casefold()
            function_labels.setdefault(key, function)
            function_owners[key].append(scene_no)
        for beat in [beat for beat in as_str_list(scene.get("owned_beats") or scene.get("required_beats")) if beat]:
            if scene_no is not None and scene_no not in beat_owners[beat]:
                beat_owners[beat].append(scene_no)

        budget = scene.get("word_budget") if isinstance(scene.get("word_budget"), dict) else {}
        planned_total_words += _int_or_none(budget.get("target")) or 0  # type: ignore[arg-type]
        planned_max_words += _int_or_none(budget.get("max")) or 0  # type: ignore[arg-type]
        planned_hard_max_words += _int_or_none(budget.get("hard_max")) or 0  # type: ignore[arg-type]

        if index == 0:
            continue
        previous = scenes[index - 1]
        previous_exit = str(previous.get("exit_state") or "").strip()
        entry_state = str(scene.get("entry_state") or "").strip()
        if previous_exit and entry_state and previous_exit != entry_state:
            entry_exit_mismatches.append(
                {
                    "previous_scene_no": previous.get("scene_no"),
                    "scene_no": scene_no,
                    "previous_exit_state": previous_exit,
                    "entry_state": entry_state,
                }
            )

    duplicate_beats = [
        {"beat": beat, "scene_nos": scene_nos} for beat, scene_nos in sorted(beat_owners.items()) if len(scene_nos) > 1
    ]
    duplicate_functions = [
        {"scene_function": function_labels[key], "scene_nos": scene_nos}
        for key, scene_nos in sorted(function_owners.items())
        if len(scene_nos) > 1
    ]

    scene_count = len(scenes)
    target_words = _int_or_none(body.get("target_words")) or planned_total_words
    max_words = _int_or_none(body.get("max_words")) or planned_max_words
    hard_max_words = _int_or_none(body.get("hard_max_words")) or planned_hard_max_words
    # Prefer the (now disciplined) values from derive; do not silently fall back to current scene_count
    # for hard_max. Fall back to planned only when explicit missing.
    target_scene_count = _int_or_none(body.get("target_scene_count")) or scene_count
    # Consistent with derive: hard max does not auto-inflate to current scene count.
    hard_max_scene_count = _int_or_none(body.get("hard_max_scene_count")) or max(target_scene_count, scene_count)

    budget_verdict = "pass"
    if scene_count > hard_max_scene_count or (hard_max_words and planned_total_words > hard_max_words):
        budget_verdict = "block"
    elif (max_words and planned_total_words > max_words) or scene_count > target_scene_count:
        budget_verdict = "warn"

    required_actions: list[dict[str, Any]] = []
    required_actions.extend(
        {
            "kind": "merge_scenes",
            "scenes": finding["scene_nos"][:2],
            "reason": f'Beat "{finding["beat"]}" is owned by multiple scenes.',
        }
        for finding in duplicate_beats
    )
    required_actions.extend(
        {
            "kind": "merge_scenes",
            "scenes": finding["scene_nos"][:2],
            "reason": f'Scene function "{finding["scene_function"]}" is duplicated.',
        }
        for finding in duplicate_functions
    )
    if scene_count > hard_max_scene_count:
        required_actions.append(
            {
                "kind": "cut_scene",
                "scenes": [],
                "reason": f"Scene count {scene_count} exceeds hard max {hard_max_scene_count}.",
            }
        )
    if hard_max_words and planned_total_words > hard_max_words:
        required_actions.append(
            {
                "kind": "chapter_compression",
                "scenes": [],
                "reason": f"Planned words {planned_total_words} exceed hard max {hard_max_words}.",
            }
        )

    budget_guard = {
        "verdict": budget_verdict,
        "planned_total_words": planned_total_words,
        "actual_total_words": None,
        "target_words": target_words,
        "hard_max_words": hard_max_words,
        "scene_count": scene_count,
        "target_scene_count": target_scene_count,
        "required_actions": required_actions,
        "warnings": [],
    }

    warnings: dict[str, Any] = {}
    if duplicate_beats:
        warnings["duplicate_beat_ownership"] = duplicate_beats
    if duplicate_functions:
        warnings["duplicate_scene_functions"] = duplicate_functions
    if entry_exit_mismatches:
        warnings["entry_exit_mismatches"] = entry_exit_mismatches
    if budget_verdict != "pass":
        warnings["budget_guard"] = budget_guard

    verdict = "approve"
    if duplicate_beats or duplicate_functions or entry_exit_mismatches or budget_verdict == "block":
        verdict = "block_drafting"
    elif budget_verdict == "warn":
        verdict = "approve_warn"

    return {
        "verdict": verdict,
        "warnings": warnings or None,
        "required_actions": required_actions,
    }


async def ensure_chapter_sequence(session: AsyncSession, packet: ChapterPacket) -> ChapterSequence:
    # The sequence's scene text feeds draft jobs, so it must come from the DERIVED drafter-safe view
    # (`_surface_contract`), never the raw top-level seeds — those are authoritative internal planning
    # data and may carry hidden canonical truth (master packet rule; legacy rows fall back to the body).
    body = derive_chapter_sequence(packet_master.drafter_view(packet.body or {}))
    source_hash = _hash_payload({"chapter_packet_id": str(packet.id), "body": body})
    latest = await latest_chapter_sequence(session, packet.chapter_id)
    evaluation = evaluate_chapter_sequence(body)
    status = (
        ChapterSequenceStatus.BLOCKED if evaluation["verdict"] == "block_drafting" else ChapterSequenceStatus.APPROVED
    )
    qa_verdict = evaluation["verdict"]
    qa_warnings = evaluation["warnings"]
    if latest is None:
        latest = ChapterSequence(
            book_id=packet.book_id,
            chapter_id=packet.chapter_id,
            chapter_packet_id=packet.id,
            status=status,
            target_words=body.get("target_words"),
            max_words=body.get("max_words"),
            hard_max_words=body.get("hard_max_words"),
            target_scene_count=body.get("target_scene_count"),
            hard_max_scene_count=body.get("hard_max_scene_count"),
            body=body,
            qa_verdict=qa_verdict,
            qa_warnings=qa_warnings,
            source_hash=source_hash,
        )
        session.add(latest)
    else:
        latest.chapter_packet_id = packet.id
        latest.status = status
        latest.target_words = body.get("target_words")
        latest.max_words = body.get("max_words")
        latest.hard_max_words = body.get("hard_max_words")
        latest.target_scene_count = body.get("target_scene_count")
        latest.hard_max_scene_count = body.get("hard_max_scene_count")
        latest.body = body
        latest.qa_verdict = qa_verdict
        latest.qa_warnings = qa_warnings
        latest.source_hash = source_hash
        latest.stale_reason = None
    await session.flush()
    return latest


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:
    rows = (
        await session.execute(
            select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.scene_no, Scene.version.desc())
        )
    ).scalars()
    latest: dict[int, Scene] = {}
    for scene in rows:
        if scene.scene_no not in latest:
            if scene.status != SceneStatus.SUPERSEDED:
                latest[scene.scene_no] = scene
            else:
                latest[scene.scene_no] = scene
    return {scene_no: scene for scene_no, scene in latest.items() if scene.status != SceneStatus.SUPERSEDED}


async def _scene_packet_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, ScenePacket]:
    rows = (
        await session.execute(
            select(ScenePacket)
            .where(ScenePacket.chapter_id == chapter_id)
            .order_by(ScenePacket.scene_no, ScenePacket.created_at.desc())
        )
    ).scalars()
    latest: dict[int, ScenePacket] = {}
    for packet in rows:
        latest.setdefault(packet.scene_no, packet)
    return latest


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


async def _apply_real_span_patch(
    session: AsyncSession, run: ProductionRun, task: RepairTask, scene: Scene
) -> dict[str, Any]:
    """Bounded span patch application for SPAN_ONLY repair tasks.

    - Locates target span/quote from task.target_spans or linked issues.
    - Validates anchors (surrounding text).
    - Computes replacement (placeholder generation for this wave; real patch LLM can plug in).
    - Applies to create a new Scene version.
    - Records before/after in patch_json and returns data for attempt.
    """
    current = scene.prose or ""
    before_wc = scene.word_count or 0

    # Use normalized targets (unifies "items" producer shape with legacy)
    targets = _normalized_repair_targets(task)
    quote = None
    st = en = None
    for t in targets:
        if t.quote:
            quote = t.quote
        if t.span_start is not None and t.span_end is not None:
            st, en = t.span_start, t.span_end
        if quote or (st is not None and en is not None):
            break

    if not quote and not (st is not None and en is not None) and task.issue_ids:
        first_issue = await session.get(Issue, uuid.UUID(task.issue_ids[0]))
        if first_issue:
            quote = first_issue.quote

    replacement = None
    if quote and quote in current:
        # Anchor validation: record context
        idx = current.find(quote)
        anchor_before = current[max(0, idx - 20) : idx]
        anchor_after = current[idx + len(quote) : idx + len(quote) + 20]
        # Actual replacement prose: in full system this is produced by a patch-generation agent
        # using instructions + constraints. For this corrective pass we keep the original span
        # text (no marker) so that no-op patches can be tested and verification can reject them.
        replacement = quote
        new_prose = current[:idx] + replacement + current[idx + len(quote) :]
    elif st is not None and en is not None and 0 <= st < en <= len(current):
        anchor_before = current[max(0, st - 20) : st]
        anchor_after = current[en : en + 20]
        original_span = current[st:en]
        replacement = original_span
        new_prose = current[:st] + replacement + current[en:]
    else:
        # Fallback: no precise target -> treat as no-op (new version created for flow, but text unchanged)
        new_prose = current
        replacement = current[:100] + "..." if current else ""
        anchor_before = ""
        anchor_after = ""

    # Create new versioned scene for the patch
    new_scene = Scene(
        chapter_id=scene.chapter_id,
        scene_no=scene.scene_no,
        version=scene.version + 1,
        parent_scene_id=scene.id,
        status=SceneStatus.PENDING_REVIEW,
        scene_packet_id=scene.scene_packet_id,
        word_count=len(new_prose.split()) if new_prose else 0,
        length_status=scene.length_status,
        prose=new_prose,
        prose_source="agent+repair_patch",
        agent_original=scene.agent_original,
        passes_run=scene.passes_run,
        token_count=scene.token_count,
        model=scene.model,
    )
    session.add(new_scene)
    await session.flush()

    after_wc = new_scene.word_count or 0
    patch_json = {
        "type": "span_patch",
        "target_spans": task.target_spans,
        "quote": quote,
        "before": current[st:en] if (st is not None and en is not None) else quote,
        "after": replacement,
        "anchor_before_preserved": anchor_before,
        "anchor_after_preserved": anchor_after,
        "instructions": task.instructions,
        "word_delta": after_wc - before_wc,
    }

    return {
        "patch_json": patch_json,
        "revised_text": new_prose,
        "word_count_before": before_wc,
        "word_count_after": after_wc,
        "change_summary": "Applied bounded span patch and created new scene version.",
        "new_scene_id": str(new_scene.id),
    }


def _highest_authority(issues: list[Issue]) -> str:
    authorities = [_infer_authority(issue) for issue in issues]
    return max(authorities, key=lambda authority: _AUTHORITY_RANK.get(authority, -1))


async def _queue_repair_task_from_issues(
    session: AsyncSession,
    *,
    run: ProductionRun,
    issues: list[Issue],
    agent_run_id: uuid.UUID | None = None,
) -> tuple[RepairTask, Artifact]:
    first = issues[0]
    repair_kind = _infer_repair_kind(first)
    authority_level = _highest_authority(issues)
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=run.chapter_id,
        scene_id=first.scene_id,
        scene_no=first.scene_no,
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
                f"Repair kind: {repair_kind}. Authority: {authority_level}.",
                *[f"- {issue.recommended_action} Claim: {issue.claim}" for issue in issues],
            ]
        ),
        preserve=[
            f"Preserve scene outcome for scene {first.scene_no}."
            if first.scene_no is not None
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
    latest_scenes = await _latest_scene_map(session, run.chapter_id)
    chapter = await session.get(Chapter, run.chapter_id)
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    if chapter is None:
        return
    seq_by_no = {}
    if sequence and sequence.body:
        for it in sequence.body.get("scenes") or []:
            if isinstance(it, dict):
                seq_by_no[int(it.get("scene_no") or 0)] = it

    scene_rows = []
    for scene in sorted(latest_scenes.values(), key=lambda s: s.scene_no):
        seq_item = seq_by_no.get(scene.scene_no, {})
        row = {
            "scene_id": str(scene.id),
            "scene_no": scene.scene_no,
            "version": scene.version,
            "status": str(scene.status),
            "word_count": scene.word_count,
            "scene_packet_id": str(scene.scene_packet_id) if scene.scene_packet_id else None,
            "prose": scene.prose or "",
            # Enrich for ChapterDraftQA and downstream consumers
            "scene_function": seq_item.get("scene_function") or seq_item.get("scene_job"),
            "entry_state": seq_item.get("entry_state"),
            "exit_state": seq_item.get("exit_state"),
            "owned_beats": seq_item.get("owned_beats") or seq_item.get("required_beats"),
            "required_beats": seq_item.get("required_beats"),
            "forbidden_beats": seq_item.get("forbidden_beats"),
            "reader_learns": seq_item.get("reader_learns"),
            "reader_must_not_know": seq_item.get("reader_must_not_know"),
            "word_budget": seq_item.get("word_budget"),
        }
        scene_rows.append(row)
    chapter_text = "\n\n".join((row["prose"] or "").strip() for row in scene_rows if (row["prose"] or "").strip())
    scene_count_expected = len((sequence.body or {}).get("scenes") or []) if sequence is not None else len(scene_rows)
    missing_scene_nos = []
    if sequence is not None:
        expected = {
            int(item.get("scene_no") or 0) for item in (sequence.body.get("scenes") or []) if isinstance(item, dict)
        }
        missing_scene_nos = sorted(expected - set(latest_scenes))

    # Keep DraftRunTimeline live as scenes are added during the run
    if sequence is not None:
        await ensure_draft_run_timeline(session, run)

    approved_packet = await latest_approved_chapter_packet(session, run.chapter_id)
    packet_body = approved_packet.body if approved_packet is not None else None
    chapter_draft_qa = run_chapter_draft_qa(
        sequence.body if sequence else None,
        scene_rows,
        chapter_text,
        packet_body=packet_body if isinstance(packet_body, dict) else None,
    )

    issues = (
        (await session.execute(select(Issue).where(Issue.production_run_id == run.id).order_by(Issue.created_at)))
        .scalars()
        .all()
    )
    tasks = (
        (
            await session.execute(
                select(RepairTask).where(RepairTask.production_run_id == run.id).order_by(RepairTask.created_at)
            )
        )
        .scalars()
        .all()
    )
    severities = Counter(issue.severity for issue in issues)
    open_issue_statuses = {
        IssueStatus.PROPOSED,
        IssueStatus.ACCEPTED,
        IssueStatus.REPAIR_QUEUED,
        IssueStatus.REPAIRED,
        IssueStatus.ESCALATED,
    }
    open_issues = [issue for issue in issues if issue.status in open_issue_statuses]
    qa_block = chapter_draft_qa.get("verdict") == "block"
    ready_for_human = not open_issues and not missing_scene_nos and not qa_block

    chapter_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_draft",
        body={
            "chapter_id": str(run.chapter_id),
            "chapter_no": chapter.chapter_no,
            "title": chapter.title,
            "pov": chapter.pov,
            "scene_count": len(scene_rows),
            "scenes": scene_rows,
            "prose": chapter_text,
        },
        domain_table="chapters",
        domain_id=run.chapter_id,
    )
    qa_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_draft_qa",
        body={
            "scene_count_actual": len(scene_rows),
            "scene_count_expected": scene_count_expected,
            "missing_scene_nos": missing_scene_nos,
            "issue_counts_by_severity": dict(severities),
            "open_issue_count": len(open_issues),
            "repair_task_count": len(tasks),
            "latest_scene_statuses": {str(k): str(v.status) for k, v in latest_scenes.items()},
            "chapter_draft_qa": chapter_draft_qa,
        },
        dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
    )
    # Persist beat-ownership scope findings as Issue rows so triage can cluster
    # scene_scope_bleed / duplicate_irreversible_beat (recovery L2). Signature-deduped so
    # re-assembly never duplicates them. Severity mapping: deterministic "block" -> "hard".
    scope_signatures = {
        str((issue.payload_json or {}).get("signature")) for issue in issues if isinstance(issue.payload_json, dict)
    }
    for finding in chapter_draft_qa.get("findings") or []:
        kind = str(finding.get("kind") or "")
        if kind not in (SCENE_SCOPE_BLEED, DUPLICATE_IRREVERSIBLE_BEAT):
            continue
        claim = str(finding.get("detail") or finding.get("beat") or kind)
        scene_no = finding.get("scene_no") if isinstance(finding.get("scene_no"), int) else None
        signature = _issue_signature(
            validator="scene_scope", issue_kind=kind, claim=claim, quote=None, scene_no=scene_no
        )
        if signature in scope_signatures:
            continue
        scope_signatures.add(signature)
        bleed_scene = latest_scenes.get(scene_no) if scene_no is not None else None
        await _create_issue(
            session,
            run=run,
            artifact_type="chapter_draft_qa",
            artifact_id=qa_artifact.id,
            scene_id=bleed_scene.id if bleed_scene is not None else None,
            scene_no=scene_no,
            validator="scene_scope",
            issue_kind=kind,
            severity="hard" if finding.get("severity") == "block" else "warn",
            quote=None,
            span_start=None,
            span_end=None,
            claim=claim,
            contract_reference=str(sequence.id) if sequence is not None else None,
            recommended_action=(
                "Cut the leaked beat from this scene; only its owning scene may stage it."
                if kind == SCENE_SCOPE_BLEED
                else "Keep the irreversible beat only in its owning scene and remove the repeats."
            ),
            confidence=1.0,
            auto_repair_allowed=False,
            payload={**finding, "signature": signature},
        )
    await _create_artifact(
        session,
        run=run,
        artifact_type="reader_simulation",
        body={
            "missing_scene_nos": missing_scene_nos,
            "likely_confusions": [issue.claim for issue in issues if issue.severity == "hard"][:5],
            "open_issues": [issue.claim for issue in open_issues[:10]],
        },
        dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
    )
    await _create_artifact(
        session,
        run=run,
        artifact_type="agent_evaluation",
        body={
            "ready_for_human": ready_for_human,
            "blocking_issues": [issue.claim for issue in open_issues if issue.severity == "hard"],
            "issue_count": len(issues),
            "repair_task_count": len(tasks),
            "missing_scene_nos": missing_scene_nos,
        },
        dependencies=[
            (chapter_artifact.id, "source", chapter_artifact.content_hash),
            (qa_artifact.id, "verification_target", qa_artifact.content_hash),
        ],
    )
    if ready_for_human:
        final_status = "fully_validated" if chapter_draft_qa.get("verdict") == "pass" else "validated_with_warnings"
        await _create_artifact(
            session,
            run=run,
            artifact_type="final_chapter",
            body={
                "chapter_id": str(run.chapter_id),
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "pov": chapter.pov,
                "prose": chapter_text,
                "scene_count": len(scene_rows),
                "final_chapter_status": final_status,
            },
            dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
        )
        run.status = ProductionRunStatus.COMPLETED
        run.current_stage = "final_ready"
        await _record_event(
            session,
            run_id=run.id,
            event_type="final_ready",
            stage="final_ready",
            message="Final chapter is ready for human review.",
            payload={"chapter_id": str(run.chapter_id)},
        )
    else:
        run.current_stage = "chapter_assembly"
        if run.status == ProductionRunStatus.RUNNING:
            run.status = ProductionRunStatus.WAITING_FOR_HUMAN


async def queue_draft_jobs_for_missing_sequence_scenes(session: AsyncSession, run: ProductionRun) -> list[uuid.UUID]:
    """Queue DRAFT jobs for ChapterSequence scenes that have an approved ScenePacket but lack prose.

    All draft paths go through dominion.workers.draft_queue (contract-first).
    Production drives by identifying the targets from ChapterSequence and delegating to the scheduler.
    """
    if run.current_stage == "timeline_failed":
        await _record_event(
            session,
            run_id=run.id,
            event_type="draft_blocked",
            stage=run.current_stage or "draft_missing",
            message="Production blocked due to prior timeline update failure.",
            payload={"production_run_id": str(run.id)},
        )
        return []

    sequence = await latest_chapter_sequence(session, run.chapter_id)
    if not sequence or not sequence.body:
        return []

    seq_scenes = sorted(
        [s for s in (sequence.body.get("scenes") or []) if isinstance(s, dict)],
        key=lambda s: int(s.get("scene_no") or 0),
    )
    existing_scenes = await _latest_scene_map(session, run.chapter_id)
    scene_packets = await _scene_packet_map(session, run.chapter_id)

    chapter = await session.get(Chapter, run.chapter_id)

    for item in seq_scenes:
        sno = int(item.get("scene_no") or 0)
        if sno <= 0:
            continue
        existing = existing_scenes.get(sno)
        if existing and (existing.prose or "").strip():
            continue  # already has prose

        # Dependency gate: only queue the next if its depends_on is satisfied
        dep_no = item.get("depends_on_scene_no")
        if dep_no is not None:
            dep = existing_scenes.get(int(dep_no))
            if not (dep and (dep.prose or "").strip()):
                continue

        sp = scene_packets.get(sno)
        if sp is None or getattr(sp, "status", None) != "approved":
            await _record_event(
                session,
                run_id=run.id,
                event_type="draft_blocked",
                stage=run.current_stage or "draft_missing",
                message=f"Scene {sno} requires an approved ScenePacket before drafting.",
                payload={"scene_no": sno, "required_action": "derive/approve ScenePacket for sequence scene"},
            )
            return []

        beat = (
            await session.execute(select(Beat).where(Beat.chapter_id == run.chapter_id, Beat.scene_no == sno))
        ).scalar_one_or_none()
        if beat is None:
            await _record_event(
                session,
                run_id=run.id,
                event_type="draft_blocked",
                stage=run.current_stage or "draft_missing",
                message=f"No approved Beat for sequence scene {sno}.",
                payload={"scene_no": sno},
            )
            return []

        # Queue *only* this next one
        if chapter:
            await schedule_contract_first_draft_jobs(
                session,
                chapter=chapter,
                beats=[beat],
                run=None,
                skip_drafted=True,
                production_run_id=run.id,
            )
            recent = (
                (
                    await session.execute(
                        select(Job)
                        .where(
                            Job.chapter_id == run.chapter_id,
                            Job.kind == JobKind.DRAFT,
                            Job.status == JobStatus.QUEUED,
                            Job.scene_no == sno,
                        )
                        .order_by(Job.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .all()
            )
            created_job_ids = [j.id for j in recent]
            for jid in created_job_ids:
                await _record_event(
                    session,
                    run_id=run.id,
                    event_type="draft_queued",
                    stage="draft_missing",
                    message=f"Draft job queued (sequentially) for sequence scene {sno}.",
                    payload={"job_id": str(jid), "scene_no": sno, "production_run_id": str(run.id)},
                )
            if created_job_ids:
                run.current_stage = "awaiting_scene_drafts"
            return created_job_ids

    return []


async def ensure_draft_run_timeline(session: AsyncSession, run: ProductionRun) -> DraftRunTimeline:
    """Ensure a durable live DraftRunTimeline exists for this production run.

    Seeds from sequence globals and current scene state. This becomes the source of truth for
    sequential drafting memory across scenes in the run.
    """
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    latest_scenes_map = await _latest_scene_map(session, run.chapter_id)

    seq_body = (sequence.body or {}) if sequence else {}
    seq_scenes = seq_body.get("scenes") or []

    drafted_scenes: list[dict[str, Any]] = []
    spent_beats: list[str] = []
    reader_learned: list[str] = []
    current_exit = seq_body.get("global_entry_state") or seq_body.get("global_exit_state")

    for item in seq_scenes:
        if not isinstance(item, dict):
            continue
        sno = int(item.get("scene_no") or 0)
        sc = latest_scenes_map.get(sno)
        entry = {
            "scene_no": sno,
            "scene_function": item.get("scene_function"),
            "status": str(sc.status) if sc else "missing",
            "word_count": sc.word_count if sc else None,
            "has_prose": bool((sc.prose or "").strip()) if sc else False,
        }
        drafted_scenes.append(entry)
        if sc and sc.prose:
            # Seed naive aggregates from owned beats on sequence (real extraction would parse prose too)
            for b in as_str_list(item.get("owned_beats") or item.get("required_beats")):
                if b not in spent_beats:
                    spent_beats.append(b)

    tl = await latest_draft_timeline(session, run.id)
    if tl is None:
        tl = DraftRunTimeline(
            production_run_id=run.id,
            chapter_id=run.chapter_id,
            current_scene_no=None,
            chapter_so_far_summary=seq_body.get("chapter_spine"),
            current_exit_state=current_exit,
            spent_beats=spent_beats or [],
            reader_learned=reader_learned or [],
            pov_learned={},
            must_not_repeat_after=[],
            drafted_scenes=drafted_scenes,
        )
        session.add(tl)
    else:
        tl.drafted_scenes = drafted_scenes
        tl.spent_beats = spent_beats or tl.spent_beats
        tl.current_exit_state = current_exit or tl.current_exit_state
        tl.updated_at = _now()

    await session.flush()

    # Keep artifact in sync for UI (the model is the live one)
    await _create_artifact(
        session,
        run=run,
        artifact_type="draft_run_timeline",
        body={
            "production_run_id": str(run.id),
            "current_scene_no": tl.current_scene_no,
            "current_exit_state": tl.current_exit_state,
            "spent_beats": tl.spent_beats or [],
            "reader_learned": tl.reader_learned or [],
            "must_not_repeat_after": tl.must_not_repeat_after or [],
            "chapter_so_far_summary": tl.chapter_so_far_summary,
            "drafted_scenes": tl.drafted_scenes or [],
        },
        dependencies=[],
    )
    return tl


async def update_timeline_after_scene(
    session: AsyncSession, production_run_id: uuid.UUID | None, scene: Scene
) -> DraftRunTimeline | None:
    """Update (or create) the DraftRunTimeline immediately after a scene for this production run persists.

    Consumes the just-drafted Scene + its ScenePacket + the ChapterSequence item to compute
    the new cumulative state. This is the critical post-persist step for sequential memory.
    """
    if production_run_id is None:
        return None
    run = await session.get(ProductionRun, production_run_id)
    if run is None:
        return None
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    sp = await session.get(ScenePacket, scene.scene_packet_id) if scene.scene_packet_id else None

    seq_item: dict[str, Any] = {}
    if sequence and sequence.body:
        for it in sequence.body.get("scenes") or []:
            if isinstance(it, dict) and int(it.get("scene_no") or 0) == scene.scene_no:
                seq_item = it
                break

    tl = await latest_draft_timeline(session, production_run_id)
    if tl is None:
        tl = DraftRunTimeline(
            production_run_id=production_run_id,
            chapter_id=run.chapter_id,
            current_scene_no=scene.scene_no,
            chapter_so_far_summary=(sequence.body or {}).get("chapter_spine") if sequence else None,
            current_exit_state=None,
            spent_beats=[],
            reader_learned=[],
            pov_learned={},
            must_not_repeat_after=[],
            drafted_scenes=[],
        )
        session.add(tl)

    # Compute updates
    tl.current_scene_no = scene.scene_no

    exit_state = None
    if sp and isinstance(sp.body, dict):
        exit_state = sp.body.get("exit_state")
    if not exit_state:
        exit_state = seq_item.get("exit_state")
    if exit_state:
        tl.current_exit_state = exit_state

    # spent_beats union
    owned = as_str_list(seq_item.get("owned_beats") or seq_item.get("required_beats"))
    spent = list(tl.spent_beats or [])
    for b in owned:
        if b and b not in spent:
            spent.append(b)
    tl.spent_beats = spent

    # reader learned from packet
    learned = list(tl.reader_learned or [])
    if sp and isinstance(sp.body, dict):
        learned_d = (sp.body.get("learned_during_scene") or {}).get("reader_must_learn") or []
        for item in as_str_list(learned_d):
            if item and item not in learned:
                learned.append(item)
    tl.reader_learned = learned

    # must_not_repeat
    mnr = list(tl.must_not_repeat_after or [])
    for item in as_str_list(seq_item.get("must_not_repeat")):
        if item and item not in mnr:
            mnr.append(item)
    tl.must_not_repeat_after = mnr

    # drafted_scenes list
    ds = list(tl.drafted_scenes or [])
    entry = {
        "scene_no": scene.scene_no,
        "scene_id": str(scene.id),
        "version": scene.version,
        "word_count": scene.word_count,
        "status": str(scene.status),
        "exit_state": exit_state,
    }
    # replace if exists
    ds = [d for d in ds if d.get("scene_no") != scene.scene_no]
    ds.append(entry)
    ds.sort(key=lambda d: d.get("scene_no") or 0)
    tl.drafted_scenes = ds

    if not tl.chapter_so_far_summary and sequence:
        tl.chapter_so_far_summary = (sequence.body or {}).get("chapter_spine")

    tl.updated_at = _now()
    await session.flush()

    # Refresh artifact for visibility (best effort)
    try:
        await _create_artifact(
            session,
            run=run,
            artifact_type="draft_run_timeline",
            body={
                "production_run_id": str(production_run_id),
                "current_scene_no": tl.current_scene_no,
                "current_exit_state": tl.current_exit_state,
                "spent_beats": tl.spent_beats,
                "reader_learned": tl.reader_learned,
                "drafted_scenes": tl.drafted_scenes,
            },
        )
    except Exception:
        pass

    return tl


async def _block_production_on_timeline_failure(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> None:
    """Block the production run from advancing when timeline memory update fails after a scene.

    Do not rollback the drafted prose. Emit a hard event so the UI and queue logic see the failure.
    Subsequent attempts to queue the next scene in sequence will see the blocked state.
    """
    run = await session.get(ProductionRun, production_run_id)
    if run is None:
        return
    run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    run.current_stage = "timeline_failed"
    await _record_event(
        session,
        run_id=run.id,
        event_type="timeline_update_failed",
        stage="timeline_failed",
        message="Timeline update failed after scene draft. Production blocked.",
        payload={"error": error, "scene_no": getattr(run, "current_scene_no", None)},
    )
    await session.flush()


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
                auto_repair_allowed=scene.id is not None and critique.severity != "hard",
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
                severity="hard",
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


async def triage_production_run(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    issues = (
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
    if not issues:
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
    grouped: dict[tuple[uuid.UUID | None, int | None, str, str], list[Issue]] = defaultdict(list)
    created_tasks: list[RepairTask] = []
    created_artifacts: list[Artifact] = []
    for issue in issues:
        if issue.issue_kind == "missing_scene":
            decision = IssueDecisionKind.ESCALATE
            issue.status = IssueStatus.ESCALATED
            reason = "Missing scenes are structural gaps and require author intervention."
        elif issue.severity == "info":
            decision = IssueDecisionKind.REJECT
            issue.status = IssueStatus.REJECTED
            reason = "Info-level notes stay advisory and do not create repair work by default."
        else:
            decision = IssueDecisionKind.ACCEPT
            issue.status = IssueStatus.ACCEPTED
            reason = "Accepted for repair task generation."
            grouped[(issue.scene_id, issue.scene_no, _infer_repair_kind(issue), _infer_authority(issue))].append(issue)
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

    for _, grouped_issues in grouped.items():
        task, artifact = await _queue_repair_task_from_issues(
            session,
            run=run,
            issues=grouped_issues,
            agent_run_id=triage.id,
        )
        created_tasks.append(task)
        created_artifacts.append(artifact)

    _finish_agent_run(
        triage,
        status=AgentRunStatus.COMPLETED,
        output_artifact_ids=[str(artifact.id) for artifact in created_artifacts],
    )
    run.status = ProductionRunStatus.REPAIRING if created_tasks else ProductionRunStatus.WAITING_FOR_HUMAN
    run.current_stage = "repair_queue" if created_tasks else "chapter_assembly"
    await _update_run_summary(session, run)
    return run


async def apply_repair_task(session: AsyncSession, task_id: uuid.UUID) -> RepairTask:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
    if task.requires_human_approval:
        task.status = RepairTaskStatus.WAITING_FOR_HUMAN
        run.status = ProductionRunStatus.WAITING_FOR_HUMAN
        await _update_run_summary(session, run)
        return task
    if task.scene_id is None:
        raise ValueError("repair task does not target a concrete scene")
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

    if task.authority_level == RepairAuthorityLevel.SPAN_ONLY and task.target_spans:
        # 8. Actual bounded span patch application (not full revision)
        patch_result = await _apply_real_span_patch(session, run, task, scene)
        patch_json = patch_result["patch_json"]
        job_id = None  # direct apply, no separate job
        # Create a lightweight agent_run record for traceability
        repair_agent = await _start_agent_run(
            session,
            run=run,
            agent_name="span_patch_applier",
            agent_role="deterministic",
            stage="repair_execution",
            input_artifact_ids=[],
            payload={"repair_task_id": str(task.id), "mode": "span_patch"},
        )
        latest_attempt_no = await session.scalar(
            select(func.max(RepairAttempt.attempt_no)).where(RepairAttempt.repair_task_id == task.id)
        )
        attempt = RepairAttempt(
            repair_task_id=task.id,
            agent_run_id=repair_agent.id,
            attempt_no=int(latest_attempt_no or 0) + 1,
            model="patch",
            patch_json=patch_json,
            revised_text=patch_result["revised_text"],
            change_summary=patch_result.get("change_summary", "Span patch applied directly."),
            issues_addressed=list(task.issue_ids),
            new_risks=[],
            word_count_before=patch_result["word_count_before"],
            word_count_after=patch_result["word_count_after"],
        )
        session.add(attempt)
        await session.flush()
        _finish_agent_run(repair_agent, status=AgentRunStatus.COMPLETED, payload={"repair_attempt_id": str(attempt.id)})
        task.status = RepairTaskStatus.RUNNING
        run.status = ProductionRunStatus.REPAIRING
        run.current_stage = "repair_execution"
        for iid in task.issue_ids:
            iss = await session.get(Issue, uuid.UUID(iid))
            if iss is not None:
                iss.status = IssueStatus.REPAIR_QUEUED
        await _record_event(
            session,
            run_id=run.id,
            event_type="repair_started",
            stage="repair_execution",
            message="Span-only patch applied directly to scene prose.",
            payload={"repair_task_id": str(task.id)},
            agent_run_id=repair_agent.id,
        )
        await _update_run_summary(session, run)
        return task

    # Non-span: normal path (full revision job)
    # 5. For span_only, prepare explicit patch_json (target span + instructions as patch spec)
    # Real replacement generation belongs to a patch agent; here we wire the bounded path.
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
    job_id = await schedule_revision(session, scene, target_pass=target_pass, production_run_id=task.production_run_id)
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


async def verify_repair_task(session: AsyncSession, task_id: uuid.UUID) -> RepairVerification:
    task = await session.get(RepairTask, task_id)
    if task is None:
        raise ValueError("repair task not found")
    run = await session.get(ProductionRun, task.production_run_id)
    if run is None:
        raise ValueError("production run not found")
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
            auto_repair_allowed=critique.severity != "hard",
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
            if any(issue.severity == "hard" for issue in created_new_issues)
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
            not any(c.reviewer == "continuity" and c.severity == "hard" for c in new_critiques)
            and direct_checks.get("span_changed", True)
        ),
        scene_outcome_preserved=revised.scene_packet_id == base_scene.scene_packet_id,
        voice_preserved=not any(c.reviewer == "voice" and c.severity == "hard" for c in new_critiques),
        required_beats_preserved=(
            revised.scene_packet_id == base_scene.scene_packet_id
            and bool(direct_checks.get("instruction_addressed", True))
        ),
        reader_state_preserved=not any(
            c.reviewer in {"continuity", "state_drift"} and c.severity == "hard" for c in new_critiques
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
    packet = await _latest_approved_packet(session, chapter_id)
    if packet is None:
        raise ValueError("no approved chapter packet for this chapter")
    return await ensure_chapter_sequence(session, packet)


async def chapter_sequence_qa(session: AsyncSession, sequence_id: uuid.UUID) -> dict[str, Any]:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    evaluation = evaluate_chapter_sequence(sequence.body or {})
    sequence.qa_verdict = evaluation["verdict"]
    sequence.qa_warnings = evaluation["warnings"]
    sequence.status = (
        ChapterSequenceStatus.BLOCKED if evaluation["verdict"] == "block_drafting" else ChapterSequenceStatus.APPROVED
    )
    await session.flush()
    return evaluation


async def update_chapter_sequence(
    session: AsyncSession, sequence_id: uuid.UUID, body: dict[str, Any], reason: str | None = None
) -> ChapterSequence:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    sequence.body = body
    sequence.target_words = _int_or_none(body.get("target_words"))
    sequence.max_words = _int_or_none(body.get("max_words"))
    sequence.hard_max_words = _int_or_none(body.get("hard_max_words"))
    sequence.target_scene_count = _int_or_none(body.get("target_scene_count"))
    sequence.hard_max_scene_count = _int_or_none(body.get("hard_max_scene_count"))
    sequence.source_hash = _hash_payload({"chapter_sequence_id": str(sequence.id), "body": body})
    if reason is not None:
        sequence.stale_reason = reason
    await chapter_sequence_qa(session, sequence.id)
    return sequence


async def approve_chapter_sequence(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    evaluation = await chapter_sequence_qa(session, sequence_id)
    if evaluation["verdict"] == "block_drafting":
        raise ValueError("chapter sequence is blocked by QA")
    sequence.status = ChapterSequenceStatus.APPROVED
    await session.flush()
    return sequence


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
        raise ValueError("chapter QA artifact not found")
    return qa_artifact


async def approve_final_chapter(session: AsyncSession, run_id: uuid.UUID) -> ProductionRun:
    run = await session.get(ProductionRun, run_id)
    if run is None:
        raise ValueError("production run not found")
    artifact = await latest_final_chapter(session, run_id)
    if artifact is None:
        raise ValueError("final chapter is not ready")
    # Upgrade explicit final status per production engine rules
    try:
        body = dict(artifact.body or {})
        body["final_chapter_status"] = "approved_by_human"
        artifact.body = body
        artifact.content_hash = _hash_payload(body)
    except Exception:
        pass
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
    await _record_event(
        session,
        run_id=run.id,
        event_type="stage_started",
        stage=run.current_stage,
        message="Production run resumed.",
    )
    await _update_run_summary(session, run)
    return run
