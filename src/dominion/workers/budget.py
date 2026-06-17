"""Hard per-job token budget (DESIGN §10). Wall-clock budget is enforced in worker.py via wait_for."""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(Exception):
    """Raised when a job's cumulative token usage crosses its ceiling. Fail-closed."""


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenBudget:
    max_tokens: int
    used: int = 0

    def charge(self, usage: Usage) -> None:
        self.used += usage.total
        if self.used > self.max_tokens:
            raise BudgetExceeded(f"token budget exceeded: {self.used} > {self.max_tokens}")
