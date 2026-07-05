"""Length Guard — count words, compare to the ScenePacket budget, rewrite if needed (DESIGN: length).

Runs after enrichment, before the scene is persisted. It is deterministic about *what* to do:

    over hard_max          → compress (always); if still over hard_max → quarantine as DRAFT
    over max, under hard   → compress only if length_auto_compress_over_max, else WARN
    under min              → expand only if skeletal/configured, else INFO
    within budget          → no-op

It returns a GuardResult the pipeline acts on: the final prose, the length_status, any DraftAttempt
rewrite stages to record (raw/final stages are the pipeline's job), a quarantine flag, and an
optional advisory critique. Compression/expansion are injectable so the guard is testable without an
LLM (pipeline passes the real compress/expand from this package).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dominion.shared.agent_registry import model_for_tier, provider_and_tier_of, provider_of
from dominion.shared.config import settings
from dominion.shared.enums import DraftStage, LengthStatus, Severity
from dominion.workers import llm
from dominion.workers.budget import TokenBudget


def _length_model(configured: str) -> str:
    """The length-rewrite model, co-located on the DRAFTER's provider.

    Compress/expand are targeted edits on the just-drafted prose, but `length_compress_model` /
    `length_expand_model` are NOT in the Settings-screen agent registry, so a user who moved every
    drafting agent to (say) OpenAI still had these two pinned to their Anthropic default — a scene
    that tripped the length guard then hit Anthropic and 400'd ("credit balance too low") even though
    the account is provider-only elsewhere. Follow the drafter: keep the configured model's TIER
    (cheap/haiku by design) but on `draft_model`'s provider. Same provider, or an unknown mapping →
    the configured model is returned unchanged, so this only ever redirects a cross-provider mismatch.
    """
    draft_provider = provider_of(settings.draft_model)
    if provider_of(configured) == draft_provider:
        return configured
    hit = provider_and_tier_of(configured)
    tier = hit[1] if hit else "haiku"
    return model_for_tier(tier, draft_provider) or configured


# Inlined from the former length/compress.py + length/expand.py (thin internal impl, not public boundaries).
# Kept here to shrink file count without changing any call sites or behavior.

_WORD_RE = re.compile(r"\b[\w'-]+\b")

# A draft this far below min is "skeletal" — expansion is warranted even when auto-expand is off.
_SKELETAL_RATIO = 0.5

Rewriter = Callable[..., Awaitable[str]]


def count_words(text: str | None) -> int:
    """Deterministic word count. Good enough for budgeting; matches the planner's units."""
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


@dataclass
class StageRecord:
    stage: str
    prose: str
    word_count: int
    model: str | None = None


@dataclass
class GuardResult:
    prose: str
    word_count: int
    length_status: str
    quarantine: bool = False
    stages: list[StageRecord] = field(default_factory=list)
    critique: tuple[str, str] | None = None  # (severity, note)


async def apply_length_guard(
    prose: str,
    *,
    word_budget: dict[str, Any] | None,
    scene_contract: dict[str, Any] | None,
    budget: TokenBudget,
    compress: Rewriter | None = None,
    expand: Rewriter | None = None,
) -> GuardResult:
    """Enforce the scene's word budget on `prose`. See module docstring for the decision table."""
    wc = count_words(prose)
    if not word_budget:
        return GuardResult(prose=prose, word_count=wc, length_status=LengthStatus.WITHIN_BUDGET)

    compress = compress or _compress
    expand = expand or _expand
    minimum = int(word_budget.get("min") or 0)
    maximum = int(word_budget.get("max") or 0)
    hard_max = int(word_budget.get("hard_max") or 0)

    # --- over hard_max: always compress -----------------------------------------------------------
    if hard_max and wc > hard_max:
        new = await compress(prose, word_budget=word_budget, scene_contract=scene_contract, budget=budget)
        new_wc = count_words(new)
        stage = StageRecord(DraftStage.LENGTH_COMPRESSION, new, new_wc, _length_model(settings.length_compress_model))
        if new_wc > hard_max and settings.length_hard_fail_over_hard_max:
            return GuardResult(
                prose=new,
                word_count=new_wc,
                length_status=LengthStatus.OVER_HARD_MAX_QUARANTINED,
                quarantine=True,
                stages=[stage],
                critique=(
                    Severity.BLOCK,
                    f"still over hard_max after compression ({new_wc} > {hard_max}); quarantined as draft",
                ),
            )
        status = LengthStatus.WITHIN_BUDGET if maximum and new_wc <= maximum else LengthStatus.OVER_HARD_MAX_COMPRESSED
        return GuardResult(
            prose=new,
            word_count=new_wc,
            length_status=status,
            stages=[stage],
            critique=(Severity.INFO, f"compressed from {wc} to {new_wc} words (hard_max {hard_max})"),
        )

    # --- over max, under hard_max -----------------------------------------------------------------
    if maximum and wc > maximum:
        if settings.length_auto_compress_over_max:
            new = await compress(prose, word_budget=word_budget, scene_contract=scene_contract, budget=budget)
            new_wc = count_words(new)
            stage = StageRecord(
                DraftStage.LENGTH_COMPRESSION, new, new_wc, _length_model(settings.length_compress_model)
            )
            status = LengthStatus.WITHIN_BUDGET if new_wc <= maximum else LengthStatus.OVER_MAX
            return GuardResult(
                prose=new,
                word_count=new_wc,
                length_status=status,
                stages=[stage],
                critique=(Severity.INFO, f"compressed from {wc} to {new_wc} words (max {maximum})"),
            )
        return GuardResult(
            prose=prose,
            word_count=wc,
            length_status=LengthStatus.OVER_MAX,
            critique=(Severity.WARN, f"over budget: {wc} words (max {maximum})"),
        )

    # --- under min --------------------------------------------------------------------------------
    if minimum and wc < minimum:
        skeletal = wc < int(minimum * _SKELETAL_RATIO)
        if settings.length_auto_expand_under_min or skeletal:
            new = await expand(prose, word_budget=word_budget, scene_contract=scene_contract, budget=budget)
            new_wc = count_words(new)
            stage = StageRecord(DraftStage.LENGTH_EXPANSION, new, new_wc, _length_model(settings.length_expand_model))
            status = LengthStatus.WITHIN_BUDGET if new_wc >= minimum else LengthStatus.UNDER_MIN
            return GuardResult(
                prose=new,
                word_count=new_wc,
                length_status=status,
                stages=[stage],
                critique=(Severity.INFO, f"expanded from {wc} to {new_wc} words (min {minimum})"),
            )
        return GuardResult(
            prose=prose,
            word_count=wc,
            length_status=LengthStatus.UNDER_MIN,
            critique=(Severity.INFO, f"under budget: {wc} words (min {minimum})"),
        )

    return GuardResult(prose=prose, word_count=wc, length_status=LengthStatus.WITHIN_BUDGET)


# --- inlined length/compress + length/expand (private to guard) ---------------------------------
# (Previously separate files; only ever imported internally by guard. Merged for contraction.)


def _section(label: str, items: list[str]) -> str:
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return ""
    return f"{label}:\n" + "\n".join(f"- {i}" for i in items) + "\n\n"


_COMPRESS_SYSTEM = (
    "You are a line editor compressing an existing scene to fit a word budget. You do NOT rewrite "
    "the story. Compress without changing canon, reveals, POV knowledge, scene outcome, or voice. "
    "Do not add new facts. Do not remove required beats. Do not explain hidden canon. "
    "Output the revised prose only — no preamble, no notes."
)


def _build_compress_prompt(
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


async def _compress(
    prose: str,
    *,
    word_budget: dict[str, Any],
    scene_contract: dict[str, Any] | None = None,
    budget: TokenBudget,
    max_tokens: int = 8000,
) -> str:
    """Inlined compression (was length.compress.compress)."""
    contract = scene_contract or {}
    user = _build_compress_prompt(
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
        model=_length_model(settings.length_compress_model),
        system=_COMPRESS_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        budget=budget,
        expect_cache=False,
        # Length rewrites are co-located on the drafter's provider, so they follow the drafter's backend
        # too — flipping the drafter to agent_cli routes its length fix-ups through the CLI as well.
        setting_key="draft_model",
    )
    return text.strip() or prose


_EXPAND_SYSTEM = (
    "You are a line editor expanding an existing scene that is too short. Expand ONLY through physical "
    "grounding, reaction beats, and clarity around the required beats. Do not add new plot events. "
    "Do not add new lore. Do not reveal hidden facts. Keep the POV's voice. "
    "Output the revised prose only — no preamble, no notes."
)


def _build_expand_prompt(
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


async def _expand(
    prose: str,
    *,
    word_budget: dict[str, Any],
    scene_contract: dict[str, Any] | None = None,
    budget: TokenBudget,
    max_tokens: int = 8000,
) -> str:
    """Inlined expansion (was length.expand.expand)."""
    contract = scene_contract or {}
    user = _build_expand_prompt(
        prose,
        target=int(word_budget.get("target") or word_budget.get("min") or 0),
        max_words=int(word_budget.get("max") or word_budget.get("hard_max") or 0),
        required_beats=contract.get("required_beats") or [],
        exit_state=contract.get("exit_state"),
        expansion_priority=word_budget.get("expansion_priority") or [],
    )
    text, _usage = await llm.complete(
        model=_length_model(settings.length_expand_model),
        system=_EXPAND_SYSTEM,
        user=user,
        max_tokens=max_tokens,
        budget=budget,
        expect_cache=False,
        # Length rewrites follow the drafter's backend (see _compress).
        setting_key="draft_model",
    )
    return text.strip() or prose
