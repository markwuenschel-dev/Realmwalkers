"""The locked SceneFidelity policy (ADR 0019) — deterministic, pure, and the sole authority for what a
clause evaluation is allowed to do.

Policy evaluates every clause independently. Only a direct contradiction or corroborated omission against
a HARD clause in an EXPORT_REQUIRED requirement, with VALID evidence, is repair-eligible. Advisory
findings and findings against standard clauses are warnings. Invalid evidence is a report-only
diagnostic. Missing/stale/indeterminate/blocked/failed evaluation of a hard export-required clause is an
operational hold — never a prose failure. LLMs never reach here; only the merged report does.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from dominion.workers.scene_fidelity.contract import finding_signature, prose_hash
from dominion.workers.scene_fidelity.models import (
    ClauseEnforcement,
    ClauseEvaluation,
    ClauseResult,
    PostDraftPolicy,
    SceneFidelityReport,
)
from dominion.workers.scene_fidelity.payloads import (
    CritiqueProjection,
    PolicyOutcome,
    SceneFidelityCritiquePayload,
)

# Results that were not a clean satisfied/lost verdict — operationally uncertain. For a hard
# export-required clause these are incomplete-evaluation holds; elsewhere they are diagnostics.
_OPERATIONAL_STATES: frozenset[ClauseResult] = frozenset(
    {
        ClauseResult.ADAPTER_FAILED,
        ClauseResult.INDETERMINATE,
        ClauseResult.BLOCKED_BY_DEPENDENCY,
        ClauseResult.NOT_EVALUATED,
    }
)


def policy_outcome_for_clause_evaluation(evaluation: ClauseEvaluation) -> PolicyOutcome:
    """The deterministic outcome for one clause evaluation, straight off the locked matrix (ADR 0019)."""
    result = evaluation.result
    hard = evaluation.enforcement == ClauseEnforcement.HARD
    export_required = evaluation.post_draft_policy == PostDraftPolicy.EXPORT_REQUIRED

    # Operationally uncertain results never create prose work. On a hard export-required clause they hold
    # the run as incomplete; anywhere else they are informational only.
    if result in _OPERATIONAL_STATES:
        if hard and export_required:
            return PolicyOutcome(
                kind="operational_hold",
                reason=f"{result.value}: incomplete evaluation of a hard export-required clause",
            )
        return PolicyOutcome(kind="diagnostic", reason=f"{result.value}: no repair work on a non-gating clause")

    # A satisfied/lost verdict with invalid or absent evidence is report-only (ADR 0008/0019).
    if not evaluation.evidence_valid:
        return PolicyOutcome(kind="diagnostic", reason="cited evidence is invalid or missing — report-only")

    # Satisfied with positive valid evidence verifies the clause; it never creates a Critique.
    if result == ClauseResult.SATISFIED:
        return PolicyOutcome(kind="diagnostic", reason="satisfied with positive prose evidence")

    # result == LOST with valid evidence.
    if not export_required:
        return PolicyOutcome(kind="warning", reason="advisory requirement — warning only")
    if not hard:
        return PolicyOutcome(kind="warning", reason="standard clause — warning only; the author may upgrade it")
    return PolicyOutcome(kind="repair_eligible", reason="hard export-required clause lost with valid evidence")


def report_is_current(
    report_body: dict[str, Any],
    *,
    scene_packet_id: uuid.UUID,
    packet_fingerprint: str,
    draft_attempt_id: uuid.UUID,
    prose: str,
) -> tuple[bool, str]:
    """A report is current only when its scene packet, packet fingerprint, source DraftAttempt, and prose
    hash all match the live scene (ADR 0010). Any mismatch is an operational staleness reason — never a
    prose failure. Returns (current, reason)."""
    if report_body.get("scene_packet_id") != str(scene_packet_id):
        return False, "scene_packet_changed"
    if report_body.get("packet_contract_fingerprint") != packet_fingerprint:
        return False, "packet_fingerprint_changed"
    if report_body.get("draft_attempt_id") != str(draft_attempt_id):
        return False, "draft_attempt_changed"
    if report_body.get("prose_hash") != prose_hash(prose):
        return False, "prose_changed"
    return True, "current"


_SEVERITY_FOR_KIND: dict[str, Literal["warn", "repair"]] = {"warning": "warn", "repair_eligible": "repair"}


def project_report_to_critiques(
    report: SceneFidelityReport, *, source_artifact_id: uuid.UUID
) -> list[CritiqueProjection]:
    """Project a report's actionable findings to would-be Critiques. Only ``warning`` and
    ``repair_eligible`` outcomes become Critiques; ``diagnostic`` and ``operational_hold`` do not. Each
    projection is keyed by a stable finding_signature so re-projecting the same report is idempotent
    (ADR 0021). ``source_artifact_id`` is the immutable report Artifact this projects from."""
    projections: list[CritiqueProjection] = []
    for evaluation in report.clause_evaluations:
        outcome = policy_outcome_for_clause_evaluation(evaluation)
        severity = _SEVERITY_FOR_KIND.get(outcome.kind)
        if severity is None:
            continue
        signature = finding_signature(
            requirement_id=evaluation.requirement_id, clause_id=evaluation.clause_id, result=evaluation.result
        )
        payload = SceneFidelityCritiquePayload(
            draft_attempt_id=report.draft_attempt_id,
            source_artifact_id=source_artifact_id,
            finding_signature=signature,
            requirement_id=evaluation.requirement_id,
            clause_id=evaluation.clause_id,
            mode=evaluation.mode,
            result=evaluation.result,
            evidence_anchors=evaluation.evidence_anchors,
            explanation=evaluation.explanation,
            scene_packet_id=report.scene_packet_id,
            prose_hash=report.prose_hash,
            packet_contract_fingerprint=report.packet_contract_fingerprint,
        )
        note = f"{evaluation.mode.value}/{evaluation.clause_id}: {evaluation.explanation or outcome.reason}"
        projections.append(
            CritiqueProjection(
                severity=severity, note=note, payload=payload.model_dump(mode="json"), finding_signature=signature
            )
        )
    return projections
