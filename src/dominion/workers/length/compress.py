"""Length compression pass (DESIGN: word budgeting → length guard).

Runs on the cheap review/enrich tier, never the main draft model. Compresses prose toward the scene's
target without changing canon, reveals, POV knowledge, outcome, or voice. It is a targeted edit on an
existing draft, so it must add no new facts and remove no required beats.
"""

from __future__ import annotations

from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget

_SYSTEM = (
    "You are a line editor compressing an existing scene to fit a word budget. You do NOT rewrite "
    "the story. Compress without changing canon, reveals, POV knowledge, scene outcome, or voice. "
    "Do not add new facts. Do not remove required beats. Do not explain hidden canon. "
    "Output the revised prose only — no preamble, no notes."
)


def _section(label: str, items: list[str]) -> str:
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return ""
    return f"{label}:\n" + "\n".join(f"- {i}" for i in items) + "\n\n"


def build_prompt(
    prose: str,
    *,
    target: int,
    hard_max: int,
    required_beats: list[str],
    forbidden_beats: list[str],
    exit_state: str | None,
    compression_priority: list[str],
    must_not_spend_words_on: list[str],
) -> str:
    parts = [
        f"Compress this scene to about {target} words (never exceed {hard_max}).\n\n",
        _section("Required beats (must remain)", required_beats),
        _section("Forbidden beats (must stay absent)", forbidden_beats),
        f"Exit state (must hold): {exit_state}\n\n" if exit_state else "",
        _section("Compression priorities (cut in this order)", compression_priority),
        _section("Must not spend words on", must_not_spend_words_on),
        "PROSE:\n",
        prose,
    ]
    return "".join(parts)


async def compress(
    prose: str,
    *,
    word_budget: dict[str, Any],
    scene_contract: dict[str, Any] | None = None,
    budget: TokenBudget,
    max_tokens: int = 8000,
) -> str:
    """One bounded compression call. Returns the revised prose (or the original on an empty reply)."""
    contract = scene_contract or {}
    user = build_prompt(
        prose,
        target=int(word_budget.get("target") or word_budget.get("max") or 0),
        hard_max=int(word_budget.get("hard_max") or word_budget.get("max") or 0),
        required_beats=contract.get("required_beats") or [],
        forbidden_beats=contract.get("forbidden_beats") or [],
        exit_state=contract.get("exit_state"),
        compression_priority=word_budget.get("compression_priority") or [],
        must_not_spend_words_on=word_budget.get("must_not_spend_words_on") or [],
    )
    text, _usage = await llm.complete(
        model=settings.length_compress_model,
        system=_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        budget=budget,
        expect_cache=False,
    )
    return text.strip() or prose
