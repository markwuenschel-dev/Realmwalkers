"""Anthropic client wrapper with usage tracking, wired to the per-job TokenBudget (DESIGN §10)."""
from __future__ import annotations

from functools import lru_cache

from anthropic import AsyncAnthropic

from dominion.shared.config import settings
from dominion.workers.budget import TokenBudget, Usage


@lru_cache
def _client() -> AsyncAnthropic:
    """Lazily constructed so importing this module never requires the key."""
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — add it to .env at the repo root (or export it)."
        )
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


async def complete(
    *, model: str, system: str, user: str, max_tokens: int, budget: TokenBudget
) -> tuple[str, Usage]:
    """One LLM call. Charges the budget from response usage (raises BudgetExceeded if over)."""
    resp = await _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
    budget.charge(usage)
    text = "".join(block.text for block in resp.content if block.type == "text")
    return text, usage
