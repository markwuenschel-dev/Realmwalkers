"""Lane 8B — end-to-end integration + corpus↔policy consistency (DB-backed).

Threads the whole vertical (approved contract → evaluate → policy → production triage → author repair /
override) and pins the flagship fixture scenarios, plus a check that the fixture corpus's declared policy
expectations match the locked policy exactly. Evaluation uses the real evaluator with a fake adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime

from test_scene_fidelity_fixtures import iter_fixtures
from test_scene_fidelity_production import _LOST, _SATISFIED, _issues, _setup

from dominion.shared.enums import IssueStatus
from dominion.shared.models import DraftAttempt
from dominion.workers import production_repair
from dominion.workers.scene_fidelity.evaluator import evaluate_scene_fidelity
from dominion.workers.scene_fidelity.models import (
    ClauseEnforcement,
    ClauseEvaluation,
    ClauseResult,
    EvidenceAnchor,
    FidelityMode,
    PostDraftPolicy,
)
from dominion.workers.scene_fidelity.policy import policy_outcome_for_clause_evaluation


async def test_agency_loss_flows_to_a_repaired_revision(db_factory) -> None:
    """serra_agency_loss: lost hard clause → human-required Issue → author preview → new revision."""
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)  # a current LOST report
        result = await production_repair.triage_scene_fidelity_for_production(s, run=run)
        assert len(result.created_issue_ids) == 1
        issue = (await _issues(s, run))[0]
        preview = await production_repair.create_repair_preview(
            s, issue=issue, candidate_prose="Marcus stepped aside; she chose to stay.", rationale="restore agency"
        )
        new_scene = await production_repair.accept_repair_preview(s, preview_artifact_id=preview.id)
    assert new_scene.version == scene.version + 1
    assert new_scene.prose == "Marcus stepped aside; she chose to stay."


async def test_true_negative_never_flags(db_factory) -> None:
    """mutual_escalation_preserved / combat_pillar_reversal: satisfied → no Issue, no hold."""
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s, results={"cl-1": _SATISFIED})
        result = await production_repair.triage_scene_fidelity_for_production(s, run=run)
    assert result.created_issue_ids == []
    assert result.operational_holds == []


async def test_override_then_fresh_loss_materializes_a_successor_issue(db_factory) -> None:
    """An override does not inherit (ADR 0009): a re-drafted scene that loses the clause again gets a NEW
    Issue while the overridden one keeps its truthful history."""
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        await production_repair.triage_scene_fidelity_for_production(s, run=run)
        issue1 = (await _issues(s, run))[0]
        await production_repair.override_fidelity_issue(s, issue=issue1, reason="intentional beat")
        assert issue1.status == IssueStatus.OVERRIDDEN.value

        # A new, later draft loses the same clause again.
        da2 = DraftAttempt(
            scene_id=scene.id,
            scene_packet_id=sp.id,
            stage="final_rendered",
            prose="Marcus sealed the door and she stayed.",
            created_at=datetime(2999, 1, 1, tzinfo=UTC),
        )
        s.add(da2)
        await s.flush()
        await evaluate_scene_fidelity(
            s, scene=scene, draft_attempt=da2, packet=sp, trigger="manual", adapter_runner=_runner_lost()
        )
        result = await production_repair.triage_scene_fidelity_for_production(s, run=run)
        assert len(result.created_issue_ids) == 1  # a fresh successor Issue
        statuses = {i.status for i in await _issues(s, run)}
    assert IssueStatus.OVERRIDDEN.value in statuses  # the overridden one keeps its history
    assert IssueStatus.REPAIR_QUEUED.value in statuses  # the successor is live


def _runner_lost():
    from test_scene_fidelity_production import _runner

    return _runner({"cl-1": _LOST})


# --- corpus ↔ policy consistency ------------------------------------------------------------------


def _evaluation_from(
    mode: str, enforcement: str, policy: str, result: str, *, evidence_valid: bool
) -> ClauseEvaluation:
    anchors = [EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="contradiction")] if result == "lost" else []
    return ClauseEvaluation(
        requirement_id="req-1",
        clause_id="cl-1",
        mode=FidelityMode(mode),
        result=ClauseResult(result),
        enforcement=ClauseEnforcement(enforcement),
        post_draft_policy=PostDraftPolicy(policy),
        evidence_anchors=anchors,
        evidence_valid=evidence_valid,
        explanation="x",
        evaluated_prose_hash="sha256:p",
        packet_contract_fingerprint="sha256:f",
    )


def test_fixture_policy_expectations_match_locked_policy() -> None:
    """Every fixture that declares an expected clause result + policy_outcome must agree with the locked
    policy — the fixtures and the code can never silently diverge."""
    checked = 0
    for fixture in iter_fixtures():
        expect = fixture["expect"]
        ce = expect.get("clause_evaluation")
        po = expect.get("policy_outcome")
        if not ce or not po or "result" not in ce:
            continue
        req = fixture["packet"]["fidelity_requirements"][0]
        clause = req["clauses"][0]
        # Invalid-anchor fixtures declare an invalid anchor => evidence is not valid.
        evidence_valid = "invalid_anchor" not in ce
        evaluation = _evaluation_from(
            req["mode"], clause["enforcement"], req["post_draft_policy"], ce["result"], evidence_valid=evidence_valid
        )
        outcome = policy_outcome_for_clause_evaluation(evaluation)
        assert outcome.kind == po["kind"], f"{fixture['id']}: policy {outcome.kind} != expected {po['kind']}"
        checked += 1
    assert checked >= 4  # serra, invalid_anchor, mutual_escalation, combat_pillar, reader_movie
