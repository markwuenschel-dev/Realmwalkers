"""Anthropic client wrapper with usage tracking + transient-error retry (DESIGN §10).

One scene makes many model calls; a single transient blip (rate limit, 5xx, overload, dropped
connection) should not fail the whole job. `complete` retries those with exponential backoff and
re-raises anything non-transient (auth, 400/403/404) immediately. The budget is charged only on a
successful response, so a retried failure never spends tokens.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from math import ceil

import anthropic
import structlog
from anthropic import AsyncAnthropic
from anthropic.types import TextBlockParam

from dominion.shared.config import settings
from dominion.workers import telemetry
from dominion.workers.budget import TokenBudget, Usage

log = structlog.get_logger()


@dataclass(frozen=True)
class CachedPrefixBlock:
    """A named cached user-content block. Order matters: Anthropic cache breakpoints match the
    request prefix through each cache_control block, so callers put the most stable blocks first."""

    name: str
    text: str


class ContextWindowExceeded(Exception):
    """Raised before an LLM call when raw prompt + output allowance exceeds the configured window."""


def estimate_tokens(text: str) -> int:
    """Conservative local estimate for context preflight when a provider tokenizer is unavailable."""
    return ceil(len(text) / 4) if text else 0


def estimate_context_tokens(
    *,
    system: str,
    user: str,
    max_tokens: int,
    user_prefix_blocks: Sequence[CachedPrefixBlock] = (),
    context_sections: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Raw, unweighted context estimate by named prompt section. Cache discounts are intentionally
    ignored: cached tokens still occupy the model context window."""
    if context_sections is None:
        sections = {"system": estimate_tokens(system)}
        sections.update({block.name: estimate_tokens(block.text) for block in user_prefix_blocks})
        sections["user"] = estimate_tokens(user)
    else:
        sections = {str(k): int(v) for k, v in context_sections.items()}
    sections["output_allowance"] = max_tokens
    return sections


def check_context_window(
    *,
    system: str,
    user: str,
    max_tokens: int,
    context_window_budget: int | None,
    user_prefix_blocks: Sequence[CachedPrefixBlock] = (),
    context_sections: Mapping[str, int] | None = None,
) -> dict[str, int]:
    sections = estimate_context_tokens(
        system=system,
        user=user,
        max_tokens=max_tokens,
        user_prefix_blocks=user_prefix_blocks,
        context_sections=context_sections,
    )
    raw_context_tokens = sum(sections.values())
    if context_window_budget is not None and raw_context_tokens > context_window_budget:
        largest = sorted(sections.items(), key=lambda kv: kv[1], reverse=True)[:6]
        detail = ", ".join(f"{name}={tokens}" for name, tokens in largest)
        raise ContextWindowExceeded(
            "ScenePacket context window exceeded: "
            f"raw_context_tokens={raw_context_tokens} "
            f"context_window_budget={context_window_budget} largest_sections: {detail}"
        )
    return sections


# Anthropic's ephemeral cache TTL is 5 minutes. Warn when subsequent calls within a job are close
# enough to the TTL that the cache may have expired, explaining an unexpected cache miss.
_CACHE_TTL_WARN_S = 270  # 4.5 min — 30s of headroom before the 5-min cliff


@lru_cache
def _client() -> AsyncAnthropic:
    """Lazily constructed so importing this module never requires the key."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — add it to .env at the repo root (or export it).")
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


def _is_transient(exc: BaseException) -> bool:
    """Worth retrying: connection/timeout, rate limit (429), 5xx, overloaded (529). NOT 4xx/auth."""
    transient_types: tuple[type[BaseException], ...] = (
        anthropic.APIConnectionError,  # base of APITimeoutError (no HTTP status)
        anthropic.RateLimitError,  # 429
        anthropic.InternalServerError,  # 5xx
    )
    overloaded = getattr(anthropic, "OverloadedError", None)
    if overloaded is not None:
        transient_types = transient_types + (overloaded,)  # 529 when SDK exposes it
    if isinstance(exc, transient_types):
        return True
    # Fallback for any other status error: retry only retryable codes (so 400/401/403/404 do not).
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code == 429 or 500 <= exc.status_code < 600
    return False


async def complete(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    budget: TokenBudget,
    user_prefix: str | None = None,
    user_prefix_blocks: Sequence[CachedPrefixBlock] | None = None,
    expect_cache: bool = True,
    context_window_budget: int | None = None,
    context_sections: Mapping[str, int] | None = None,
) -> tuple[str, Usage]:
    """One LLM call. Retries transient errors with exponential backoff; charges the budget from the
    response usage on success (raises BudgetExceeded if over). Non-transient errors raise at once.

    user_prefix: when given, sent as a cached content block before `user`. Use for stable context
    (canon, summaries, prior-scene tail) that doesn't change across calls within a job, so subsequent
    calls read it from cache rather than re-sending it as uncached input.

    user_prefix_blocks: ordered cached blocks for explicit cache breakpoints. The old `user_prefix`
    parameter maps to one block named "user_prefix" so existing callers keep their behavior.
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

    blocks: tuple[CachedPrefixBlock, ...] = tuple(user_prefix_blocks or ())
    if user_prefix:
        blocks = (CachedPrefixBlock(name="user_prefix", text=user_prefix), *blocks)

    sections = check_context_window(
        system=system,
        user=user,
        max_tokens=max_tokens,
        context_window_budget=context_window_budget,
        user_prefix_blocks=blocks,
        context_sections=context_sections,
    )
    raw_context_total = sum(sections.values())

    # Build the user content: plain string or a block list with one or more cached stable prefixes.
    user_content: str | list[TextBlockParam]
    if blocks:
        user_content = [
            *(TextBlockParam(type="text", text=block.text, cache_control={"type": "ephemeral"}) for block in blocks),
            TextBlockParam(type="text", text=user),
        ]
    else:
        user_content = user

    # Cache the (large, stable) system prefix — _CRAFT + voice + exemplars + dialogue rules. Cheaper +
    # lower time-to-first-token, reused across a POV's scenes within the cache TTL. Below the model's
    # minimum cacheable length the breakpoint is simply ignored.
    system_blocks: list[TextBlockParam] = [
        TextBlockParam(type="text", text=system, cache_control={"type": "ephemeral"})
    ]

    attempt = 0
    call_started = time.time()
    while True:
        try:
            resp = await _client().messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": user_content}],
            )
            break
        except Exception as exc:
            if not _is_transient(exc) or attempt >= settings.llm_max_retries:
                raise
            await asyncio.sleep(settings.llm_retry_base_delay_s * 2**attempt)
            attempt += 1
    latency_ms = int((time.time() - call_started) * 1000)

    # Truncation is silent at the API level: the response just stops mid-output. Surface it so callers
    # that parse JSON (packet author/QA, reviewers) can see *why* their parse failed instead of only a
    # generic "no usable result". The text is still returned — the caller decides whether to fail closed.
    stop_reason = getattr(resp, "stop_reason", None)
    truncated = stop_reason == "max_tokens"
    if truncated:
        log.warning("llm.truncated", model=model, max_tokens=max_tokens, output_tokens=resp.usage.output_tokens)

    # Only a successful response charges; BudgetExceeded propagates (it is not a transient error).
    ru = resp.usage
    usage = Usage(
        input_tokens=ru.input_tokens,
        output_tokens=ru.output_tokens,
        cache_creation_tokens=getattr(ru, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(ru, "cache_read_input_tokens", 0) or 0,
        truncated=truncated,
    )

    # Warn when cache_control was sent but nothing was written or read: the prompt was below
    # Anthropic's minimum cacheable length (~1024 tokens for Sonnet/Opus, 2048 for Haiku).
    # Suppressed when the caller declares the prompt is intentionally short (expect_cache=False).
    if expect_cache and usage.cache_creation_tokens == 0 and usage.cache_read_tokens == 0:
        log.warning(
            "llm.cache_skipped", model=model, note="system prompt below minimum cacheable length; cache_control ignored"
        )

    budget.charge(usage)
    weighted_charged = usage.budget_cost
    total_prompt = usage.input_tokens + usage.cache_creation_tokens + usage.cache_read_tokens
    elapsed_since_first_s = int(time.time() - budget.first_call_at) if budget.first_call_at is not None else 0
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
        system_chars=len(system),
    )
    # Record this call for any active telemetry sink (set by an instrumented orchestrator). No-op
    # otherwise, so uninstrumented callers are unaffected.
    telemetry.record(
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        truncated=truncated,
        latency_ms=latency_ms,
        metadata={
            "max_tokens": max_tokens,
            "stop_reason": stop_reason,
            "context_sections": dict(sections),
            "context_window_budget": context_window_budget,
            "raw_context_total": raw_context_total,
            "weighted_budget_charged": weighted_charged,
        },
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return text, usage
