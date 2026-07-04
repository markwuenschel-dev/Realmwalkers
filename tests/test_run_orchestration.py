"""Run-orchestration stage machine (lane 6) — pure, deterministic, no DB / network / LLM.

Covers the extracted stage-transition decisions in dominion.workers.run_stages:
- assembly refusal when expected scenes lack prose -> waiting_for_scene_drafts;
- structural blocking issues after chapter QA -> structural_repair_required;
- provider rate-limit classification -> provider_rate_limited (never a contract failure);
- drafting preconditions (approved non-stale packets, budget arithmetic) fail BEFORE LLM spend,
  reproduced against the preserved ch1 bad-run fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from dominion.workers import run_stages
from dominion.workers.run_stages import (
    STAGE_ASSEMBLING_CHAPTER,
    STAGE_CHAPTER_QA,
    STAGE_DRAFTING_SCENES,
    STAGE_PROVIDER_RATE_LIMITED,
    STAGE_STRUCTURAL_REPAIR_REQUIRED,
    STAGE_WAITING_FOR_SCENE_DRAFTS,
    STRUCTURAL_BLOCKING_ISSUE_KINDS,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ch1_bad_run"


def _sequence_body(scene_nos: list[int], hard_max_words: int | None = None) -> dict:
    body: dict = {"scenes": [{"scene_no": no, "scene_function": f"fn{no}"} for no in scene_nos]}
    if hard_max_words is not None:
        body["hard_max_words"] = hard_max_words
    return body


# --- Assembly gate ----------------------------------------------------------------------------------


def test_assembly_refuses_when_expected_scenes_lack_prose():
    decision = run_stages.evaluate_assembly_readiness(_sequence_body([1, 2, 3, 4]), scenes_with_prose={1, 2})
    assert decision.ok is False
    assert decision.next_stage == STAGE_WAITING_FOR_SCENE_DRAFTS
    assert decision.reason == "missing_scene_prose"
    assert decision.violations[0]["missing_scene_nos"] == [3, 4]
    assert decision.violations[0]["expected_scene_count"] == 4


def test_assembly_refuses_when_nothing_has_prose():
    decision = run_stages.evaluate_assembly_readiness(_sequence_body([1, 2]), scenes_with_prose=set())
    assert decision.ok is False
    assert decision.next_stage == STAGE_WAITING_FOR_SCENE_DRAFTS


def test_assembly_refuses_structurally_when_sequence_is_blocked():
    decision = run_stages.evaluate_assembly_readiness(_sequence_body([1]), scenes_with_prose={1}, sequence_blocked=True)
    assert decision.ok is False
    assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED
    assert decision.reason == "sequence_blocked"


def test_assembly_proceeds_when_every_sequence_scene_has_prose():
    decision = run_stages.evaluate_assembly_readiness(_sequence_body([1, 2, 3]), scenes_with_prose={1, 2, 3})
    assert decision.ok is True
    assert decision.next_stage == STAGE_ASSEMBLING_CHAPTER


def test_assembly_without_sequence_falls_back_to_present_prose():
    # Legacy path: no sequence -> whatever prose exists may assemble; nothing at all may not.
    assert run_stages.evaluate_assembly_readiness(None, scenes_with_prose={1}).ok is True
    assert run_stages.evaluate_assembly_readiness(None, scenes_with_prose=set()).ok is False


# --- Chapter-QA routing -----------------------------------------------------------------------------


def test_structural_issue_after_qa_routes_to_structural_repair_required():
    for kind in sorted(STRUCTURAL_BLOCKING_ISSUE_KINDS):
        decision = run_stages.classify_qa_outcome(["pacing_flat", kind, "length"])
        assert decision.ok is False, kind
        assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED
        assert {"kind": kind} in decision.violations


def test_multiple_structural_kinds_collapse_into_one_decision():
    decision = run_stages.classify_qa_outcome(
        ["scene_scope_bleed", "duplicate_irreversible_beat", "scene_scope_bleed", "pacing_flat"]
    )
    assert decision.ok is False
    assert [v["kind"] for v in decision.violations] == ["duplicate_irreversible_beat", "scene_scope_bleed"]


def test_non_structural_issues_stay_in_chapter_qa_flow():
    decision = run_stages.classify_qa_outcome(["pacing_flat", "word_budget_exceeded", "missing_scene"])
    assert decision.ok is True
    assert decision.next_stage == STAGE_CHAPTER_QA


# --- Provider rate-limit classification ---------------------------------------------------------------


def test_rate_limit_failures_classify_as_provider_rate_limited():
    persisted = "LlmRateLimited: provider rate limit (429) persisted after 6 automatic retries @ llm.py:328"
    assert run_stages.is_provider_rate_limited(persisted) is True
    assert run_stages.stage_after_draft_failure(persisted) == STAGE_PROVIDER_RATE_LIMITED
    assert run_stages.stage_after_draft_failure(RuntimeError("HTTP 429 Too Many Requests")) == (
        STAGE_PROVIDER_RATE_LIMITED
    )


def test_non_rate_limit_failures_never_route_to_a_contract_failure_stage_from_here():
    for error in ("ValueError: scene packet contract invalid", "AuthenticationError: bad key", None):
        assert run_stages.stage_after_draft_failure(error) is None
    assert run_stages.is_provider_rate_limited("TimeoutError: request took 90s") is False


# --- Drafting preconditions (before any LLM call) ----------------------------------------------------


def _packets(nos: list[int], status: str = "approved", hard_max: int | None = None) -> dict:
    return {
        no: {"status": status, "word_budget": {"hard_max": hard_max} if hard_max is not None else None} for no in nos
    }


def test_drafting_refused_without_a_derived_sequence():
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="approved", sequence_qa_verdict="approve", sequence_body=None, scene_packets={}
    )
    assert decision.ok is False
    assert decision.reason == "sequence_missing"
    assert decision.next_stage is None  # human gate: the run stays put


def test_drafting_refused_when_sequence_qa_blocks():
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="blocked",
        sequence_qa_verdict="block_drafting",
        sequence_body=_sequence_body([1, 2]),
        scene_packets=_packets([1, 2]),
    )
    assert decision.ok is False
    assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED


def test_drafting_refused_on_stale_or_unapproved_scene_packets():
    packets = _packets([1, 2, 3])
    packets[2]["status"] = "stale"
    del packets[3]
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="approved",
        sequence_qa_verdict="approve",
        sequence_body=_sequence_body([1, 2, 3]),
        scene_packets=packets,
    )
    assert decision.ok is False
    assert decision.reason == "scene_packets_not_ready"
    kinds = {(v["kind"], v.get("scene_no")) for v in decision.violations}
    assert kinds == {("scene_packet_stale", 2), ("scene_packet_missing", 3)}


def test_drafting_proceeds_when_sequence_and_packets_are_ready():
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="approved",
        sequence_qa_verdict="approve",
        sequence_body=_sequence_body([1, 2], hard_max_words=5000),
        scene_packets=_packets([1, 2], hard_max=2500),
    )
    assert decision.ok is True
    assert decision.next_stage == STAGE_DRAFTING_SCENES


def test_contradictory_budgets_block_drafting_as_sequence_budget_mismatch():
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="approved",
        sequence_qa_verdict="approve",
        sequence_body=_sequence_body([1, 2], hard_max_words=4000),
        scene_packets=_packets([1, 2], hard_max=2500),  # 5000 > 4000
    )
    assert decision.ok is False
    assert decision.reason == "sequence_budget_mismatch"
    assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED
    violation = decision.violations[0]
    assert violation["planned_hard_max_words"] == 5000
    assert violation["chapter_hard_max_words"] == 4000


# --- Regression against the preserved ch1 bad run -----------------------------------------------------


def test_ch1_bad_run_fixtures_would_have_been_refused_before_llm_spend():
    """The failing run 51d635ec carried TWO structural refusals the pipeline ignored:

    1. the derived sequence itself was `blocked` / `block_drafting` (entry_exit_mismatches — every
       scene restarted from the global entry), yet four drafters were dispatched anyway;
    2. even with the sequence approved, scene hard maxes 2200+2400+3200+2600 = 10,400 words
       contradict the 7,200-word chapter hard max — an overrun guaranteed before any LLM call.

    The drafting gate refuses on (1) as preserved, and on (2) once (1) is repaired; both park the
    run in structural_repair_required with zero LLM spend.
    """
    sequence = json.loads((FIXTURES / "chapter_sequence.json").read_text(encoding="utf-8"))
    scene_packets = json.loads((FIXTURES / "scene_packets.json").read_text(encoding="utf-8"))
    packets = {
        int(p["scene_no"]): {"status": str(p["status"]), "word_budget": (p.get("body") or {}).get("word_budget")}
        for p in scene_packets
    }

    # (1) As preserved: the blocked sequence is refused outright.
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status=str(sequence["status"]),
        sequence_qa_verdict=sequence.get("qa_verdict"),
        sequence_body=sequence["body"],
        scene_packets=packets,
    )
    assert decision.ok is False
    assert decision.reason == "sequence_blocked"
    assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED

    # (2) Sequence repaired/approved: the budget contradiction still refuses drafting.
    decision = run_stages.evaluate_drafting_readiness(
        sequence_status="approved",
        sequence_qa_verdict="approve",
        sequence_body=sequence["body"],
        scene_packets=packets,
    )
    assert decision.ok is False
    assert decision.reason == "sequence_budget_mismatch"
    assert decision.next_stage == STAGE_STRUCTURAL_REPAIR_REQUIRED
    assert decision.violations[0]["planned_hard_max_words"] == 10400
    assert decision.violations[0]["chapter_hard_max_words"] == 7200
