"""The deterministic coordinator is plain, testable code — no LLM, no I/O (DESIGN §5)."""

from __future__ import annotations

from dominion.workers.router import passes_for, reviewers_for


def test_passes_run_in_canonical_order_regardless_of_tag_order() -> None:
    passes = passes_for(["dialogue", "combat"])  # beat lists them reversed
    assert [p.name for p in passes] == ["combat", "dialogue"]


def test_untagged_scene_runs_no_enrichment_passes() -> None:
    assert passes_for([]) == []


def test_unknown_tags_are_ignored() -> None:
    assert passes_for(["nonsense", "combat"]) == [p for p in passes_for(["combat"])]
    assert [p.name for p in passes_for(["nonsense", "combat"])] == ["combat"]


def test_continuity_always_reviews() -> None:
    assert "continuity" in [r.name for r in reviewers_for([])]


def test_tag_review_lanes_merge_onto_always_reviewers() -> None:
    # A tagged lane reviewer joins the always-on set, keyed by the same tag as its enrichment pass.
    for tag, lane in [("combat", "combat"), ("physical_description", "sensory"), ("dialogue", "dialogue")]:
        names = [r.name for r in reviewers_for([tag])]
        assert lane in names
        assert "continuity" in names  # always-on reviewers still run


def test_untagged_scene_runs_only_always_reviewers() -> None:
    assert [r.name for r in reviewers_for([])] == [r.name for r in reviewers_for([])]
    assert "combat" not in [r.name for r in reviewers_for([])]


def test_pass_and_review_lane_share_a_tag() -> None:
    # OPEN-8: combat/sensory/dialogue run as BOTH a pass and a review lane off the same beat tag.
    assert [p.name for p in passes_for(["combat"])] == ["combat"]
    assert "combat" in [r.name for r in reviewers_for(["combat"])]
