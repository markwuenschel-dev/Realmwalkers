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
