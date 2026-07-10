"""Lane 5 — the locked policy matrix (ADR 0019), pure logic.

Every row is exercised directly on constructed ClauseEvaluations, plus report→critique projection and its
idempotency. No DB, no model.
"""

from __future__ import annotations

import uuid

from dominion.workers.scene_fidelity.models import (
    ClauseEvaluation,
    ClauseResult,
    EvidenceAnchor,
    FidelityMode,
    SceneFidelityReport,
)
from dominion.workers.scene_fidelity.policy import (
    policy_outcome_for_clause_evaluation,
    project_report_to_critiques,
)

_ANCHOR = EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="contradiction")


def _eval(**over) -> ClauseEvaluation:
    base = dict(
        requirement_id="req-1",
        clause_id="cl-1",
        mode=FidelityMode.RELATIONSHIP_TURN,
        result=ClauseResult.LOST,
        enforcement="hard",
        post_draft_policy="export_required",
        evidence_anchors=[_ANCHOR],
        evidence_valid=True,
        explanation="coerced, not chosen",
        evaluated_prose_hash="sha256:p",
        packet_contract_fingerprint="sha256:f",
    )
    base.update(over)
    return ClauseEvaluation.model_validate(base)


def _kind(**over) -> str:
    return policy_outcome_for_clause_evaluation(_eval(**over)).kind


# --- the matrix -----------------------------------------------------------------------------------


def test_hard_export_required_lost_with_valid_evidence_is_repair_eligible() -> None:
    assert _kind() == "repair_eligible"


def test_invalid_evidence_is_report_only_diagnostic() -> None:
    # A hard export-required LOST would be repair-eligible, but invalid evidence downgrades it to a
    # report-only diagnostic (the evaluator sets evidence_valid; policy trusts it).
    assert _kind(evidence_valid=False) == "diagnostic"


def test_standard_clause_lost_is_only_a_warning() -> None:
    assert _kind(enforcement="standard") == "warning"


def test_advisory_requirement_lost_is_only_a_warning() -> None:
    assert _kind(post_draft_policy="advisory") == "warning"
    assert _kind(post_draft_policy="advisory", enforcement="standard") == "warning"


def test_satisfied_with_positive_evidence_creates_no_critique() -> None:
    assert (
        _kind(
            result=ClauseResult.SATISFIED,
            evidence_anchors=[EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="satisfaction")],
        )
        == "diagnostic"
    )


def test_hard_export_required_operational_states_are_holds() -> None:
    for state in (
        ClauseResult.ADAPTER_FAILED,
        ClauseResult.INDETERMINATE,
        ClauseResult.BLOCKED_BY_DEPENDENCY,
        ClauseResult.NOT_EVALUATED,
    ):
        assert _kind(result=state) == "operational_hold", state


def test_operational_states_on_non_gating_clauses_are_diagnostics() -> None:
    assert _kind(result=ClauseResult.ADAPTER_FAILED, enforcement="standard") == "diagnostic"
    assert _kind(result=ClauseResult.INDETERMINATE, post_draft_policy="advisory") == "diagnostic"


# --- report projection ----------------------------------------------------------------------------


def _report(evals) -> SceneFidelityReport:
    return SceneFidelityReport(
        report_schema_version=1,
        scene_id=uuid.uuid4(),
        draft_attempt_id=uuid.uuid4(),
        scene_packet_id=uuid.uuid4(),
        prose_hash="sha256:p",
        packet_contract_fingerprint="sha256:f",
        clause_evaluations=evals,
    )


def test_projection_makes_critiques_only_for_actionable_findings() -> None:
    report = _report(
        [
            _eval(clause_id="cl-repair"),  # hard export_required lost valid -> repair
            _eval(clause_id="cl-warn", enforcement="standard"),  # standard lost -> warn
            _eval(
                clause_id="cl-quiet",
                result=ClauseResult.SATISFIED,
                evidence_anchors=[EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="satisfaction")],
            ),  # satisfied -> none
            _eval(
                clause_id="cl-hold", result=ClauseResult.ADAPTER_FAILED
            ),  # hard export_required failed -> hold, no critique
        ]
    )
    art_id = uuid.uuid4()
    projections = project_report_to_critiques(report, source_artifact_id=art_id)
    by_clause = {p.payload["clause_id"]: p for p in projections}
    assert set(by_clause) == {"cl-repair", "cl-warn"}
    assert by_clause["cl-repair"].severity == "repair"
    assert by_clause["cl-warn"].severity == "warn"
    # payload provenance IDs match the Critique columns they will be written to (ADR 0021).
    assert by_clause["cl-repair"].payload["source_artifact_id"] == str(art_id)
    assert by_clause["cl-repair"].payload["finding_signature"] == by_clause["cl-repair"].finding_signature


def test_projection_is_idempotent_by_finding_signature() -> None:
    report = _report([_eval()])
    art_id = uuid.uuid4()
    first = project_report_to_critiques(report, source_artifact_id=art_id)
    second = project_report_to_critiques(report, source_artifact_id=art_id)
    assert [p.finding_signature for p in first] == [p.finding_signature for p in second]
