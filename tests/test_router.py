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
    for tag, lane in [("combat", "combat"), ("sensory", "sensory"), ("dialogue", "dialogue")]:
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


def test_every_producible_lane_tag_is_routable() -> None:
    # Integrity guard against tag/lane vocabulary drift. Every lane tag the scene-packet producer can
    # stamp onto a Beat (beats._LANE_TAGS) MUST be routable: it has to select its enrichment pass AND
    # its review lane. The sensory pass/review lane was silently dead because the router keyed the lane
    # on "physical_description" while the producer emitted "sensory", and the lane test above fed the
    # router its OWN vocabulary instead of the producer's — so nothing caught it. Keying DRAFT_PASSES/
    # TAG_REVIEWERS off each pass/reviewer .name locks the two sides together; this fails loudly if any
    # producible tag ever becomes unroutable again.
    from dominion.workers.scene_packet.beats import _LANE_TAGS

    for tag in _LANE_TAGS:
        assert [p.name for p in passes_for([tag])] == [tag], f"lane tag {tag!r} triggers no enrichment pass"
        assert tag in [r.name for r in reviewers_for([tag])], f"lane tag {tag!r} has no review lane"
