"""Anthropic client wrapper with usage tracking + transient-error retry (DESIGN §10).

One scene makes many model calls; a single transient blip (rate limit, 5xx, overload, dropped
connection) should not fail the whole job. `complete` retries those with exponential backoff and
re-raises anything non-transient (auth, 400/403/404) immediately. The budget is charged only on a
successful response, so a retried failure never spends tokens.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

import anthropic
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


def _is_transient(exc: BaseException) -> bool:
    """Worth retrying: connection/timeout, rate limit (429), 5xx, overloaded (529). NOT 4xx/auth."""
    if isinstance(
        exc,
        (
            anthropic.APIConnectionError,   # base of APITimeoutError (no HTTP status)
            anthropic.RateLimitError,       # 429
            anthropic.InternalServerError,  # 5xx
            anthropic.OverloadedError,      # 529
        ),
    ):
        return True
    # Fallback for any other status error: retry only retryable codes (so 400/401/403/404 do not).
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or 500 <= exc.status_code < 600
    return False


async def complete(
    *, model: str, system: str, user: str, max_tokens: int, budget: TokenBudget
) -> tuple[str, Usage]:
    """One LLM call. Retries transient errors with exponential backoff; charges the budget from the
    response usage on success (raises BudgetExceeded if over). Non-transient errors raise at once."""
    attempt = 0
    while True:
        try:
            resp = await _client().messages.create(
                model=model,
                max_tokens=max_tokens,
                # Cache the (large, stable) system prefix — _CRAFT + voice + exemplars + dialogue rules.
                # Cheaper + lower time-to-first-token, and reused across a POV's scenes within the cache
                # TTL. Below the model's minimum cacheable length the breakpoint is simply ignored.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
            break
        except Exception as exc:
            if not _is_transient(exc) or attempt >= settings.llm_max_retries:
                raise
            await asyncio.sleep(settings.llm_retry_base_delay_s * 2**attempt)
            attempt += 1

    # Only a successful response charges; BudgetExceeded propagates (it is not a transient error).
    usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
    budget.charge(usage)
    text = "".join(block.text for block in resp.content if block.type == "text")
    return text, usage
