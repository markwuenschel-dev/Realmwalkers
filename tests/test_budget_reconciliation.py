"""Lane 3 — reconcile scene word budgets against the chapter envelope.

Deterministic, pure-Python: no network, no LLM, no Postgres.
"""

from __future__ import annotations

import json
from pathlib import Path

from dominion.workers.budget_reconciliation import (
    SEQUENCE_BUDGET_MISMATCH,
    check_sequence_budget_consistency,
    reconcile,
)
from dominion.workers.length.planner import plan_word_budgets

FIXTURES = Path(__file__).parent / "fixtures" / "ch1_bad_run"


def _ch1_sequence() -> dict:
    return json.loads((FIXTURES / "chapter_sequence.json").read_text(encoding="utf-8"))


def _budgets(sequence: dict) -> list[dict]:
    return [scene["word_budget"] for scene in sequence["body"]["scenes"]]


class TestCh1BadRunFixture:
    """(a) The real bad-run numbers: 10,400 scene hard_max vs 7,200 chapter."""

    def test_fixture_is_the_documented_contradiction(self):
        sequence = _ch1_sequence()
        budgets = _budgets(sequence)
        assert sequence["hard_max_words"] == 7200
        assert [b["hard_max"] for b in budgets] == [2200, 2400, 3200, 2600]
        assert sum(b["hard_max"] for b in budgets) == 10400

    def test_reconciles_to_consistent_scaled_envelope(self):
        sequence = _ch1_sequence()
        budgets = _budgets(sequence)
        result = reconcile(sequence["hard_max_words"], budgets)

        assert result.issues == ()
        assert result.changed is True
        assert not result.blocking
        assert sum(b["hard_max"] for b in result.budgets) <= 7200

    def test_scaling_preserves_min_floors_and_per_scene_ordering(self):
        sequence = _ch1_sequence()
        budgets = _budgets(sequence)
        result = reconcile(sequence["hard_max_words"], budgets)

        for original, scaled in zip(budgets, result.budgets, strict=True):
            assert scaled["min"] == original["min"]  # floor preserved
            assert scaled["min"] <= scaled["target"] <= scaled["max"] <= scaled["hard_max"]
            # Scaled down, never up.
            assert scaled["hard_max"] <= original["hard_max"]
            assert scaled["target"] <= original["target"]
            assert scaled["max"] <= original["max"]

    def test_scaling_preserves_relative_weights(self):
        """Scene 3 was the biggest scene and must stay the biggest."""
        sequence = _ch1_sequence()
        result = reconcile(sequence["hard_max_words"], _budgets(sequence))
        hard_maxes = [b["hard_max"] for b in result.budgets]
        assert hard_maxes.index(max(hard_maxes)) == 2
        assert sorted(hard_maxes) == sorted(hard_maxes)  # sanity
        # Same rank order as the originals: 3 > 4 > 2 > 1.
        assert hard_maxes[2] > hard_maxes[3] > hard_maxes[1] > hard_maxes[0]

    def test_non_numeric_budget_keys_survive_scaling(self):
        sequence = _ch1_sequence()
        budgets = _budgets(sequence)
        result = reconcile(sequence["hard_max_words"], budgets)
        for original, scaled in zip(budgets, result.budgets, strict=True):
            assert scaled["compression_priority"] == original["compression_priority"]
            assert scaled["expansion_priority"] == original["expansion_priority"]

    def test_accepts_full_sequence_mapping(self):
        """reconcile() can take the sequence dump directly (body.scenes)."""
        sequence = _ch1_sequence()
        result = reconcile(sequence)
        assert result.changed is True
        assert sum(b["hard_max"] for b in result.budgets) <= 7200


class TestConsistentEnvelope:
    """(b) A valid envelope passes through untouched."""

    def test_untouched_when_sum_fits(self):
        budgets = [
            {"min": 800, "target": 1000, "max": 1200, "hard_max": 1400},
            {"min": 900, "target": 1200, "max": 1500, "hard_max": 1700},
        ]
        result = reconcile(7200, budgets)
        assert result.issues == ()
        assert result.changed is False
        assert list(result.budgets) == budgets
        assert result.budgets[0] is not budgets[0]  # defensive copy

    def test_exactly_at_envelope_is_consistent(self):
        budgets = [
            {"min": 1000, "target": 2000, "max": 3000, "hard_max": 3600},
            {"min": 1000, "target": 2000, "max": 3000, "hard_max": 3600},
        ]
        result = reconcile(7200, budgets)
        assert result.changed is False
        assert result.issues == ()

    def test_no_scenes_is_consistent(self):
        result = reconcile(7200, [])
        assert result.budgets == ()
        assert result.issues == ()
        assert result.changed is False

    def test_consistent_envelope_has_no_gate_issues(self):
        budgets = [{"min": 500, "target": 700, "max": 900, "hard_max": 1000}]
        assert check_sequence_budget_consistency(7200, budgets) == []


class TestImpossibleEnvelope:
    """(c) Min floors above the chapter envelope: blocking mismatch."""

    def test_blocking_sequence_budget_mismatch(self):
        budgets = [
            {"min": 3000, "target": 3200, "max": 3400, "hard_max": 3600},
            {"min": 3000, "target": 3200, "max": 3400, "hard_max": 3600},
            {"min": 3000, "target": 3200, "max": 3400, "hard_max": 3600},
        ]
        result = reconcile(7200, budgets)  # floors sum to 9000 > 7200
        assert result.blocking
        assert result.changed is False
        assert list(result.budgets) == budgets  # nothing rewritten
        (issue,) = result.issues
        assert issue.kind == SEQUENCE_BUDGET_MISMATCH
        assert issue.blocks_drafting is True
        assert "9000" in issue.detail and "7200" in issue.detail

    def test_gate_check_blocks_impossible_envelope(self):
        budgets = [
            {"min": 4000, "target": 4200, "max": 4400, "hard_max": 4600},
            {"min": 4000, "target": 4200, "max": 4400, "hard_max": 4600},
        ]
        issues = check_sequence_budget_consistency(7200, budgets)
        assert len(issues) == 1
        assert issues[0].kind == SEQUENCE_BUDGET_MISMATCH
        assert issues[0].blocks_drafting is True


class TestSingleGlobalBlocker:
    """(d) One global contract error yields ONE issue — no per-scene spam."""

    def test_one_issue_regardless_of_scene_count(self):
        budgets = [
            {"min": 2000, "target": 2100, "max": 2200, "hard_max": 2300} for _ in range(8)
        ]  # floors sum to 16,000 >> 7,200
        result = reconcile(7200, budgets)
        assert len(result.issues) == 1
        assert result.issues[0].kind == SEQUENCE_BUDGET_MISMATCH

    def test_gate_check_emits_one_issue_for_persisted_overflow(self):
        """The ch1 case as stored data: scalable, but the gate can't rewrite
        persisted packets, so it must block with ONE mismatch issue."""
        sequence = _ch1_sequence()
        issues = check_sequence_budget_consistency(sequence["hard_max_words"], _budgets(sequence))
        assert len(issues) == 1
        assert issues[0].kind == SEQUENCE_BUDGET_MISMATCH
        assert issues[0].blocks_drafting is True
        assert "10400" in issues[0].detail and "7200" in issues[0].detail

    def test_gate_check_skips_sequences_without_a_numeric_envelope(self):
        """Missing envelope is not the ch1 contradiction — nothing to compare,
        so the gate must not false-positive legacy rows."""
        budgets = [{"min": 500, "target": 700, "max": 900, "hard_max": 9000}]
        assert check_sequence_budget_consistency(None, budgets) == []


class TestPlannerWiring:
    """plan_word_budgets emits budgets already reconciled with the envelope."""

    def test_ch1_manual_budgets_come_out_reconciled(self):
        """Replaying the ch1 numbers through the planner (manual seed budgets
        override allocation) now yields a consistent envelope."""
        sequence = _ch1_sequence()
        seeds = [
            {
                "seed_id": scene["seed_id"],
                "scene_type": scene.get("scene_type"),
                "word_budget": {k: scene["word_budget"][k] for k in ("min", "target", "max", "hard_max")},
            }
            for scene in sequence["body"]["scenes"]
        ]
        budgets = plan_word_budgets(
            chapter_target_words=7200,
            chapter_max_words=7200,
            scene_seeds=seeds,
        )
        assert len(budgets) == 4
        assert sum(b["hard_max"] for b in budgets.values()) <= 7200
        for seed in seeds:
            b = budgets[str(seed["seed_id"])]
            assert b["min"] <= b["target"] <= b["max"] <= b["hard_max"]
            assert b["min"] == seed["word_budget"]["min"]  # floor preserved

    def test_auto_planned_budgets_sum_within_envelope(self):
        """The default 1.6x hard_max multiplier used to overflow the chapter
        cap in aggregate (4 x 1800-target scenes -> 11,520 vs 7,200)."""
        seeds = [{"seed_id": f"seed-{n}", "scene_type": "dialogue"} for n in range(4)]
        budgets = plan_word_budgets(
            chapter_target_words=7200,
            chapter_max_words=7200,
            scene_seeds=seeds,
        )
        assert sum(b["hard_max"] for b in budgets.values()) <= 7200
        for b in budgets.values():
            assert b["min"] <= b["target"] <= b["max"] <= b["hard_max"]

    def test_consistent_plan_is_not_rescaled(self):
        """A chapter with plenty of envelope headroom passes through the
        planner exactly as before (no churn for healthy chapters)."""
        seeds = [{"seed_id": "a", "scene_type": "dialogue"}]
        budgets = plan_word_budgets(
            chapter_target_words=1500,
            chapter_max_words=7200,
            scene_seeds=seeds,
        )
        b = budgets["a"]
        # Untouched deterministic shape: target 1500, hard_max 1500*1.6.
        assert b["target"] == 1500
        assert b["hard_max"] == 2400
