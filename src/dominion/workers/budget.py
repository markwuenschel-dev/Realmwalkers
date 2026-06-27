"""Hard per-job token budget (DESIGN §10). Wall-clock budget is enforced in worker.py via wait_for."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a job's cumulative token usage crosses its ceiling. Fail-closed."""


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
        """All prompt tokens processed + output; used for rate-limit tracking and budget charging."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens + self.output_tokens

    @property
    def cache_savings_tokens(self) -> int:
        """Net tokens saved: reads recoup write cost (cache_creation written once; each read saves ~full cost).
        Negative when a cache breakpoint was written but not yet read back on this call."""
        return self.cache_read_tokens - self.cache_creation_tokens


@dataclass
class TokenBudget:
    max_tokens: int
    used: int = 0
    total_input: int = field(default=0)
    total_output: int = field(default=0)
    total_cache_creation: int = field(default=0)
    total_cache_read: int = field(default=0)
    first_call_at: float | None = field(default=None)

    def charge(self, usage: Usage) -> None:
        if self.first_call_at is None:
            self.first_call_at = time.time()
        self.used += usage.total
        self.total_input += usage.input_tokens
        self.total_output += usage.output_tokens
        self.total_cache_creation += usage.cache_creation_tokens
        self.total_cache_read += usage.cache_read_tokens
        if self.used > self.max_tokens:
            raise BudgetExceeded(f"token budget exceeded: {self.used} > {self.max_tokens}")

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
