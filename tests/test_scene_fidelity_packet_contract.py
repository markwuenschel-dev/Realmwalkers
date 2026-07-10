"""Lane 2 — versioned fidelity contracts at the packet gate + author-action normalization.

Pure logic (no DB): evaluate_scene_packet() must block approval on a malformed ACTIVE contract, stay
silent on suggestions and legacy packets, expose the active contract + fingerprint through project(), and
mint/preserve identity correctly on accept / refine / replace (ADR 0006/0024). Malformed/cycle cases are
driven off the shared fixture corpus.
"""

from __future__ import annotations

from test_scene_fidelity_fixtures import load_fixture

from dominion.workers.scene_fidelity import (
    fidelity_contract_fingerprint,
    is_fidelity_active,
    validate_active_requirements,
)
from dominion.workers.scene_packet.fidelity import (
    accept_suggestions,
    mint_identity,
    refine_requirement,
    replace_requirement,
)
from dominion.workers.scene_packet.projections import project
from dominion.workers.scene_packet.validation import evaluate_scene_packet


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


def _evaluate(packet_body):
    return evaluate_scene_packet(
        body=packet_body,
        chapter_packet_body={},
        scene_seed={},
        word_budget={"target_words": 1000},
        scene_no=1,
        sources=[],
    )


def _blocker_kinds(result) -> set[str]:
    return {v.kind for v in result.draft_blockers}


# --- packet gate ----------------------------------------------------------------------------------


def test_malformed_active_contract_blocks_packet_approval() -> None:
    result = _evaluate(load_fixture("malformed_active_requirement_blocks_approval")["packet"])
    assert not result.draftable
    assert "fidelity_hard_clause_missing_criterion" in _blocker_kinds(result)


def test_dependency_cycle_blocks_packet_approval() -> None:
    result = _evaluate(load_fixture("dependency_cycle_rejected")["packet"])
    assert not result.draftable
    assert "fidelity_clause_dependency_cycle" in _blocker_kinds(result)


def test_version_required_surfaces_through_packet_gate() -> None:
    result = _evaluate({"fidelity_requirements": [_req()]})  # active reqs, no version marker
    assert not result.draftable
    assert "fidelity_contract_version_required" in _blocker_kinds(result)


def test_valid_active_contract_is_draftable() -> None:
    result = _evaluate(load_fixture("serra_agency_loss")["packet"])
    assert result.draftable
    assert not any(v.kind.startswith("fidelity_") for v in result.violations)


def test_legacy_packet_gate_is_unchanged() -> None:
    result = _evaluate(load_fixture("legacy_packet_is_inert")["packet"])
    assert result.draftable
    assert not any(v.kind.startswith("fidelity_") for v in result.violations)


# --- suggestions never activate (ADR 0005) --------------------------------------------------------


def test_suggestions_never_block_or_activate() -> None:
    # A structurally BROKEN requirement is fine as a suggestion — suggestions are never validated as active.
    body = {"suggested_fidelity_requirements": [_req(clauses=[_clause(satisfaction_criterion=None)])]}
    result = _evaluate(body)
    assert result.draftable
    assert not any(v.kind.startswith("fidelity_") for v in result.violations)
    assert not is_fidelity_active(body)
    assert project(body, {}).fidelity_requirements == []


# --- projection exposes the active contract + fingerprint -----------------------------------------


def test_projection_exposes_active_requirements_and_fingerprint() -> None:
    packet = load_fixture("serra_agency_loss")["packet"]
    proj = project(packet, {})
    assert len(proj.fidelity_requirements) == 1
    assert proj.fidelity_fingerprint == fidelity_contract_fingerprint(packet)
    assert proj.fidelity_fingerprint != fidelity_contract_fingerprint({})


# --- accept (ADR 0005/0006) -----------------------------------------------------------------------


def test_accept_suggestion_mints_identity_and_activates() -> None:
    body = {"suggested_fidelity_requirements": [_req()]}
    new_body, violations = accept_suggestions(body)
    assert violations == []
    assert new_body["fidelity_contract_version"] == 1
    active = new_body["fidelity_requirements"]
    assert len(active) == 1
    assert active[0]["requirement_id"] != "req-1"  # server-minted fresh identity
    assert active[0]["clauses"][0]["clause_id"] != "cl-1"
    assert new_body["suggested_fidelity_requirements"] == []
    assert validate_active_requirements(new_body) == []


def test_accept_remaps_dependencies_to_new_clause_ids() -> None:
    req = _req(
        clauses=[
            _clause(clause_id="cl-a"),
            _clause(clause_id="cl-b", depends_on_clause_ids=["cl-a"]),
        ]
    )
    new_body, violations = accept_suggestions({"suggested_fidelity_requirements": [req]})
    assert violations == []
    active = new_body["fidelity_requirements"][0]
    new_ids = [c["clause_id"] for c in active["clauses"]]
    assert active["clauses"][1]["depends_on_clause_ids"] == [new_ids[0]]
    assert validate_active_requirements(new_body) == []


def test_accept_rejects_a_structurally_invalid_suggestion() -> None:
    bad = _req(clauses=[_clause(satisfaction_criterion=None)])  # hard clause with no criterion
    new_body, violations = accept_suggestions({"suggested_fidelity_requirements": [bad]})
    assert "fidelity_hard_clause_missing_criterion" in {v.kind for v in violations}
    assert new_body == {"suggested_fidelity_requirements": [bad]}  # unchanged


# --- refine vs replace identity (ADR 0024) --------------------------------------------------------


def _active_body():
    return {"fidelity_contract_version": 1, "fidelity_requirements": [_req()]}


def test_refine_preserves_identity_on_clarification() -> None:
    updated = _req(clauses=[_clause(statement="Serra chooses, with an exit still open to her.")])
    new_body, violations = refine_requirement(_active_body(), "req-1", updated)
    assert violations == []
    refined = new_body["fidelity_requirements"][0]
    assert refined["requirement_id"] == "req-1"
    assert refined["clauses"][0]["clause_id"] == "cl-1"
    assert "exit still open" in refined["clauses"][0]["statement"]


def test_refine_rejects_mode_change() -> None:
    _, violations = refine_requirement(_active_body(), "req-1", _req(mode="combat_blocking"))
    assert "fidelity_refine_requires_replace" in {v.kind for v in violations}


def test_refine_rejects_enforcement_and_clauseset_changes() -> None:
    _, v1 = refine_requirement(_active_body(), "req-1", _req(clauses=[_clause(enforcement="standard")]))
    assert "fidelity_refine_requires_replace" in {v.kind for v in v1}
    _, v2 = refine_requirement(_active_body(), "req-1", _req(clauses=[_clause(), _clause(clause_id="cl-2")]))
    assert "fidelity_refine_requires_replace" in {v.kind for v in v2}


# --- replace (ADR 0024) ---------------------------------------------------------------------------


def test_replace_mints_new_identity() -> None:
    replacement = _req(clauses=[_clause(statement="A different, stronger obligation.")])
    new_body, violations = replace_requirement(_active_body(), "req-1", replacement)
    assert violations == []
    assert new_body["fidelity_requirements"][0]["requirement_id"] != "req-1"


def test_replace_rejects_dangling_dependency() -> None:
    replacement = _req(clauses=[_clause(clause_id="cl-a", depends_on_clause_ids=["cl-missing"])])
    _, violations = replace_requirement(_active_body(), "req-1", replacement)
    assert "fidelity_clause_dependency_missing_target" in {v.kind for v in violations}


def test_mint_identity_is_idempotently_fresh() -> None:
    a = mint_identity(_req())
    b = mint_identity(_req())
    assert a["requirement_id"] != b["requirement_id"]
    assert a["clauses"][0]["clause_id"] != b["clauses"][0]["clause_id"]
