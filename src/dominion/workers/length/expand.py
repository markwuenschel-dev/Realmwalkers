"""Length expansion pass (DESIGN: word budgeting → length guard).

Used sparingly: only when a draft is below its budget and either skeletal or missing required beats.
Expands through physical grounding, reaction beats, and clarity — never new plot, lore, or reveals.
"""

from __future__ import annotations

from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.length.compress import _section

_SYSTEM = (
    "You are a line editor expanding an existing scene that is too short. Expand ONLY through physical "
    "grounding, reaction beats, and clarity around the required beats. Do not add new plot events. "
    "Do not add new lore. Do not reveal hidden facts. Keep the POV's voice. "
    "Output the revised prose only — no preamble, no notes."
)


def build_prompt(
    prose: str,
    *,
    target: int,
    max_words: int,
    required_beats: list[str],
    exit_state: str | None,
    expansion_priority: list[str],
) -> str:
    parts = [
        f"Expand this scene to about {target} words (do not exceed {max_words}).\n\n",
        _section("Required beats (ground these)", required_beats),
        f"Exit state (must hold): {exit_state}\n\n" if exit_state else "",
        _section("Expansion priorities", expansion_priority),
        "PROSE:\n",
        prose,
    ]
    return "".join(parts)


async def expand(
    prose: str,
    *,
    word_budget: dict[str, Any],
    scene_contract: dict[str, Any] | None = None,
    budget: TokenBudget,
    max_tokens: int = 8000,
) -> str:
    """One bounded expansion call. Returns the revised prose (or the original on an empty reply)."""
    contract = scene_contract or {}
    user = build_prompt(
        prose,
        target=int(word_budget.get("target") or word_budget.get("min") or 0),
        max_words=int(word_budget.get("max") or word_budget.get("hard_max") or 0),
        required_beats=contract.get("required_beats") or [],
        exit_state=contract.get("exit_state"),
        expansion_priority=word_budget.get("expansion_priority") or [],
    )
    text, _usage = await llm.complete(
        model=settings.length_expand_model,
        system=_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        budget=budget,
        expect_cache=False,
    )
    return text.strip() or prose
