"""Root-cause clustering tests for repair triage (pure, no DB / network / LLM).

`triage_production_run` is DB-coupled, so per the recovery plan the clustering
logic lives in `dominion.workers.repair_triage` as pure functions and is tested
here against in-memory Issue-like rows plus the preserved bad Ch1 run fixture.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dominion.workers.repair_triage import (
    ROOT_CAUSE_BUDGET_MISMATCH,
    ROOT_CAUSE_INFRA_RATE_LIMIT,
    ROOT_CAUSE_INSTRUCTIONS,
    ROOT_CAUSE_PROSE_POLISH,
    ROOT_CAUSE_SCENE_SCOPE_BLEED,
    ROOT_CAUSE_SEQUENCE_ENTRY_STATE,
    STRUCTURAL_AUTHORITY,
    STRUCTURAL_ROOT_CAUSES,
    infer_root_cause,
    plan_repair_tasks,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ch1_bad_run" / "production_run_detail.json"


@dataclass
class FakeIssue:
    """In-memory stand-in for the SQLAlchemy Issue row (attribute-compatible)."""

    issue_kind: str
    validator: str
    claim: str = ""
    severity: str = "warn"
    scene_no: int | None = None
    scene_id: uuid.UUID | None = None
    recommended_action: str = ""
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_transition_entry_mismatch_issues_collapse_into_one_sequence_entry_state_cluster():
    """(a) Three per-scene transition/entry-mismatch issues -> ONE root task."""
    issues = [
        FakeIssue(
            issue_kind="pacing",
            validator="pacing",
            scene_no=2,
            claim="Scene opens as if the match never started; transition from scene 1 exit is missing.",
        ),
        FakeIssue(
            issue_kind="pacing",
            validator="pacing",
            scene_no=3,
            claim="Beat timing mismatch: the scene restarts the arc instead of continuing.",
        ),
        FakeIssue(
            issue_kind="sequence_entry_state",
            validator="continuity",
            scene_no=4,
            claim="entry_state equals the global chapter entry despite depends_on_scene_no=3.",
        ),
    ]
    plan = plan_repair_tasks(issues)
    assert list(plan.structural_clusters) == [ROOT_CAUSE_SEQUENCE_ENTRY_STATE]
    cluster = plan.structural_clusters[ROOT_CAUSE_SEQUENCE_ENTRY_STATE]
    assert {issue.id for issue in cluster} == {issue.id for issue in issues}
    assert plan.prose_issues == []
    # One cluster == one chapter-scoped repair task, not three per-scene transitions.
    assert len(plan.structural_clusters) == 1


def test_word_budget_contradiction_produces_one_budget_cluster_not_a_scene_rewrite_swarm():
    """(b) Per-scene length overruns + budget blowout -> ONE budget_mismatch task."""
    issues = [
        FakeIssue(issue_kind="length", validator="length", scene_no=1, claim="over budget: 2090 words (max 1900)"),
        FakeIssue(
            issue_kind="length",
            validator="length",
            severity="hard",
            scene_no=2,
            claim="still over hard_max after compression (2435 > 2400); quarantined as draft",
        ),
        FakeIssue(issue_kind="length", validator="length", scene_no=3, claim="over budget: 2962 words (max 2800)"),
        FakeIssue(
            issue_kind="budget",
            validator="budget",
            severity="hard",
            scene_no=2,
            claim="token budget exceeded (used 20401 / 60000); saved partial draft",
        ),
        FakeIssue(
            issue_kind="sequence_budget_mismatch",
            validator="budget",
            severity="hard",
            claim="scene packet hard_max sum 10400 exceeds chapter hard_max_words 7200",
        ),
    ]
    plan = plan_repair_tasks(issues)
    assert list(plan.structural_clusters) == [ROOT_CAUSE_BUDGET_MISMATCH]
    cluster = plan.structural_clusters[ROOT_CAUSE_BUDGET_MISMATCH]
    assert {issue.id for issue in cluster} == {issue.id for issue in issues}
    assert plan.prose_issues == []


def test_duplicate_recognition_issues_collapse_into_one_scene_scope_bleed_cluster():
    """(c) Duplicated irreversible recognition beats -> ONE scene_scope_bleed task."""
    issues = [
        FakeIssue(
            issue_kind="duplicate_irreversible_beat",
            validator="continuity",
            scene_no=3,
            claim="Recognition (hood tear / red hair) is performed here although scene 4 owns it.",
        ),
        FakeIssue(
            issue_kind="duplicate_irreversible_beat",
            validator="continuity",
            scene_no=4,
            claim="Recognition beat re-performed after scene 3 already staged it.",
        ),
        # Legacy combat symptom that only text-signals the duplication.
        FakeIssue(
            issue_kind="combat",
            validator="combat",
            scene_no=3,
            claim="Double/duplicated hood-tear exposures create ambiguous timing relative to recognition.",
        ),
    ]
    plan = plan_repair_tasks(issues)
    assert list(plan.structural_clusters) == [ROOT_CAUSE_SCENE_SCOPE_BLEED]
    cluster = plan.structural_clusters[ROOT_CAUSE_SCENE_SCOPE_BLEED]
    assert {issue.id for issue in cluster} == {issue.id for issue in issues}


def test_prose_polish_is_deferred_while_structural_clusters_exist():
    """(d) prose_polish tasks are gated behind unresolved structural clusters."""
    structural = FakeIssue(
        issue_kind="budget",
        validator="budget",
        severity="hard",
        claim="token budget exceeded",
    )
    prose = [
        FakeIssue(issue_kind="dialogue", validator="dialogue", scene_no=2, claim="Stilted exchange."),
        FakeIssue(
            issue_kind="pov_knowledge_leak",
            validator="continuity",
            scene_no=4,
            claim="POV states facts Marcus cannot know.",
        ),
    ]
    plan = plan_repair_tasks([structural, *prose])
    assert plan.defer_prose is True
    assert {issue.id for issue in plan.prose_issues} == {issue.id for issue in prose}
    assert list(plan.structural_clusters) == [ROOT_CAUSE_BUDGET_MISMATCH]

    # Without any structural cluster, prose repair proceeds immediately.
    prose_only_plan = plan_repair_tasks(prose)
    assert prose_only_plan.defer_prose is False
    assert prose_only_plan.structural_clusters == {}
    assert {issue.id for issue in prose_only_plan.prose_issues} == {issue.id for issue in prose}


def test_infra_rate_limit_issues_never_join_repair_clusters():
    """Provider 429s are retry state: no structural cluster, no prose task."""
    issues = [
        FakeIssue(
            issue_kind="infra_rate_limit",
            validator="infra",
            claim="provider returned 429 (rate limit); drafting must retry",
        ),
        FakeIssue(issue_kind="dialogue", validator="dialogue", scene_no=1, claim="Flat line delivery."),
    ]
    assert infer_root_cause(issues[0]) == ROOT_CAUSE_INFRA_RATE_LIMIT
    plan = plan_repair_tasks(issues)
    assert plan.structural_clusters == {}
    assert [issue.id for issue in plan.rate_limit_issues] == [issues[0].id]
    assert [issue.id for issue in plan.prose_issues] == [issues[1].id]


def test_ch1_bad_run_fixture_collapses_to_three_structural_root_tasks():
    """Regression over the preserved bad run: 23 accepted issues -> 3 root tasks
    (was 10 symptom repair tasks), with prose polish deferred behind them."""
    detail = json.loads(FIXTURE.read_text(encoding="utf-8"))
    accepted = [
        FakeIssue(
            issue_kind=row["issue_kind"],
            validator=row["validator"],
            severity=row["severity"],
            scene_no=row.get("scene_no"),
            claim=row.get("claim") or "",
            recommended_action=row.get("recommended_action") or "",
        )
        for row in detail["issues"]
        # triage escalates missing_scene and rejects info before clustering
        if row["issue_kind"] != "missing_scene" and row["severity"] != "info"
    ]
    assert len(accepted) == 23

    plan = plan_repair_tasks(accepted)
    assert set(plan.structural_clusters) == {
        ROOT_CAUSE_SEQUENCE_ENTRY_STATE,
        ROOT_CAUSE_SCENE_SCOPE_BLEED,
        ROOT_CAUSE_BUDGET_MISMATCH,
    }
    sizes = {key: len(cluster) for key, cluster in plan.structural_clusters.items()}
    assert sizes == {
        ROOT_CAUSE_SEQUENCE_ENTRY_STATE: 9,
        ROOT_CAUSE_SCENE_SCOPE_BLEED: 1,
        ROOT_CAUSE_BUDGET_MISMATCH: 5,
    }
    assert len(plan.prose_issues) == 8
    assert plan.rate_limit_issues == []
    assert plan.defer_prose is True
    # 3 structural root tasks replace the 10-task repair swarm from the bad run.
    assert len(plan.structural_clusters) < len(detail["repair_tasks"])
    # No issue is lost by clustering.
    clustered = sum(sizes.values()) + len(plan.prose_issues) + len(plan.rate_limit_issues)
    assert clustered == len(accepted)


def test_every_root_cause_is_classified_and_structural_metadata_is_complete():
    """Every issue maps to exactly one pinned key; structural tables stay in sync."""
    samples = {
        ROOT_CAUSE_SEQUENCE_ENTRY_STATE: FakeIssue(issue_kind="pacing", validator="pacing", claim="sag"),
        ROOT_CAUSE_SCENE_SCOPE_BLEED: FakeIssue(issue_kind="scene_scope_bleed", validator="continuity"),
        ROOT_CAUSE_BUDGET_MISMATCH: FakeIssue(issue_kind="length", validator="length"),
        "canon_contract_leak": FakeIssue(issue_kind="canon_contract_leak", validator="canon"),
        ROOT_CAUSE_PROSE_POLISH: FakeIssue(issue_kind="reader_context_gap", validator="continuity"),
        ROOT_CAUSE_INFRA_RATE_LIMIT: FakeIssue(issue_kind="infra_rate_limit", validator="infra"),
    }
    for expected, issue in samples.items():
        assert infer_root_cause(issue) == expected
    assert set(STRUCTURAL_AUTHORITY) == set(STRUCTURAL_ROOT_CAUSES)
    assert set(ROOT_CAUSE_INSTRUCTIONS) == set(STRUCTURAL_ROOT_CAUSES)
