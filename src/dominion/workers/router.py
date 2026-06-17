"""Deterministic coordination (DESIGN §5). A lookup table + a loop decide what runs — never an LLM.

This is the seat that, in the previous system, was an LLM re-reasoning invariants on boot. Here it
executes instantly for zero tokens and cannot spiral.
"""
from __future__ import annotations

from dominion.workers.reviewers.base import Reviewer
from dominion.workers.reviewers.continuity import continuity_reviewer
from dominion.workers.specialists.base import Specialist
from dominion.workers.specialists.combat import combat_pass
from dominion.workers.specialists.dialogue import dialogue_pass
from dominion.workers.specialists.sensory import sensory_pass

# Enrichment passes fire only when tagged, and always in this fixed order (determinism).
_PASS_ORDER: list[str] = ["combat", "physical_description", "dialogue"]
DRAFT_PASSES: dict[str, Specialist] = {
    "combat": combat_pass,
    "physical_description": sensory_pass,
    "dialogue": dialogue_pass,
}

# Continuity always runs (advisory). Phase 3 adds domain + pacing/voice reviewers here.
ALWAYS_REVIEWERS: list[Reviewer] = [continuity_reviewer]
TAG_REVIEWERS: dict[str, list[Reviewer]] = {}


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
