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

from dominion.shared.config import settings
from dominion.shared.enums import DraftStage, LengthStatus, Severity
from dominion.workers.budget import TokenBudget
from dominion.workers.length import compress as _compress_mod
from dominion.workers.length import expand as _expand_mod

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

    compress = compress or _compress_mod.compress
    expand = expand or _expand_mod.expand
    minimum = int(word_budget.get("min") or 0)
    maximum = int(word_budget.get("max") or 0)
    hard_max = int(word_budget.get("hard_max") or 0)

    # --- over hard_max: always compress -----------------------------------------------------------
    if hard_max and wc > hard_max:
        new = await compress(prose, word_budget=word_budget, scene_contract=scene_contract, budget=budget)
        new_wc = count_words(new)
        stage = StageRecord(DraftStage.LENGTH_COMPRESSION, new, new_wc, settings.length_compress_model)
        if new_wc > hard_max and settings.length_hard_fail_over_hard_max:
            return GuardResult(
                prose=new,
                word_count=new_wc,
                length_status=LengthStatus.OVER_HARD_MAX_QUARANTINED,
                quarantine=True,
                stages=[stage],
                critique=(
                    Severity.HARD,
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
            stage = StageRecord(DraftStage.LENGTH_COMPRESSION, new, new_wc, settings.length_compress_model)
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
            stage = StageRecord(DraftStage.LENGTH_EXPANSION, new, new_wc, settings.length_expand_model)
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
