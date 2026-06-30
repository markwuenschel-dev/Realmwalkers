"""Unit tests for the token budget's cost weighting (workers/budget.py).

The budget bounds a job's *work*, not its raw token count: a cache READ is re-sent context (near-free,
~0.1x) while fresh input/output/cache-writes count in full. `budget_cost` weights them so a large
cached prefix doesn't re-count in full on every call — the regression that blocked scene-packet
derivation — without ever penalizing the scene that pays the cache write.
"""

from __future__ import annotations

import pytest

from dominion.workers.budget import BudgetExceeded, TokenBudget, Usage
from dominion.workers.llm import CachedPrefixBlock, ContextWindowExceeded, check_context_window


def test_total_stays_raw_token_count():
    # `total` is the raw billing count used for reporting/rate-limit tracking — unweighted.
    u = Usage(input_tokens=100, output_tokens=200, cache_creation_tokens=1000, cache_read_tokens=4000)
    assert u.total == 100 + 200 + 1000 + 4000


def test_budget_cost_discounts_cache_reads():
    u = Usage(input_tokens=100, output_tokens=200, cache_creation_tokens=1000, cache_read_tokens=4000)
    # input + output + cache write at 1.0 (all real first-time work); cache read at 0.1.
    assert u.budget_cost == int(100 + 200 + 1000 * 1.0 + 4000 * 0.1)
    # And the weighted cost is below the raw token count, because re-read cache is discounted.
    assert u.budget_cost < u.total


def test_budget_cost_does_not_penalize_cache_writes():
    # A scene that warms the cache (large write, no read) is charged the write at full weight only —
    # never a premium — so priming the cache for later scenes never pushes the primer over its ceiling.
    writer = Usage(input_tokens=100, output_tokens=200, cache_creation_tokens=10_000)
    no_cache = Usage(input_tokens=100, output_tokens=200, cache_creation_tokens=0, cache_read_tokens=0)
    assert writer.budget_cost == 100 + 200 + 10_000  # write == plain input weight
    assert writer.budget_cost - no_cache.budget_cost == 10_000


def test_budget_cost_equals_total_without_cache():
    u = Usage(input_tokens=10, output_tokens=20)
    assert u.budget_cost == u.total == 30


def test_charge_uses_weighted_cost_not_raw_total():
    budget = TokenBudget(max_tokens=10_000)
    # 9k of cache_read would blow a 10k ceiling at full weight (the old bug); weighted it costs ~900.
    budget.charge(Usage(input_tokens=500, output_tokens=500, cache_read_tokens=9000))
    assert budget.used == int(500 + 500 + 9000 * 0.1)  # 1900, well under the ceiling
    # Raw token counts are still tracked verbatim for reporting.
    assert budget.total_cache_read == 9000


def test_charge_raises_when_weighted_cost_crosses_ceiling():
    budget = TokenBudget(max_tokens=1000)
    with pytest.raises(BudgetExceeded):
        budget.charge(Usage(input_tokens=600, output_tokens=600))  # 1200 > 1000


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
