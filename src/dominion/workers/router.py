"""Deterministic coordination (DESIGN §5). A lookup table + a loop decide what runs — never an LLM.

This is the seat that, in the previous system, was an LLM re-reasoning invariants on boot. Here it
executes instantly for zero tokens and cannot spiral.
"""

from __future__ import annotations

from dominion.workers.reviewers.base import Reviewer
from dominion.workers.reviewers.continuity import continuity_reviewer
from dominion.workers.reviewers.lane import combat_reviewer, dialogue_reviewer, sensory_reviewer
from dominion.workers.reviewers.pacing import pacing_reviewer
from dominion.workers.reviewers.state_drift import state_drift_reviewer
from dominion.workers.reviewers.voice import voice_reviewer
from dominion.workers.specialists.base import Specialist
from dominion.workers.specialists.enrich import combat_pass, dialogue_pass, sensory_pass

# Enrichment passes fire only when tagged, and always in this fixed order (determinism).
_PASS_ORDER: list[str] = ["combat", "physical_description", "dialogue"]
DRAFT_PASSES: dict[str, Specialist] = {
    "combat": combat_pass,
    "physical_description": sensory_pass,
    "dialogue": dialogue_pass,
}

# Always-on advisory reviewers (read-only). Continuity stays first; voice/pacing/state-drift each
# gate their own LLM call and stay silent (and free) when they have nothing to assess.
ALWAYS_REVIEWERS: list[Reviewer] = [
    continuity_reviewer,
    voice_reviewer,
    pacing_reviewer,
    state_drift_reviewer,
]
# Tag-gated review lanes — same tags as the enrichment passes (OPEN-8: novel runs combat/sensory/
# dialogue as BOTH a pass and a review lane). reviewers_for() merges these onto ALWAYS_REVIEWERS.
TAG_REVIEWERS: dict[str, list[Reviewer]] = {
    "combat": [combat_reviewer],
    "physical_description": [sensory_reviewer],
    "dialogue": [dialogue_reviewer],
}


def passes_for(tags: list[str]) -> list[Specialist]:
    """Tagged enrichment passes, in canonical order regardless of tag order in the beat."""
    tagset = set(tags)
    return [DRAFT_PASSES[t] for t in _PASS_ORDER if t in tagset]


def reviewers_for(tags: list[str]) -> list[Reviewer]:
    """Continuity (always) + any tag-mapped reviewers. Read-only, advisory."""
    revs: list[Reviewer] = list(ALWAYS_REVIEWERS)
    for t in tags:
        revs.extend(TAG_REVIEWERS.get(t, []))
    return revs
