"""Hard per-job token budget (DESIGN §10). Wall-clock budget is enforced in worker.py via wait_for."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a job's cumulative token usage crosses its HARD ceiling. Fail-closed."""


# The budget is a *work* ceiling, not a dollar ceiling. A cache WRITE is content the model processes
# for the first time (the store is incidental), so it counts as real work at full weight, like plain
# input — and so warming the cache never penalizes the scene that pays the write (e.g. the priming
# scene). A cache READ is re-sent context the model already processed, so it is near-free: charged at
# ~0.1x so a large shared prefix doesn't re-count in full on every call (the derive-blocking bug).
_CACHE_WRITE_WEIGHT = 1.0
_CACHE_READ_WEIGHT = 0.1


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    # True when the model stopped because it hit max_tokens (the JSON/prose was cut off). Surfaced so
    # callers that parse structured output can tell a truncation apart from a genuinely malformed body.
    truncated: bool = False

    @property
    def total(self) -> int:
        """All prompt tokens processed + output; the raw token count (rate-limit tracking, reporting)."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens + self.output_tokens

    @property
    def budget_cost(self) -> int:
        """Weighted cost charged against a job's token ceiling — NOT the raw token count.

        The budget bounds the *work* a job is allowed to do. Fresh input, output, and a first-time
        cache write all count at full weight (a write is just input the model also stores). A cache
        READ is re-sent prefix the model already processed, so it is near-free (~0.1x). Charging cache
        reads at full weight (the old `total`) made a large shared prefix re-count in full on every
        call, so a scene whose real new work was ~10k tokens could blow a 40k ceiling purely on re-read
        cache — and caching could never relieve the budget. Now warming the cache only ever buys
        headroom, and never penalizes the scene that pays the write."""
        return int(
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens * _CACHE_WRITE_WEIGHT
            + self.cache_read_tokens * _CACHE_READ_WEIGHT
        )

    @property
    def cache_savings_tokens(self) -> int:
        """Net tokens saved: reads recoup write cost (cache_creation written once; each read saves ~full cost).
        Negative when a cache breakpoint was written but not yet read back on this call."""
        return self.cache_read_tokens - self.cache_creation_tokens


@dataclass(frozen=True)
class BudgetChargeResult:
    """The outcome of one `TokenBudget.charge`, so a caller can record/branch on budget state without
    re-deriving it. `soft_exceeded` means the soft target was crossed (warn); `hard_exceeded` means the
    hard ceiling was crossed (block). When `hard_max_tokens` is unset the two coincide."""

    used: int
    soft_exceeded: bool
    hard_exceeded: bool


@dataclass
class TokenBudget:
    """A job's cumulative work ceiling. `max_tokens` is the SOFT target; `hard_max_tokens` (defaulting
    to `max_tokens` for backward compatibility) is the HARD ceiling that actually blocks.

    The split exists because a scene that lands a few tokens over the soft target (`60043 > 60000`)
    already produced valid output — raising there just discards it and re-runs identical work. So a soft
    overage only WARNS (surfaced via telemetry); only a hard overage fails closed. A caller that wants
    the old single-ceiling behavior passes only `max_tokens` and gets `hard == soft`."""

    max_tokens: int
    hard_max_tokens: int | None = None
    used: int = 0
    total_input: int = field(default=0)
    total_output: int = field(default=0)
    total_cache_creation: int = field(default=0)
    total_cache_read: int = field(default=0)
    first_call_at: float | None = field(default=None)

    @property
    def hard_limit(self) -> int:
        """The effective hard ceiling: `hard_max_tokens` when set, else the soft `max_tokens`."""
        return self.hard_max_tokens if self.hard_max_tokens is not None else self.max_tokens

    @property
    def soft_exceeded(self) -> bool:
        return self.used > self.max_tokens

    @property
    def hard_exceeded(self) -> bool:
        return self.used > self.hard_limit

    @property
    def remaining_soft(self) -> int:
        """Work left under the soft target (0 once exceeded)."""
        return max(0, self.max_tokens - self.used)

    @property
    def remaining_hard(self) -> int:
        """Work left under the hard ceiling (0 once exceeded)."""
        return max(0, self.hard_limit - self.used)

    def charge(self, usage: Usage, *, raise_on_hard_exceeded: bool = True) -> BudgetChargeResult:
        """Charge one call's weighted cost. Raises `BudgetExceeded` when the HARD ceiling is crossed and
        `raise_on_hard_exceeded` is True (the default — backward compatible). Pass False to charge without
        raising (e.g. so the caller can record telemetry before deciding to fail), reading the returned
        `BudgetChargeResult` for soft/hard state. A soft-only overage never raises."""
        if self.first_call_at is None:
            self.first_call_at = time.time()
        self.used += usage.budget_cost
        self.total_input += usage.input_tokens
        self.total_output += usage.output_tokens
        self.total_cache_creation += usage.cache_creation_tokens
        self.total_cache_read += usage.cache_read_tokens
        result = BudgetChargeResult(used=self.used, soft_exceeded=self.soft_exceeded, hard_exceeded=self.hard_exceeded)
        if result.hard_exceeded and raise_on_hard_exceeded:
            raise BudgetExceeded(f"token budget exceeded: {self.used} > {self.hard_limit}")
        return result

    @property
    def cache_hit_ratio(self) -> float:
        """Fraction of total prompt tokens served from cache across all calls so far.
        Prompt tokens = plain input + cache writes + cache reads (excludes output)."""
        total_prompt = self.total_input + self.total_cache_creation + self.total_cache_read
        return self.total_cache_read / total_prompt if total_prompt else 0.0

    @property
    def cache_tokens_saved(self) -> int:
        """Token-equivalent savings from cache reads: each cached token costs 10% vs 100% uncached,
        so 90% of cache_read tokens were 'saved' from full billing. Useful proxy without hardcoding model prices."""
        return int(self.total_cache_read * 0.9)
