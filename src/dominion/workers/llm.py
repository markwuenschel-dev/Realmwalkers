"""Anthropic client wrapper with usage tracking + transient-error retry (DESIGN §10).

One scene makes many model calls; a single transient blip (rate limit, 5xx, overload, dropped
connection) should not fail the whole job. `complete` retries those with exponential backoff and
re-raises anything non-transient (auth, 400/403/404) immediately. The budget is charged only on a
successful response, so a retried failure never spends tokens.
"""
from __future__ import annotations

import asyncio
import time
from functools import lru_cache

import anthropic
import structlog
from anthropic import AsyncAnthropic

from dominion.shared.config import settings
from dominion.workers.budget import TokenBudget, Usage

log = structlog.get_logger()

# Anthropic's ephemeral cache TTL is 5 minutes. Warn when subsequent calls within a job are close
# enough to the TTL that the cache may have expired, explaining an unexpected cache miss.
_CACHE_TTL_WARN_S = 270  # 4.5 min — 30s of headroom before the 5-min cliff


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
    *, model: str, system: str, user: str, max_tokens: int, budget: TokenBudget,
    user_prefix: str | None = None,
) -> tuple[str, Usage]:
    """One LLM call. Retries transient errors with exponential backoff; charges the budget from the
    response usage on success (raises BudgetExceeded if over). Non-transient errors raise at once.

    user_prefix: when given, sent as a cached content block before `user`. Use for stable context
    (canon, summaries, prior-scene tail) that doesn't change across calls within a job, so subsequent
    calls read it from cache rather than re-sending it as uncached input.
    """
    # Warn when the cache may have expired: if a prior call in this job wrote to cache more than
    # ~4.5 minutes ago, the next call is likely cold — surfacing this explains a cache_ratio drop.
    now = time.time()
    if budget.first_call_at is not None and now - budget.first_call_at > _CACHE_TTL_WARN_S:
        log.warning(
            "llm.cache_ttl_risk",
            elapsed_s=int(now - budget.first_call_at),
            threshold_s=_CACHE_TTL_WARN_S,
        )

    # Build the user content: plain string or a two-block list with a cached stable prefix.
    if user_prefix:
        user_content: str | list[dict] = [
            {"type": "text", "text": user_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user},
        ]
    else:
        user_content = user

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
                messages=[{"role": "user", "content": user_content}],
            )
            break
        except Exception as exc:
            if not _is_transient(exc) or attempt >= settings.llm_max_retries:
                raise
            await asyncio.sleep(settings.llm_retry_base_delay_s * 2**attempt)
            attempt += 1

    # Truncation is silent at the API level: the response just stops mid-output. Surface it so callers
    # that parse JSON (packet author/QA, reviewers) can see *why* their parse failed instead of only a
    # generic "no usable result". The text is still returned — the caller decides whether to fail closed.
    if getattr(resp, "stop_reason", None) == "max_tokens":
        log.warning("llm.truncated", model=model, max_tokens=max_tokens,
                    output_tokens=resp.usage.output_tokens)

    # Only a successful response charges; BudgetExceeded propagates (it is not a transient error).
    ru = resp.usage
    usage = Usage(
        input_tokens=ru.input_tokens,
        output_tokens=ru.output_tokens,
        cache_creation_tokens=getattr(ru, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(ru, "cache_read_input_tokens", 0) or 0,
    )

    # Warn when cache_control was sent but nothing was written or read: the prompt was below
    # Anthropic's minimum cacheable length (~1024 tokens for Sonnet/Opus, 2048 for Haiku).
    if usage.cache_creation_tokens == 0 and usage.cache_read_tokens == 0:
        log.warning("llm.cache_skipped", model=model,
                    note="system prompt below minimum cacheable length; cache_control ignored")

    budget.charge(usage)
    total_prompt = usage.input_tokens + usage.cache_creation_tokens + usage.cache_read_tokens
    elapsed_since_first_s = (
        int(time.time() - budget.first_call_at)
        if budget.first_call_at is not None else 0
    )
    log.info(
        "llm.complete",
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_savings_tokens=usage.cache_savings_tokens,
        cache_hit=usage.cache_read_tokens > 0,
        cache_ratio=round(usage.cache_read_tokens / total_prompt, 3) if total_prompt else 0.0,
        elapsed_since_first_s=elapsed_since_first_s,
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return text, usage
