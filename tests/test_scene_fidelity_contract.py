"""Lane 1 contract tests — pure logic, no DB.

Proves deterministic validation of active fidelity requirements (unknown modes, duplicate IDs, missing
hard criteria, dependency defects), forward-only inertness of legacy packets, order-independent
fingerprinting, and the strict Critique payload schema. Several cases are driven straight off the shared
fixture corpus (Lane 8A) so the contract layer and the corpus can never silently diverge.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from test_scene_fidelity_fixtures import load_fixture

from dominion.workers.scene_fidelity import (
    CLOSED_MODES,
    ClauseResult,
    FidelityMode,
    FidelityRequirement,
    SceneFidelityCritiquePayload,
    active_requirements,
    fidelity_contract_fingerprint,
    finding_signature,
    is_fidelity_active,
    validate_active_requirements,
)


def _clause(**over):
    base = {
        "clause_id": "cl-1",
        "enforcement": "hard",
        "statement": "Serra's agency is preserved.",
        "satisfaction_criterion": {
            "evidence_kind": "state_change",
            "statement": "A self-originated decision is shown.",
        },
        "depends_on_clause_ids": [],
    }
    base.update(over)
    return base


def _req(**over):
    base = {
        "requirement_id": "req-1",
        "mode": "relationship_turn",
        "post_draft_policy": "export_required",
        "clauses": [_clause()],
    }
    base.update(over)
    return base


def _body(reqs, *, version: int | None = 1):
    body: dict = {"fidelity_requirements": reqs}
    if version is not None:
        body["fidelity_contract_version"] = version
    return body


def _kinds(body) -> set[str]:
    return {v.kind for v in validate_active_requirements(body)}


# --- closed registry ------------------------------------------------------------------------------


def test_closed_mode_registry_is_exactly_five() -> None:
    assert CLOSED_MODES == {
        "relationship_turn",
        "intimacy_blocking",
        "combat_blocking",
        "spatial_affordance",
        "reader_movie",
    }


def test_valid_active_contract_has_no_violations() -> None:
    assert validate_active_requirements(_body([_req()])) == []
    assert is_fidelity_active(_body([_req()]))


# --- forward-only inertness (ADR 0025) ------------------------------------------------------------


def test_legacy_packet_with_no_fidelity_fields_is_inert() -> None:
    body = {"reader_state": {"pov": "Serra"}, "required_beats": ["Serra reaches the tower."]}
    assert validate_active_requirements(body) == []
    assert not is_fidelity_active(body)
    assert active_requirements(body) == []


def test_legacy_fixture_is_inert() -> None:
    fx = load_fixture("legacy_packet_is_inert")
    assert not is_fidelity_active(fx["packet"])
    assert validate_active_requirements(fx["packet"]) == []


def test_empty_requirements_list_is_inert() -> None:
    assert validate_active_requirements({"fidelity_requirements": []}) == []
    assert not is_fidelity_active({"fidelity_contract_version": 1, "fidelity_requirements": []})


# --- version gate (ADR 0025) ----------------------------------------------------------------------


def test_version_required_only_when_active_requirements_exist() -> None:
    assert "fidelity_contract_version_required" in _kinds(_body([_req()], version=None))
    assert "fidelity_contract_version_required" in _kinds(_body([_req()], version=2))
    # ...but a body with no active requirements never demands the version marker.
    assert "fidelity_contract_version_required" not in _kinds({"fidelity_requirements": []})


# --- structural violations ------------------------------------------------------------------------


def test_unknown_mode_blocks() -> None:
    assert "fidelity_unknown_mode" in _kinds(_body([_req(mode="banter")]))


def test_duplicate_requirement_id_blocks() -> None:
    assert "fidelity_duplicate_requirement_id" in _kinds(_body([_req(), _req(clauses=[_clause(clause_id="cl-2")])]))


def test_duplicate_clause_id_blocks() -> None:
    dup = _req(clauses=[_clause(clause_id="cl-x"), _clause(clause_id="cl-x")])
    assert "fidelity_duplicate_clause_id" in _kinds(_body([dup]))


def test_invalid_post_draft_policy_blocks() -> None:
    assert "fidelity_invalid_post_draft_policy" in _kinds(_body([_req(post_draft_policy="maybe")]))


def test_empty_clauses_blocks() -> None:
    assert "fidelity_requirement_has_no_clauses" in _kinds(_body([_req(clauses=[])]))


# --- hard-clause criteria (ADR 0023) --------------------------------------------------------------


def test_hard_clause_missing_criterion_blocks() -> None:
    hard_no_crit = _req(clauses=[_clause(satisfaction_criterion=None)])
    assert "fidelity_hard_clause_missing_criterion" in _kinds(_body([hard_no_crit]))


def test_standard_clause_may_omit_criterion() -> None:
    standard = _req(clauses=[_clause(enforcement="standard", satisfaction_criterion=None)])
    assert validate_active_requirements(_body([standard])) == []


def test_criterion_unknown_evidence_kind_blocks() -> None:
    bad = _req(clauses=[_clause(satisfaction_criterion={"evidence_kind": "vibes", "statement": "x"})])
    assert "fidelity_criterion_unknown_evidence_kind" in _kinds(_body([bad]))


def test_criterion_empty_statement_blocks() -> None:
    bad = _req(clauses=[_clause(satisfaction_criterion={"evidence_kind": "action", "statement": "   "})])
    assert "fidelity_criterion_empty_statement" in _kinds(_body([bad]))


def test_malformed_fixture_blocks_approval() -> None:
    fx = load_fixture("malformed_active_requirement_blocks_approval")
    assert "fidelity_hard_clause_missing_criterion" in _kinds(fx["packet"])


# --- dependencies (ADR 0012) ----------------------------------------------------------------------


def test_missing_dependency_target_blocks() -> None:
    req = _req(clauses=[_clause(clause_id="cl-a", depends_on_clause_ids=["cl-missing"])])
    assert "fidelity_clause_dependency_missing_target" in _kinds(_body([req]))


def test_self_dependency_blocks() -> None:
    req = _req(clauses=[_clause(clause_id="cl-a", depends_on_clause_ids=["cl-a"])])
    assert "fidelity_clause_self_dependency" in _kinds(_body([req]))


def test_dependency_cycle_fixture_blocks() -> None:
    fx = load_fixture("dependency_cycle_rejected")
    assert "fidelity_clause_dependency_cycle" in _kinds(fx["packet"])


def test_acyclic_dependencies_are_allowed() -> None:
    req = _req(
        clauses=[
            _clause(clause_id="cl-a"),
            _clause(clause_id="cl-b", depends_on_clause_ids=["cl-a"]),
        ]
    )
    assert validate_active_requirements(_body([req])) == []


# --- unconditional block severity -----------------------------------------------------------------


def test_every_active_violation_is_block_severity() -> None:
    body = _body([_req(mode="banter", post_draft_policy="maybe", clauses=[_clause(satisfaction_criterion=None)])])
    violations = validate_active_requirements(body)
    assert violations, "expected violations for a thoroughly malformed requirement"
    assert all(v.severity == "block" for v in violations)


def test_validation_never_raises_on_garbage() -> None:
    assert validate_active_requirements({"fidelity_requirements": "not-a-list"})
    assert validate_active_requirements({"fidelity_contract_version": 1, "fidelity_requirements": [None, 3, "x"]})


# --- fingerprint (ADR 0006/0010/0024) -------------------------------------------------------------


def test_fingerprint_is_order_independent() -> None:
    a = _clause(clause_id="cl-a")
    b = _clause(clause_id="cl-b", depends_on_clause_ids=["cl-a"])
    body1 = _body([_req(clauses=[a, b])])
    body2 = _body([_req(clauses=[b, a])])  # pure reorder
    assert fidelity_contract_fingerprint(body1) == fidelity_contract_fingerprint(body2)


def test_fingerprint_changes_on_semantic_edit() -> None:
    base = _body([_req()])
    edited = _body([_req(clauses=[_clause(statement="A different obligation.")])])
    assert fidelity_contract_fingerprint(base) != fidelity_contract_fingerprint(edited)


def test_fingerprint_of_inert_bodies_is_stable_and_equal() -> None:
    assert fidelity_contract_fingerprint({}) == fidelity_contract_fingerprint({"reader_state": {}})
    assert fidelity_contract_fingerprint({}).startswith("sha256:")


# --- finding signature ----------------------------------------------------------------------------


def test_finding_signature_is_stable_and_keyed() -> None:
    sig = finding_signature(requirement_id="req-1", clause_id="cl-1", result=ClauseResult.LOST)
    assert sig == finding_signature(requirement_id="req-1", clause_id="cl-1", result="lost")
    assert sig != finding_signature(requirement_id="req-1", clause_id="cl-1", result=ClauseResult.SATISFIED)
    assert sig != finding_signature(requirement_id="req-2", clause_id="cl-1", result=ClauseResult.LOST)


# --- pydantic contract models ---------------------------------------------------------------------


def test_requirement_model_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        FidelityRequirement.model_validate(_req(mode="banter"))
    assert FidelityRequirement.model_validate(_req()).mode is FidelityMode.RELATIONSHIP_TURN


def test_critique_payload_is_strict_and_carries_direct_ids() -> None:
    payload = SceneFidelityCritiquePayload(
        draft_attempt_id=uuid.uuid4(),
        source_artifact_id=uuid.uuid4(),
        finding_signature="sha256:abc",
        requirement_id="req-1",
        clause_id="cl-1",
        mode=FidelityMode.RELATIONSHIP_TURN,
        result=ClauseResult.LOST,
        evidence_anchors=[],
        explanation="coerced, not chosen",
        scene_packet_id=uuid.uuid4(),
        prose_hash="sha256:p",
        packet_contract_fingerprint="sha256:f",
    )
    assert payload.payload_schema_version == 1
    with pytest.raises(ValidationError):
        SceneFidelityCritiquePayload.model_validate({**payload.model_dump(), "surprise": "x"})
