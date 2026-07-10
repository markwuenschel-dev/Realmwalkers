"""Lane 3A — projecting the active fidelity contract into drafter prompt sections.

Pure logic (no DB): prerequisites render before dependents, criterion kind routes each clause to the
right section, suggestions/legacy project nothing, and the drafter block carries statements only (never
clause IDs). Combat/serra cases are driven off the shared fixture corpus.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_scene_fidelity_fixtures import load_fixture

from dominion.workers.scene_fidelity import project_fidelity_for_drafter
from dominion.workers.scene_packet.projections import project
from dominion.workers.specialists.drafter import _fidelity_block


def _clause(**over):
    base = {
        "clause_id": "cl-1",
        "enforcement": "hard",
        "statement": "A thing is preserved.",
        "satisfaction_criterion": {"evidence_kind": "state_change", "statement": "shown."},
        "depends_on_clause_ids": [],
    }
    base.update(over)
    return base


def _body(clauses, *, mode="relationship_turn"):
    return {
        "fidelity_contract_version": 1,
        "fidelity_requirements": [
            {"requirement_id": "req-1", "mode": mode, "post_draft_policy": "export_required", "clauses": clauses}
        ],
    }


# --- projection -----------------------------------------------------------------------------------


def test_prerequisite_is_ordered_before_its_dependent() -> None:
    # Dependent listed FIRST in the raw body; projection must still emit the prerequisite first.
    body = _body(
        [
            _clause(clause_id="cl-payoff", statement="the payoff", depends_on_clause_ids=["cl-setup"]),
            _clause(clause_id="cl-setup", statement="the setup"),
        ]
    )
    proj = project_fidelity_for_drafter(body)
    mp = proj["must_preserve"]
    assert mp.index("the setup") < mp.index("the payoff")
    assert {"establish": "the setup", "before": "the payoff"} in proj["establish_before_payoff"]


def test_criterion_kind_routes_to_the_right_section() -> None:
    body = _body(
        [
            _clause(
                clause_id="cl-p",
                statement="preserve me",
                satisfaction_criterion={"evidence_kind": "action", "statement": "x"},
            ),
            _clause(
                clause_id="cl-n",
                statement="restrain this",
                satisfaction_criterion={"evidence_kind": "absence_or_restraint", "statement": "x"},
            ),
            _clause(
                clause_id="cl-s",
                statement="stay reachable",
                satisfaction_criterion={"evidence_kind": "spatial_relation", "statement": "x"},
            ),
        ]
    )
    proj = project_fidelity_for_drafter(body)
    assert proj["must_preserve"] == ["preserve me"]
    assert proj["must_not"] == ["restrain this"]
    assert proj["scene_state"] == ["stay reachable"]


def test_suggestions_and_legacy_project_nothing() -> None:
    assert (
        project_fidelity_for_drafter(
            {"suggested_fidelity_requirements": [_body([_clause()])["fidelity_requirements"][0]]}
        )
        == {}
    )
    assert project_fidelity_for_drafter(load_fixture("legacy_packet_is_inert")["packet"]) == {}


def test_projection_is_wired_into_scene_projection() -> None:
    packet = load_fixture("combat_pillar_reversal")["packet"]
    p = project(packet, {})
    assert p.fidelity_drafter == project_fidelity_for_drafter(packet)
    assert project(load_fixture("legacy_packet_is_inert")["packet"], {}).fidelity_drafter == {}


# --- drafter rendering ----------------------------------------------------------------------------


def test_fidelity_block_renders_statements_without_ids() -> None:
    packet = load_fixture("combat_pillar_reversal")["packet"]
    block = _fidelity_block(SimpleNamespace(fidelity=project_fidelity_for_drafter(packet)))
    assert block is not None
    assert "FIDELITY" in block
    assert "ESTABLISH BEFORE PAYOFF" in block  # combat_pillar_reversal has a clause dependency
    # No identity leaks into author-facing prompt text (ADR 0016).
    assert "cl-" not in block
    assert "req-" not in block


def test_fidelity_block_orders_prerequisite_before_payoff_in_text() -> None:
    body = _body(
        [
            _clause(clause_id="cl-payoff", statement="the payoff line", depends_on_clause_ids=["cl-setup"]),
            _clause(clause_id="cl-setup", statement="the setup line"),
        ]
    )
    block = _fidelity_block(SimpleNamespace(fidelity=project_fidelity_for_drafter(body)))
    assert block is not None
    assert block.index("the setup line") < block.index("the payoff line")


def test_fidelity_block_is_none_when_inert() -> None:
    assert _fidelity_block(SimpleNamespace(fidelity=None)) is None
    assert _fidelity_block(SimpleNamespace(fidelity={})) is None
