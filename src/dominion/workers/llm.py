"""Anthropic client wrapper with usage tracking + transient-error retry (DESIGN §10).

One scene makes many model calls; a single transient blip (rate limit, 5xx, overload, dropped
connection) should not fail the whole job. `complete` retries those with exponential backoff (full
jitter, floored at the provider's Retry-After hint) and re-raises anything non-transient (auth,
400/403/404) immediately. A 429 that survives every retry raises `LlmRateLimited` so orchestrators
classify it as transient infrastructure, never an author/QA failure. Per-provider semaphores bound
in-flight calls (OpenAI-compatible defaults to 1 — gpt-mini TPM windows are small enough that a
concurrent swarm self-inflicts 429s). Failed calls are telemetry-recorded with retry counts and
rate-limit headers; `input_budget` fails an oversized prompt locally (`PromptBudgetExceeded`)
before any provider traffic. The budget is charged only on a successful response, so a retried
failure never spends tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import random
import re
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

from dominion.shared.agent_registry import supports_effort, supports_temperature
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


class PromptBudgetExceeded(Exception):
    """Raised locally, BEFORE any provider call, when the estimated prompt input exceeds the caller's
    hard `input_budget`. Message starts with "prompt_budget_exceeded" so the failure is classifiable
    downstream. Distinct from ContextWindowExceeded: that guards the model's context window; this
    guards a per-stage cost/TPM policy ceiling."""


class LlmRateLimited(Exception):
    """A provider 429 that survived every automatic retry. The request was valid — the provider
    refused it for rate limiting, so this is transient infrastructure, never an author/QA quality
    failure. Callers classify on this type instead of string-matching provider messages."""

    def __init__(self, message: str, *, retry_after_s: float | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.attempts = attempts


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
        raise RuntimeError("ANTHROPIC_API_KEY is not set — add it to the deploy environment (Railway → Variables).")
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


# Only a RECOGNIZED non-Anthropic prefix routes off the Anthropic path — Anthropic is the default/
# fallback, not an allowlist. Every existing caller, test, and model setting predates multi-provider
# support and passes a plain model string never intended to select a new provider (tests even use bare
# placeholders like "m"), so anything NOT explicitly one of these prefixes must keep behaving exactly as
# it did before this feature existed.
_OPENAI_COMPATIBLE_PREFIXES: tuple[str, ...] = ("gpt-", "o1-", "o3-", "o4-", "grok-", "gemini-")


def _is_anthropic_model(model: str) -> bool:
    return not any(model.startswith(prefix) for prefix in _OPENAI_COMPATIBLE_PREFIXES)


# OpenAI's reasoning models (o-series + the gpt-5 family) require `max_completion_tokens` and reject a
# non-default `temperature` with a 400; older gpt-4* and xAI's Grok take the classic `max_tokens` + a
# free temperature. Grok is intentionally excluded — it keeps the classic shape in the request builder.
_OPENAI_REASONING_PREFIXES: tuple[str, ...] = ("o1-", "o3-", "o4-", "gpt-5")


def _is_openai_reasoning_model(model: str) -> bool:
    return any(model.startswith(prefix) for prefix in _OPENAI_REASONING_PREFIXES)


def _openai_compatible_endpoint(model: str) -> tuple[str, str]:
    """(base_url, api_key) for a non-Anthropic model. xAI and Gemini both expose OpenAI-compatible
    chat-completions endpoints, reached by swapping base_url + key — routed by the model-id prefix
    (`grok-*`, `gemini-*`), not a separate SDK path. No new dependency: a plain httpx POST, matching the
    embedding provider's existing convention (workers.memory.embedding)."""
    # .strip() the key: a value pasted into Railway with a trailing newline/space would otherwise be
    # sent as `Bearer <key>\n` (or a lone space slips past a truthiness check as `Bearer `), which the
    # provider rejects with a confusing 400 "missing bearer authentication" instead of a clear error.
    if model.startswith("grok-"):
        key = (settings.xai_api_key or "").strip()
        if not key:
            raise RuntimeError("XAI_API_KEY is not set — add it to the deploy environment (Railway → Variables).")
        return settings.xai_base_url, key
    if model.startswith("gemini-"):
        key = (settings.google_api_key or "").strip()
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY / GOOGLE_API_KEY is not set — add it to the deploy environment (Railway → Variables)."
            )
        return settings.google_base_url, key
    key = (settings.openai_api_key or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set — add it to the deploy environment (Railway → Variables).")
    return settings.openai_base_url, key


@lru_cache
def _openai_compatible_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    """Lazily constructed + cached per (base_url, api_key) pair so the small number of distinct
    provider/key combinations reuse one connection pool instead of opening a new client per call."""
    # httpx defaults to a 5s timeout on ALL operations — far too short for a chat completion (reasoning
    # models routinely take 30s-2min), so an otherwise-valid generation dies as a ReadTimeout. Give a
    # generous read/write budget (matching the Anthropic SDK's ~10-min default); the per-call work
    # budgets (e.g. packet_time_budget_s) still bound wall-clock below this.
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(600.0, connect=15.0),
    )


def _prompt_cache_key(system: str, blocks: tuple[CachedPrefixBlock, ...]) -> str:
    """Routing hint for OpenAI/xAI's cache: both providers cache automatically by exact-prefix match,
    but recommend a stable `prompt_cache_key` so repeat requests sharing the same static prefix get
    routed to the same cache-holding backend instance (their docs: 'Prompt Caching Best Practices' /
    xAI 'Maximizing Cache Hits'). Hash only the STABLE prefix (system + the caller's stable prefix
    blocks) — never the per-call `user` tail — so calls that actually share a cacheable prefix get the
    same key, and calls that don't, don't."""
    stable = system + "".join(b.text for b in blocks)
    return hashlib.sha256(stable.encode()).hexdigest()[:32]


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


def _is_rate_limit(exc: BaseException) -> bool:
    """True for a provider 429 on either path (Anthropic SDK or httpx OpenAI-compatible)."""
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code == 429:
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _response_of(exc: BaseException) -> httpx.Response | None:
    """The underlying httpx.Response when the exception carries one (both the Anthropic SDK and the
    httpx path attach it), so retry hints and rate-limit headers survive classification."""
    resp = getattr(exc, "response", None)
    return resp if isinstance(resp, httpx.Response) else None


# OpenAI reset headers use duration strings like "12ms" / "1.234s" / "6m0s" — parse the leading unit.
_RESET_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)?")

# Rate-limit response headers worth persisting in telemetry (OpenAI x-ratelimit-* and Anthropic
# anthropic-ratelimit-*), so a 429 postmortem can see remaining/reset without re-reproducing it.
_RATE_LIMIT_HEADER_KEYS: tuple[str, ...] = (
    "retry-after",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-tokens-remaining",
    "anthropic-ratelimit-tokens-reset",
)


def rate_limit_headers(resp: httpx.Response | None) -> dict[str, str]:
    if resp is None:
        return {}
    return {k: v for k in _RATE_LIMIT_HEADER_KEYS if (v := resp.headers.get(k)) is not None}


def _retry_after_seconds(exc: BaseException) -> float | None:
    """The provider's own wait hint, in seconds: Retry-After when sent, else OpenAI's
    x-ratelimit-reset-tokens duration. None when the exception carries neither."""
    resp = _response_of(exc)
    if resp is None:
        return None
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except ValueError:
            pass  # HTTP-date form — not sent by these providers; fall through to reset headers
    reset = resp.headers.get("x-ratelimit-reset-tokens") or resp.headers.get("x-ratelimit-reset-requests")
    if reset and (m := _RESET_DURATION_RE.match(reset.strip())):
        value, unit = float(m.group(1)), m.group(2) or "s"
        return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value * 3600 if unit == "h" else value
    return None


async def _call_with_retries(
    make_coro: Any, *, what: str, is_transient: Any = _is_transient, stats: dict[str, Any] | None = None
) -> Any:
    """Await `make_coro()`, retrying transient errors with exponential backoff + full jitter, floored
    at the provider's Retry-After hint and capped at llm_retry_max_delay_s. Non-transient errors (and
    exhausted retries) propagate — except a 429, which raises LlmRateLimited so callers classify it as
    infrastructure instead of an author/QA failure. Shared by both messages.create and
    messages.count_tokens (and the OpenAI-compatible path, via `is_transient` override) so a transient
    blip never one-offs any of them. `stats` (when given) receives retry diagnostics for telemetry:
    retries, last_error, rate_limit_headers."""
    attempt = 0
    while True:
        try:
            result = await make_coro()
            if stats is not None:
                stats["retries"] = attempt
            return result
        except Exception as exc:
            rate_limited = _is_rate_limit(exc)
            if stats is not None:
                stats["retries"] = attempt
                stats["last_error"] = f"{type(exc).__name__}: {exc}"
                if headers := rate_limit_headers(_response_of(exc)):
                    stats["rate_limit_headers"] = headers
            if not is_transient(exc) or attempt >= settings.llm_max_retries:
                if rate_limited:
                    raise LlmRateLimited(
                        f"provider rate limit (429) persisted after {attempt} automatic retr"
                        f"{'y' if attempt == 1 else 'ies'}: {exc}",
                        retry_after_s=_retry_after_seconds(exc),
                        attempts=attempt + 1,
                    ) from exc
                raise
            delay = min(settings.llm_retry_max_delay_s, settings.llm_retry_base_delay_s * 2**attempt)
            delay *= 0.5 + random.random()  # full jitter: throttled peers must not re-fire in lockstep
            if (hint := _retry_after_seconds(exc)) is not None:
                delay = min(max(delay, hint), settings.llm_retry_max_delay_s)
            log.warning(
                "llm.retry",
                what=what,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                rate_limited=rate_limited,
                error=type(exc).__name__,
            )
            await asyncio.sleep(delay)
            attempt += 1


# One semaphore per (event loop, provider path), sized from settings on first use in that loop —
# mirrors author_sections._inflight_sem. Held across the WHOLE retry loop on purpose: when the
# provider is telling us to slow down, freeing the slot mid-backoff would just let the next call
# burn the same TPM window and 429 too.
_provider_sems: dict[tuple[int, str], asyncio.Semaphore] = {}


def _provider_slot(model: str) -> Any:
    """Async context manager bounding in-flight calls to this model's provider path (0 = uncapped)."""
    is_a = _is_anthropic_model(model)
    limit = settings.llm_anthropic_concurrency if is_a else settings.llm_openai_concurrency
    if limit <= 0:
        return contextlib.nullcontext()
    key = (id(asyncio.get_running_loop()), "anthropic" if is_a else "openai_compatible")
    sem = _provider_sems.get(key)
    if sem is None:
        sem = asyncio.Semaphore(limit)
        _provider_sems[key] = sem
    return sem


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
    effort: str | None = None,
    input_budget: int | None = None,
) -> tuple[str, Usage]:
    """One LLM call. Retries transient errors with exponential backoff; charges the budget from the
    response usage on success (raises BudgetExceeded if over). Non-transient errors raise at once.
    A 429 that survives every retry raises LlmRateLimited (classified infrastructure failure).

    input_budget: hard per-stage ceiling on ESTIMATED input tokens. Exceeding it raises
    PromptBudgetExceeded locally, before any provider traffic — a policy gate on prompt size,
    tighter than (and independent of) the model-context-window preflight.

    user_prefix: when given, sent as a cached content block before `user`. Use for stable context
    (canon, summaries, prior-scene tail) that doesn't change across calls within a job, so subsequent
    calls read it from cache rather than re-sending it as uncached input.

    user_prefix_blocks: ordered cached blocks for explicit cache breakpoints. The old `user_prefix`
    parameter maps to one block named "user_prefix" so existing callers keep their behavior.
    """
    is_anthropic = _is_anthropic_model(model)

    # Warn when the cache may have expired: if a prior call in this job wrote to cache more than
    # ~4.5 minutes ago, the next call is likely cold — surfacing this explains a cache_ratio drop.
    # Anthropic-only: we control its cache breakpoints explicitly (cache_control blocks) so a stale
    # write is actionable to flag ahead of time. OpenAI/xAI cache fully automatically with their own
    # opaque (and longer-lived: 5-10 min up to 1hr, longer on some models) TTL we don't control or
    # write to explicitly, so there's no anticipatory warning to raise here for those two paths — a
    # miss just shows up after the fact as cache_read_tokens=0.
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
    estimated_input_tokens = max(0, raw_context_total - max_tokens)  # sections includes output_allowance

    # ---- Prompt-budget gate: fail locally before ANY provider traffic (including token counting).
    # An oversized prompt should cost zero TPM — it gets a clear, classifiable local failure instead
    # of burning the rate-limit window just to be refused (or to overpay) mid-generation.
    if input_budget is not None and estimated_input_tokens > input_budget:
        largest = sorted(sections.items(), key=lambda kv: kv[1], reverse=True)[:6]
        detail = ", ".join(f"{name}={tokens}" for name, tokens in largest)
        raise PromptBudgetExceeded(
            "prompt_budget_exceeded: "
            f"estimated_input_tokens={estimated_input_tokens} > input_budget={input_budget} "
            f"model={model}; largest sections: {detail}"
        )

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
        # Sampling vs. effort split: Anthropic models that accept `temperature` (Haiku, 4.6-era) take it;
        # the flagship models (Opus 4.7+, Sonnet 5, Fable 5) 400 on temperature and take
        # output_config.effort (low/medium/high, mapped from quality_level) instead. Exactly one is sent
        # per Anthropic model; the OpenAI/xAI branch below always uses `temperature`.
        if temperature is not None and supports_temperature(model):
            create_kwargs["temperature"] = temperature
        elif effort is not None and supports_effort(model):
            create_kwargs["output_config"] = {"effort": effort}
    else:
        # OpenAI-compatible chat completions shape: no explicit cache_control blocks — both OpenAI and
        # xAI cache automatically by exact-prefix match instead, so the request only needs the stable
        # content (system, then the caller's stable prefix blocks) to consistently come BEFORE the
        # per-call dynamic `user` text, which it already does here. `prompt_cache_key` is a routing
        # hint, not a cache directive: it doesn't create the cache, just improves the hit rate.
        oa_user = "\n\n".join([*(b.text for b in blocks), user]) if blocks else user
        oa_messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": oa_user},
        ]
        create_kwargs = {
            "model": model,
            "messages": oa_messages,
            "prompt_cache_key": _prompt_cache_key(system, blocks),
        }
        # Output-length param diverges by provider: OpenAI's current models (gpt-5 / o-series) REQUIRE
        # `max_completion_tokens` and 400 on the old `max_tokens`; xAI's Grok and Gemini's OpenAI-
        # compatible endpoint keep the classic `max_tokens` parameter.
        if model.startswith(("grok-", "gemini-")):
            create_kwargs["max_tokens"] = max_tokens
        else:
            create_kwargs["max_completion_tokens"] = max_tokens
        # OpenAI's reasoning models only accept the default temperature (they 400 on any explicit value),
        # so omit it for them; other OpenAI-compatible models take it freely.
        if temperature is not None and not _is_openai_reasoning_model(model):
            create_kwargs["temperature"] = temperature
        if effort is not None and model.startswith("gemini-"):
            create_kwargs["reasoning_effort"] = effort

    preflight_input_tokens: int | None = None
    preflight_total: int | None = None
    token_count_method = "disabled"
    token_count_error: str | None = None
    if context_window_budget is not None:
        local_input_estimate = estimated_input_tokens
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
    retry_stats: dict[str, Any] = {}
    provider_headers: dict[str, str] = {}
    resp: Any = None
    http_resp: httpx.Response | None = None
    try:
        if is_anthropic:
            async with _provider_slot(model):
                resp = await _call_with_retries(
                    lambda: _client().messages.create(**create_kwargs), what="create", stats=retry_stats
                )
        else:
            base_url, api_key = _openai_compatible_endpoint(model)
            client = _openai_compatible_client(base_url, api_key)

            async def _make() -> httpx.Response:
                r = await client.post("/chat/completions", json=create_kwargs)
                if r.is_error:
                    # Surface the provider's error body (e.g. OpenAI's "missing bearer authentication",
                    # "model not found", "max_tokens is not supported") — raise_for_status alone drops it,
                    # leaving only a bare status code that hides the actionable reason.
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} from {r.request.url}: {r.text[:600]}",
                        request=r.request,
                        response=r,
                    )
                return r

            async with _provider_slot(model):
                http_resp = await _call_with_retries(
                    _make, what="create", is_transient=_is_transient_http, stats=retry_stats
                )
            provider_headers = rate_limit_headers(http_resp)
    except Exception as exc:
        # A failed call must still be observable: without this record, a retry-exhausted 429 (or any
        # terminal provider error) vanished from llm_calls entirely and the run's telemetry read as if
        # the call never happened. Zero token measures — the provider did no billable work for us.
        telemetry.record(
            model=model,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=int((time.time() - call_started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
            metadata={
                "max_tokens": max_tokens,
                "estimated_input_tokens": estimated_input_tokens,
                "requested_tokens": estimated_input_tokens + max_tokens,
                "retries": retry_stats.get("retries", 0),
                "rate_limited": isinstance(exc, LlmRateLimited),
                "retry_after_s": getattr(exc, "retry_after_s", None),
                "rate_limit_headers": retry_stats.get("rate_limit_headers"),
                "context_sections": dict(sections),
            },
        )
        raise

    if is_anthropic:
        assert resp is not None  # set in the try block above for the Anthropic branch
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
        assert http_resp is not None  # set in the try block above for the OpenAI-compatible branch
        body = http_resp.json()
        choice = body["choices"][0]
        stop_reason = choice.get("finish_reason")
        truncated = stop_reason == "length"
        ru = body.get("usage") or {}
        # Both OpenAI and xAI report cached tokens at usage.prompt_tokens_details.cached_tokens (xAI's
        # chat API mirrors OpenAI's response shape here) — and, unlike Anthropic, `prompt_tokens`
        # already INCLUDES the cached tokens rather than counting them separately, so cache_read_tokens
        # must be split back OUT of it here or Usage.total/budget_cost would double-count them.
        # Neither provider bills or reports a distinct "cache write" — a first-touch miss looks
        # identical to plain uncached input (cached_tokens=0) — so cache_creation_tokens stays 0 by
        # design for this path, not as a gap.
        prompt_tokens = int(ru.get("prompt_tokens") or 0)
        cached_tokens = int((ru.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        usage = Usage(
            input_tokens=max(0, prompt_tokens - cached_tokens),
            output_tokens=int(ru.get("completion_tokens") or 0),
            cache_creation_tokens=0,
            cache_read_tokens=cached_tokens,
            truncated=truncated,
        )
        text = choice["message"]["content"] or ""
    latency_ms = int((time.time() - call_started) * 1000)

    if truncated:
        log.warning("llm.truncated", model=model, max_tokens=max_tokens, output_tokens=usage.output_tokens)

    # Warn when cache_control was sent but nothing was written or read: the prompt was below
    # Anthropic's minimum cacheable length (~1024 tokens for Sonnet/Opus, 2048 for Haiku).
    # Suppressed when the caller declares the prompt is intentionally short (expect_cache=False).
    # Anthropic-only: unlike Anthropic's explicit cache_control blocks, the OpenAI-compatible path has
    # no "creation" signal to distinguish a legitimate first-touch miss (cache not warmed yet — expected
    # on every job's first call) from a genuinely-too-short prompt, so a per-call warning here would
    # mostly just fire on normal first touches. The aggregate cache_ratio/hit-rate in telemetry is the
    # right place to notice a persistently-cold OpenAI/xAI cache instead.
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
            # Per-call token accounting requested vs. actual: the estimate is what we ASKED the
            # provider to admit (input estimate + output allowance); the Usage fields above are what
            # it actually metered. `retries` + rate-limit headers make a 429-adjacent call diagnosable
            # from telemetry alone.
            "estimated_input_tokens": estimated_input_tokens,
            "requested_tokens": estimated_input_tokens + max_tokens,
            "input_budget": input_budget,
            "retries": retry_stats.get("retries", 0),
            "rate_limit_headers": provider_headers or None,
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
