"""The SceneFidelity evaluator facade (Lane 3B).

One facade runs: deterministic preflight → bounded-concurrent active-mode adapters → a deterministic
merger → exactly one immutable report Artifact tied to the DraftAttempt (ADR 0003/0013). The merger
guarantees COMPLETE coverage: every active clause gets exactly one ClauseEvaluation, and a hard clause is
never dropped — a failed adapter yields ``adapter_failed`` and an unevaluable prerequisite yields
``blocked_by_dependency`` (ADR 0022). LLMs only report evidence; every result state here is assigned by
deterministic code. The adapter runner is injectable so this is fully testable without a live model.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.models import Artifact, DraftAttempt, Scene, ScenePacket
from dominion.workers.budget import TokenBudget
from dominion.workers.scene_fidelity import adapters
from dominion.workers.scene_fidelity.adapters import AdapterOutcome, AdapterRunner
from dominion.workers.scene_fidelity.contract import fidelity_contract_fingerprint
from dominion.workers.scene_fidelity.models import (
    ClauseEnforcement,
    ClauseEvaluation,
    ClauseResult,
    EvidenceAnchor,
    FidelityMode,
    PostDraftPolicy,
    SceneFidelityReport,
    active_requirements,
    is_fidelity_active,
)

REPORT_ARTIFACT_TYPE = "scene_fidelity_report"

# Prerequisite results that leave a dependent unevaluable → the dependent is blocked_by_dependency. A LOST
# or SATISFIED prerequisite does NOT block (ADR 0012: a failed prerequisite is diagnostic context, not an
# automatic failure of the dependent).
_OPERATIONAL_UNCERTAIN: frozenset[ClauseResult] = frozenset(
    {
        ClauseResult.ADAPTER_FAILED,
        ClauseResult.INDETERMINATE,
        ClauseResult.NOT_EVALUATED,
        ClauseResult.BLOCKED_BY_DEPENDENCY,
    }
)

Trigger = Literal["post_draft", "manual", "production"]


def _prose_hash(prose: str) -> str:
    return "sha256:" + hashlib.sha256((prose or "").encode("utf-8")).hexdigest()


async def maybe_evaluate_scene_fidelity(
    session: AsyncSession,
    *,
    scene: Scene,
    draft_attempt: DraftAttempt,
    packet: ScenePacket | None,
    trigger: Trigger,
    adapter_runner: AdapterRunner | None = None,
) -> Artifact | None:
    """Evaluate only when the packet carries an ACTIVE fidelity contract; otherwise a cheap no-op. This is
    the trigger seam the pipeline calls after the final author-visible DraftAttempt is persisted (ADR
    0010/0025) — a legacy/inert packet is never evaluated, never held, never backfilled."""
    if packet is None or not is_fidelity_active(dict(packet.body or {})):
        return None
    return await evaluate_scene_fidelity(
        session, scene=scene, draft_attempt=draft_attempt, packet=packet, trigger=trigger, adapter_runner=adapter_runner
    )


async def evaluate_scene_fidelity(
    session: AsyncSession,
    *,
    scene: Scene,
    draft_attempt: DraftAttempt,
    packet: ScenePacket,
    trigger: Trigger,
    adapter_runner: AdapterRunner | None = None,
) -> Artifact:
    """Run every active mode adapter, merge deterministically, and persist one immutable report Artifact."""
    runner = adapter_runner or adapters.run_mode_adapter
    body = dict(packet.body or {})
    reqs = active_requirements(body)
    prose = draft_attempt.prose or scene.prose or ""
    prose_hash = _prose_hash(prose)
    fingerprint = fidelity_contract_fingerprint(body)
    scene_context = {"scene_no": scene.scene_no, "pov": (body.get("reader_state") or {}).get("pov")}

    # Group active clauses by their owning mode (a clause belongs to exactly one mode, ADR 0011).
    by_mode: dict[FidelityMode, list[dict[str, Any]]] = {}
    for req in reqs:
        try:
            mode = FidelityMode(req.get("mode"))
        except ValueError:
            continue
        for clause in req.get("clauses") or []:
            if isinstance(clause, dict) and isinstance(clause.get("clause_id"), str):
                by_mode.setdefault(mode, []).append(clause)

    sem = asyncio.Semaphore(max(1, settings.scene_fidelity_max_inflight))

    async def _run(mode: FidelityMode, clauses: list[dict[str, Any]]) -> AdapterOutcome:
        async with sem:
            try:
                return await runner(
                    mode,
                    clauses,
                    prose=prose,
                    scene_context=scene_context,
                    budget=TokenBudget(max_tokens=settings.scene_token_budget),
                )
            except Exception as exc:  # a raising adapter is a failed adapter — never a lost coverage
                return AdapterOutcome(
                    mode.value,
                    [],
                    settings.scene_fidelity_model,
                    settings.scene_fidelity_model,
                    False,
                    "failed",
                    f"adapter raised: {exc}",
                )

    outcomes = list(await asyncio.gather(*[_run(m, cs) for m, cs in by_mode.items()]))
    outcomes_by_mode = {o.mode: o for o in outcomes}

    evaluations = _merge(reqs, outcomes_by_mode, prose=prose, prose_hash=prose_hash, fingerprint=fingerprint)
    report = SceneFidelityReport(
        report_schema_version=settings.scene_fidelity_report_schema_version,
        scene_id=scene.id,
        draft_attempt_id=draft_attempt.id,
        scene_packet_id=packet.id,
        prose_hash=prose_hash,
        packet_contract_fingerprint=fingerprint,
        clause_evaluations=evaluations,
        evaluation_telemetry=_telemetry(trigger, outcomes),
    )
    return await _persist_report(session, draft_attempt=draft_attempt, report=report)


def _merge(
    reqs: list[dict[str, Any]],
    outcomes_by_mode: dict[str, AdapterOutcome],
    *,
    prose: str,
    prose_hash: str,
    fingerprint: str,
) -> list[ClauseEvaluation]:
    """Produce exactly one ClauseEvaluation per active clause, in dependency order, with complete coverage."""
    evaluations: list[ClauseEvaluation] = []
    final_by_key: dict[tuple[Any, str], ClauseResult] = {}

    for req in reqs:
        req_id = req.get("requirement_id")
        if not isinstance(req_id, str):
            continue  # an active packet is validated, but stay defensive on the merge path
        try:
            mode = FidelityMode(req.get("mode"))
            policy = PostDraftPolicy(req.get("post_draft_policy"))
        except ValueError:
            continue
        outcome = outcomes_by_mode.get(mode.value)
        mode_failed = outcome is None or outcome.status == "failed"
        raw_by_id = {f.clause_id: f for f in outcome.findings} if (outcome and outcome.status == "ok") else {}

        clauses = [c for c in (req.get("clauses") or []) if isinstance(c, dict) and isinstance(c.get("clause_id"), str)]
        by_id = {c["clause_id"]: c for c in clauses}
        for clause in _dependency_ordered(clauses, by_id):
            cid = clause["clause_id"]
            anchors: list[Any] = []
            if mode_failed:
                result = ClauseResult.ADAPTER_FAILED
                explanation = (outcome.error if outcome else "adapter did not run") or "adapter failed"
            else:
                deps = [d for d in (clause.get("depends_on_clause_ids") or []) if d in by_id]
                if any(final_by_key.get((req_id, d)) in _OPERATIONAL_UNCERTAIN for d in deps):
                    result = ClauseResult.BLOCKED_BY_DEPENDENCY
                    explanation = "a prerequisite clause could not be evaluated"
                elif cid in raw_by_id:
                    finding = raw_by_id[cid]
                    result = ClauseResult(finding.result)
                    anchors = finding.evidence_anchors
                    explanation = finding.explanation
                else:
                    result = ClauseResult.NOT_EVALUATED
                    explanation = "the adapter returned no finding for this clause"
            final_by_key[(req_id, cid)] = result
            try:
                enforcement = ClauseEnforcement(clause.get("enforcement"))
            except ValueError:
                enforcement = ClauseEnforcement.STANDARD
            evaluations.append(
                ClauseEvaluation(
                    requirement_id=req_id,
                    clause_id=cid,
                    mode=mode,
                    result=result,
                    enforcement=enforcement,
                    post_draft_policy=policy,
                    evidence_anchors=anchors,
                    evidence_valid=_evidence_valid(result, anchors, prose),
                    explanation=explanation,
                    evaluated_prose_hash=prose_hash,
                    packet_contract_fingerprint=fingerprint,
                )
            )
    return evaluations


def _anchor_valid(anchor: EvidenceAnchor, prose: str) -> bool:
    """An anchor is semantically valid if its span is in range and — for a contradiction/satisfaction
    anchor — its quote matches the prose exactly. Omission anchors (expected_beat/transition) need only a
    valid span, since absence has no quote of its own (ADR 0008)."""
    if not (0 <= anchor.start <= anchor.end <= len(prose)):
        return False
    if anchor.anchor_kind in ("expected_beat", "transition"):
        return True
    return prose[anchor.start : anchor.end] == anchor.quote


def _evidence_valid(result: ClauseResult, anchors: list[EvidenceAnchor], prose: str) -> bool:
    """A satisfied/lost verdict must cite at least one valid anchor and cite no invalid one; other results
    are not evidence-bearing for policy, so they are trivially 'valid'."""
    if result not in (ClauseResult.SATISFIED, ClauseResult.LOST):
        return True
    return bool(anchors) and all(_anchor_valid(a, prose) for a in anchors)


def _dependency_ordered(clauses: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """DFS post-order: a clause's within-requirement prerequisites are emitted before it (ADR 0012).
    Robust to cycles, which validation rejects at approval."""
    visited: set[str] = set()
    order: list[dict[str, Any]] = []

    def visit(clause: dict[str, Any]) -> None:
        cid = clause["clause_id"]
        if cid in visited:
            return
        visited.add(cid)
        for dep_id in clause.get("depends_on_clause_ids") or []:
            dep = by_id.get(dep_id)
            if dep is not None:
                visit(dep)
        order.append(clause)

    for clause in clauses:
        visit(clause)
    return order


def _telemetry(trigger: str, outcomes: list[AdapterOutcome]) -> dict[str, Any]:
    return {
        "trigger": trigger,
        "requested_model": settings.scene_fidelity_model,
        "prompt_version": settings.scene_fidelity_prompt_version,
        "facade_version": settings.scene_fidelity_facade_version,
        "report_schema_version": settings.scene_fidelity_report_schema_version,
        "adapters": [
            {"mode": o.mode, "status": o.status, "model_used": o.model_used, "escalated": o.escalated, "error": o.error}
            for o in outcomes
        ],
    }


async def _persist_report(
    session: AsyncSession, *, draft_attempt: DraftAttempt, report: SceneFidelityReport
) -> Artifact:
    """One immutable report Artifact per evaluation, tied to the DraftAttempt (ADR 0003). A manual rerun
    mints a new, higher-versioned row rather than mutating the prior report."""
    version = (
        await session.execute(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.artifact_type == REPORT_ARTIFACT_TYPE, Artifact.domain_id == draft_attempt.id)
        )
    ).scalar_one()
    artifact = Artifact(
        production_run_id=None,
        artifact_type=REPORT_ARTIFACT_TYPE,
        domain_table="draft_attempts",
        domain_id=draft_attempt.id,
        version=int(version) + 1,
        status="active",
        body=report.model_dump(mode="json"),
        content_hash=f"{report.prose_hash}:{report.packet_contract_fingerprint}",
    )
    session.add(artifact)
    await session.flush()
    return artifact
