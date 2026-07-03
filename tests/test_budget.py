"""Unit tests for the token budget's cost weighting (workers/budget.py).

The budget bounds a job's *work*, not its raw token count: a cache READ is re-sent context (near-free,
~0.1x) while fresh input/output/cache-writes count in full. `budget_cost` weights them so a large
cached prefix doesn't re-count in full on every call — the regression that blocked scene-packet
derivation — without ever penalizing the scene that pays the cache write.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dominion.workers import llm
from dominion.workers.budget import BudgetExceeded, TokenBudget, Usage
from dominion.workers.llm import CachedPrefixBlock, ContextWindowExceeded, check_context_window


def test_budget_cost_discounts_cache_reads():
    u = Usage(input_tokens=100, output_tokens=200, cache_creation_tokens=1000, cache_read_tokens=4000)
    # input + output + cache write at 1.0 (all real first-time work); cache read at 0.1.
    assert u.budget_cost == int(100 + 200 + 1000 * 1.0 + 4000 * 0.1)
    # And the weighted cost is below the raw token count, because re-read cache is discounted.
    assert u.budget_cost < u.total


def test_charge_uses_weighted_cost_not_raw_total():
    budget = TokenBudget(max_tokens=10_000)
    # 9k of cache_read would blow a 10k ceiling at full weight (the old bug); weighted it costs ~900.
    budget.charge(Usage(input_tokens=500, output_tokens=500, cache_read_tokens=9000))
    assert budget.used == int(500 + 500 + 9000 * 0.1)  # 1900, well under the ceiling
    # Raw token counts are still tracked verbatim for reporting.
    assert budget.total_cache_read == 9000


# --- soft / hard budget split ---------------------------------------------------------------------


def test_soft_overage_under_hard_does_not_raise():
    # The production fix: a scene that lands a few tokens over the soft target (the recurring
    # `60043 > 60000`) keeps its valid output — soft overage warns, it does not block.
    budget = TokenBudget(max_tokens=60_000, hard_max_tokens=75_000)
    result = budget.charge(Usage(input_tokens=60_043, output_tokens=0))
    assert budget.used == 60_043
    assert result.soft_exceeded and not result.hard_exceeded
    assert budget.soft_exceeded and not budget.hard_exceeded


def test_hard_overage_still_raises():
    budget = TokenBudget(max_tokens=60_000, hard_max_tokens=75_000)
    with pytest.raises(BudgetExceeded):
        budget.charge(Usage(input_tokens=75_001, output_tokens=0))


def test_context_window_guard_uses_raw_tokens_not_weighted_cache_cost():
    # Weighted budget would pass because cache reads are discounted to ~10%, but raw context-window
    # safety must count the full cached prefix plus output allowance.
    budget = TokenBudget(max_tokens=10_000)
    budget.charge(Usage(input_tokens=100, output_tokens=100, cache_read_tokens=40_000))
    assert budget.used == 4_200

    with pytest.raises(ContextWindowExceeded, match="context_window_budget=1000"):
        check_context_window(
            system="s",
            user="u",
            max_tokens=100,
            context_window_budget=1000,
            user_prefix_blocks=(CachedPrefixBlock("chapter_shared_prefix", "x" * 8000),),
            context_sections={"chapter_shared_prefix": 2_000},
        )


# --- consolidated LLM context-window preflight (moved from deleted test_llm_token_counting.py) -----
# The token-counting/provider plumbing suite was dropped, but the context-window preflight gate sits
# right next to the budget/timeout hot branch, so one consolidated case stays: preflight runs before
# generation and skips generation entirely once the counted input + output allowance overflows the
# context window.


class _FakeMessages:
    def __init__(self, *, count_value: int = 100) -> None:
        self.events: list[str] = []
        self._count_value = count_value

    async def count_tokens(self, **kwargs: object) -> object:
        self.events.append("count")
        return SimpleNamespace(input_tokens=self._count_value)

    async def create(self, **kwargs: object) -> object:
        self.events.append("create")
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            content=[SimpleNamespace(type="text", text="{}")],
            stop_reason="end_turn",
        )


async def _preflight_complete(msgs, monkeypatch, **over):
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(messages=msgs))
    kwargs: dict[str, object] = dict(
        model="m",
        system="s",
        user="u",
        max_tokens=50,
        budget=TokenBudget(max_tokens=10_000),
        context_window_budget=200_000,
    )
    kwargs.update(over)
    return await llm.complete(**kwargs)


async def test_preflight_counts_tokens_before_generation(monkeypatch):
    msgs = _FakeMessages(count_value=100)
    await _preflight_complete(msgs, monkeypatch)
    assert msgs.events == ["count", "create"]  # context-window preflight runs before generation


async def test_preflight_skips_create_when_context_window_exceeded(monkeypatch):
    msgs = _FakeMessages(count_value=500)
    with pytest.raises(ContextWindowExceeded, match="context window preflight exceeded"):
        await _preflight_complete(msgs, monkeypatch, max_tokens=600, context_window_budget=1000)
    assert msgs.events == ["count"]  # generation never reached once preflight is over the window
