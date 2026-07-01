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
from typing import Any

import anthropic
import httpx
import structlog
from anthropic import AsyncAnthropic
from anthropic.types import TextBlockParam

from dominion.shared.config import settings
from dominion.workers import telemetry
from dominion.workers.budget import BudgetExceeded, TokenBudget, Usage

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


# Only a RECOGNIZED non-Anthropic prefix routes off the Anthropic path — Anthropic is the default/
# fallback, not an allowlist. Every existing caller, test, and model setting predates multi-provider
# support and passes a plain model string never intended to select a new provider (tests even use bare
# placeholders like "m"), so anything NOT explicitly one of these prefixes must keep behaving exactly as
# it did before this feature existed.
_OPENAI_COMPATIBLE_PREFIXES: tuple[str, ...] = ("gpt-", "o1-", "o3-", "o4-", "grok-")


def _is_anthropic_model(model: str) -> bool:
    return not any(model.startswith(prefix) for prefix in _OPENAI_COMPATIBLE_PREFIXES)


def _openai_compatible_endpoint(model: str) -> tuple[str, str]:
    """(base_url, api_key) for a non-Anthropic model. xAI's chat API is OpenAI-request-shape compatible
    (same /chat/completions body/response), reached by swapping base_url + key — routed by the model-id
    prefix (`grok-*`), not a separate code path. No new SDK dependency: a plain httpx POST, matching the
    embedding provider's existing convention (workers.memory.embedding)."""
    if model.startswith("grok-"):
        if not settings.xai_api_key:
            raise RuntimeError("XAI_API_KEY is not set — add it to .env at the repo root (or export it).")
        return settings.xai_base_url, settings.xai_api_key
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set — add it to .env at the repo root (or export it).")
    return settings.openai_base_url, settings.openai_api_key


@lru_cache
def _openai_compatible_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    """Lazily constructed + cached per (base_url, api_key) pair so the small number of distinct
    provider/key combinations reuse one connection pool instead of opening a new client per call."""
    return httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"})


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


def _is_transient_http(exc: BaseException) -> bool:
    """The httpx equivalent of `_is_transient`, for the OpenAI-compatible path: connection/timeout
    errors, and a 429/5xx HTTPStatusError. NOT 4xx/auth."""
    if isinstance(exc, httpx.TransportError):  # connect/read/write/timeout — no HTTP status yet
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
    return False


async def _call_with_retries(make_coro: Any, *, what: str, is_transient: Any = _is_transient) -> Any:
    """Await `make_coro()`, retrying transient errors with the same exponential backoff used for
    message creation. Non-transient errors (and exhausted retries) propagate. Shared by both
    messages.create and messages.count_tokens (and the OpenAI-compatible path, via `is_transient`
    override) so a transient blip never one-offs any of them."""
    attempt = 0
    while True:
        try:
            return await make_coro()
        except Exception as exc:
            if not is_transient(exc) or attempt >= settings.llm_max_retries:
                raise
            log.warning("llm.retry", what=what, attempt=attempt + 1, error=type(exc).__name__)
            await asyncio.sleep(settings.llm_retry_base_delay_s * 2**attempt)
            attempt += 1


async def count_input_tokens(
    *,
    model: str,
    system: list[TextBlockParam],
    messages: list[dict[str, Any]],
) -> int:
    """The exact input-token count for a request, from Anthropic's `messages.count_tokens` endpoint.

    Counted against the SAME `model`/`system`/`messages` the matching `messages.create` will send, since
    tokenization differs by model and the count is the authoritative context-window gate (the local
    `estimate_tokens` heuristic is kept only for section attribution). Generation-only params
    (`max_tokens`, `temperature`) are intentionally NOT sent — the endpoint counts input only. Transient
    errors retry; anything else propagates to the caller's fail-closed / fallback policy."""

    # Spread a dict[str, Any] (as the create call does) so the SDK's strict MessageParam/TextBlockParam
    # typing doesn't reject our plain dict payload — the shape is the same one create() will send.
    count_kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages}

    async def _make() -> Any:
        return await _client().messages.count_tokens(**count_kwargs)

    resp = await _call_with_retries(_make, what="count_tokens")
    return int(resp.input_tokens)


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
    temperature: float | None = None,
) -> tuple[str, Usage]:
    """One LLM call. Retries transient errors with exponential backoff; charges the budget from the
    response usage on success (raises BudgetExceeded if over). Non-transient errors raise at once.

    user_prefix: when given, sent as a cached content block before `user`. Use for stable context
    (canon, summaries, prior-scene tail) that doesn't change across calls within a job, so subsequent
    calls read it from cache rather than re-sending it as uncached input.

    user_prefix_blocks: ordered cached blocks for explicit cache breakpoints. The old `user_prefix`
    parameter maps to one block named "user_prefix" so existing callers keep their behavior.
    """
    is_anthropic = _is_anthropic_model(model)

    # Warn when the cache may have expired: if a prior call in this job wrote to cache more than
    # ~4.5 minutes ago, the next call is likely cold — surfacing this explains a cache_ratio drop.
    # Anthropic-only: the other provider path never caches, so there's nothing to warn about expiring.
    now = time.time()
    if is_anthropic and budget.first_call_at is not None and now - budget.first_call_at > _CACHE_TTL_WARN_S:
        log.warning(
            "llm.cache_ttl_risk",
            elapsed_s=int(now - budget.first_call_at),
            threshold_s=_CACHE_TTL_WARN_S,
        )

    blocks: tuple[CachedPrefixBlock, ...] = tuple(user_prefix_blocks or ())
    if user_prefix:
        blocks = (CachedPrefixBlock(name="user_prefix", text=user_prefix), *blocks)

    # Local per-section estimate: kept ONLY for attribution/reporting (and the disabled/fallback gate),
    # never the authoritative context-window gate when real counting succeeds.
    sections = estimate_context_tokens(
        system=system,
        user=user,
        max_tokens=max_tokens,
        user_prefix_blocks=blocks,
        context_sections=context_sections,
    )
    raw_context_total = sum(sections.values())

    # ---- Context-window preflight: count the exact request BEFORE creating it, so an oversized prompt
    # fails cleanly here instead of erroring mid-generation. The real count (input only) plus the output
    # allowance is the gate; cache discounts are work/cost accounting and do NOT shrink the window.
    # Anthropic-specific request shapes (system_blocks/messages/create_kwargs) are built here too, since
    # count_input_tokens needs the exact same system/messages shape the create call will send.
    system_blocks: list[TextBlockParam] = []
    create_kwargs: dict[str, Any] = {}
    if is_anthropic:
        # Build the user content: plain string or a block list with one or more cached stable prefixes.
        user_content: str | list[TextBlockParam]
        if blocks:
            user_content = [
                *(
                    TextBlockParam(type="text", text=block.text, cache_control={"type": "ephemeral"})
                    for block in blocks
                ),
                TextBlockParam(type="text", text=user),
            ]
        else:
            user_content = user

        # Cache the (large, stable) system prefix — _CRAFT + voice + exemplars + dialogue rules. Cheaper +
        # lower time-to-first-token, reused across a POV's scenes within the cache TTL. Below the model's
        # minimum cacheable length the breakpoint is simply ignored.
        system_blocks = [TextBlockParam(type="text", text=system, cache_control={"type": "ephemeral"})]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

        # The token-count payload and the create payload share one model/system/messages shape (per
        # Anthropic's guidance: count against the exact request the model will see); create_kwargs only
        # adds the generation-only params. Building both from one source keeps the preflight count honest.
        create_kwargs = {"model": model, "system": system_blocks, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            create_kwargs["temperature"] = temperature
    else:
        # OpenAI-compatible chat completions shape: no cache blocks (neither provider exposes an
        # equivalent primitive worth emulating) — the stable prefix content is just folded into the
        # single user message ahead of the scene-specific `user` text.
        oa_user = "\n\n".join([*(b.text for b in blocks), user]) if blocks else user
        oa_messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": oa_user},
        ]
        create_kwargs = {"model": model, "messages": oa_messages, "max_tokens": max_tokens}
        if temperature is not None:
            create_kwargs["temperature"] = temperature

    preflight_input_tokens: int | None = None
    preflight_total: int | None = None
    token_count_method = "disabled"
    token_count_error: str | None = None
    if context_window_budget is not None:
        local_input_estimate = max(0, raw_context_total - max_tokens)  # sections includes output_allowance
        if is_anthropic and settings.llm_token_counting_enabled:
            try:
                preflight_input_tokens = await count_input_tokens(
                    model=model, system=system_blocks, messages=create_kwargs["messages"]
                )
                token_count_method = "anthropic"
            except Exception as exc:  # noqa: BLE001 — policy below decides fail-closed vs. estimate fallback
                token_count_error = f"{type(exc).__name__}: {exc}"
                if settings.llm_token_counting_fail_closed:
                    raise ContextWindowExceeded(
                        "context window preflight unavailable (fail-closed): "
                        f"model={model} token_count_error={token_count_error} "
                        f"output_allowance={max_tokens} context_window_budget={context_window_budget}"
                    ) from exc
                # Explicit, telemetry-recorded fallback — never a silent downgrade to the old heuristic.
                preflight_input_tokens = int(
                    local_input_estimate * settings.llm_token_counting_estimate_fallback_multiplier
                )
                token_count_method = "local_estimate"
                log.warning("llm.token_count_fallback", model=model, error=token_count_error)
        else:
            # No equivalent counting endpoint for the OpenAI-compatible path (or counting is disabled) —
            # always estimate. Distinct from "local_estimate" (an Anthropic-counting failure fallback) so
            # telemetry can tell "never available" apart from "available but failed this time".
            preflight_input_tokens = local_input_estimate
            if not is_anthropic:
                token_count_method = "estimate_only"

        preflight_total = preflight_input_tokens + max_tokens
        if preflight_total > context_window_budget:
            largest = sorted(sections.items(), key=lambda kv: kv[1], reverse=True)[:6]
            detail = ", ".join(f"{name}={tokens}" for name, tokens in largest)
            raise ContextWindowExceeded(
                "context window preflight exceeded: "
                f"model={model} preflight_input_tokens={preflight_input_tokens} "
                f"output_allowance={max_tokens} preflight_total={preflight_total} "
                f"context_window_budget={context_window_budget} token_count_method={token_count_method} "
                f"largest_sections: {detail}"
            )

    call_started = time.time()
    if is_anthropic:
        resp = await _call_with_retries(lambda: _client().messages.create(**create_kwargs), what="create")

        # Truncation is silent at the API level: the response just stops mid-output. Surface it so
        # callers that parse JSON (packet author/QA, reviewers) can see *why* their parse failed instead
        # of only a generic "no usable result". The text is still returned — the caller decides whether
        # to fail closed.
        stop_reason = getattr(resp, "stop_reason", None)
        truncated = stop_reason == "max_tokens"
        ru = resp.usage
        usage = Usage(
            input_tokens=ru.input_tokens,
            output_tokens=ru.output_tokens,
            cache_creation_tokens=getattr(ru, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(ru, "cache_read_input_tokens", 0) or 0,
            truncated=truncated,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
    else:
        base_url, api_key = _openai_compatible_endpoint(model)
        client = _openai_compatible_client(base_url, api_key)

        async def _make() -> httpx.Response:
            r = await client.post("/chat/completions", json=create_kwargs)
            r.raise_for_status()
            return r

        http_resp = await _call_with_retries(_make, what="create", is_transient=_is_transient_http)
        body = http_resp.json()
        choice = body["choices"][0]
        stop_reason = choice.get("finish_reason")
        truncated = stop_reason == "length"
        ru = body.get("usage") or {}
        usage = Usage(
            input_tokens=int(ru.get("prompt_tokens") or 0),
            output_tokens=int(ru.get("completion_tokens") or 0),
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=truncated,
        )
        text = choice["message"]["content"] or ""
    latency_ms = int((time.time() - call_started) * 1000)

    if truncated:
        log.warning("llm.truncated", model=model, max_tokens=max_tokens, output_tokens=usage.output_tokens)

    # Warn when cache_control was sent but nothing was written or read: the prompt was below
    # Anthropic's minimum cacheable length (~1024 tokens for Sonnet/Opus, 2048 for Haiku).
    # Suppressed when the caller declares the prompt is intentionally short (expect_cache=False), and
    # entirely for the OpenAI-compatible path, which never caches by design (not a "skip" worth flagging).
    if is_anthropic and expect_cache and usage.cache_creation_tokens == 0 and usage.cache_read_tokens == 0:
        log.warning(
            "llm.cache_skipped", model=model, note="system prompt below minimum cacheable length; cache_control ignored"
        )

    # Charge WITHOUT raising on a hard overage yet: a valid response body must not be lost before its
    # cost/outcome is recorded. A soft-only overage never raises; a hard overage raises AFTER telemetry.
    charge_result = budget.charge(usage, raise_on_hard_exceeded=False)
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
        token_count_method=token_count_method,
        preflight_total=preflight_total,
        budget_soft_exceeded=charge_result.soft_exceeded,
        budget_hard_exceeded=charge_result.hard_exceeded,
    )
    # Record this call for any active telemetry sink (set by an instrumented orchestrator). No-op
    # otherwise, so uninstrumented callers are unaffected. Recorded even on a hard-budget overage so
    # the failure stays observable instead of vanishing with the raised exception.
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
            # `raw_context_total` kept for existing readers; `_estimate` is the clearer alias (this is the
            # local heuristic sum, NOT the authoritative count — that is `preflight_input_tokens`).
            "raw_context_total": raw_context_total,
            "raw_context_total_estimate": raw_context_total,
            "weighted_budget_charged": weighted_charged,
            # Preflight (real or fallback) token accounting.
            "preflight_input_tokens": preflight_input_tokens,
            "preflight_output_allowance": max_tokens,
            "preflight_total": preflight_total,
            "token_count_method": token_count_method,
            "token_count_error": token_count_error,
            # Work-budget state after charging this call.
            "budget_soft_exceeded": charge_result.soft_exceeded,
            "budget_hard_exceeded": charge_result.hard_exceeded,
            "budget_used_after_charge": charge_result.used,
            "budget_soft_limit": budget.max_tokens,
            "budget_hard_limit": budget.hard_limit,
        },
    )
    if charge_result.hard_exceeded:
        raise BudgetExceeded(f"token budget exceeded: {charge_result.used} > {budget.hard_limit}")
    return text, usage
