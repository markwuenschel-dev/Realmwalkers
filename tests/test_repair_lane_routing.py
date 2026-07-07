"""Fitness check for enrichment-lane repair routing (audit candidate D2, 2026-07-06).

OPEN-8 runs combat / sensory / dialogue as BOTH an enrichment pass and a review lane. When a lane
reviewer flags an issue, the repair must be routed back to that lane's own enrichment pass — the pass
is the tool built to fix exactly what the reviewer critiques. Sensory and dialogue did this; combat
silently did not: ``validator == "combat"`` fell through ``_infer_repair_kind`` to ``"reader_context"``,
which ``_target_pass_for_task`` maps nowhere, so a combat critique became a full scene revision instead
of a combat-pass revision.

The guard asserts every reviewed enrichment lane round-trips to its own pass, so a future lane that is
registered as pass + reviewer without a repair route fails here instead of silently full-revising.
"""

from __future__ import annotations

from dominion.shared.models import Issue, RepairTask
from dominion.workers import router
from dominion.workers.production_repair import _infer_repair_kind, _target_pass_for_task

# Lanes that run as BOTH an enrichment pass and a review lane (combat/sensory/dialogue).
_REVIEWED_ENRICHMENT_LANES = sorted(set(router.DRAFT_PASSES) & set(router.TAG_REVIEWERS))


def _pass_for_validator(validator: str) -> str | None:
    """Full routing chain a lane critique travels: validator -> repair_kind -> target pass."""
    issue = Issue(validator=validator, issue_kind="lane_note")
    kind = _infer_repair_kind(issue)
    return _target_pass_for_task(RepairTask(repair_kind=kind))


def test_every_reviewed_enrichment_lane_repairs_back_to_its_own_pass():
    # RED before the fix: combat routed to None (full revision) instead of the combat pass.
    assert _REVIEWED_ENRICHMENT_LANES, "expected combat/sensory/dialogue lanes to be registered"
    for lane in _REVIEWED_ENRICHMENT_LANES:
        assert _pass_for_validator(lane) == lane, (
            f"{lane!r} runs as a pass + review lane but its critiques do not repair back to the "
            f"{lane!r} pass (got target pass {_pass_for_validator(lane)!r})"
        )


def test_combat_critique_routes_to_the_combat_pass():
    # Regression anchor for D2 specifically.
    assert _infer_repair_kind(Issue(validator="combat", issue_kind="lane_note")) == "combat"
    assert _target_pass_for_task(RepairTask(repair_kind="combat")) == "combat"


def test_repair_kind_pass_targets_are_all_real_passes():
    # Every non-None target must be a real router.DRAFT_PASSES lane, so a typo can't silently
    # produce an unroutable REVISE_PASS job that filters to zero specialists.
    from dominion.workers.production_repair import _REPAIR_KIND_TO_PASS

    pass_names = set(router.DRAFT_PASSES)
    for kind, target in _REPAIR_KIND_TO_PASS.items():
        if target is not None:
            assert target in pass_names, f"repair_kind {kind!r} targets unknown pass {target!r}"
