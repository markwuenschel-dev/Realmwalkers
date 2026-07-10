"""Unit tests for the LLM layer's 429 handling: Retry-After parsing, jittered backoff,
LlmRateLimited classification, the per-provider throttle, and the local prompt-budget gate.
No network — provider errors are hand-built httpx objects."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget


def _resp_429(headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, headers=headers or {}, request=request, text="rate limited")
    return httpx.HTTPStatusError("429 from provider: TPM exceeded", request=request, response=response)


def test_retry_after_seconds_prefers_retry_after_header():
    exc = _resp_429({"retry-after": "7"})
    assert llm._retry_after_seconds(exc) == 7.0


def test_retry_after_seconds_parses_openai_reset_durations():
    assert llm._retry_after_seconds(_resp_429({"x-ratelimit-reset-tokens": "1.234s"})) == pytest.approx(1.234)
    assert llm._retry_after_seconds(_resp_429({"x-ratelimit-reset-tokens": "12ms"})) == pytest.approx(0.012)
    assert llm._retry_after_seconds(_resp_429({"x-ratelimit-reset-tokens": "2m"})) == pytest.approx(120.0)
    assert llm._retry_after_seconds(_resp_429({})) is None


def test_rate_limit_headers_kept_for_telemetry():
    exc = _resp_429({"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "6s", "x-other": "1"})
    headers = llm.rate_limit_headers(exc.response)
    assert headers == {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "6s"}


async def test_persistent_429_raises_llm_rate_limited(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    monkeypatch.setattr(settings, "llm_retry_base_delay_s", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay_s", 0.0)

    calls = 0

    async def _always_429() -> Any:
        nonlocal calls
        calls += 1
        raise _resp_429({"retry-after": "3"})

    stats: dict[str, Any] = {}
    with pytest.raises(llm.LlmRateLimited) as exc_info:
        await llm._call_with_retries(_always_429, what="create", is_transient=llm._is_transient_http, stats=stats)
    err = exc_info.value
    assert calls == 3  # initial call + 2 retries
    assert err.attempts == 3
    assert err.retry_after_s == 3.0
    assert isinstance(err.__cause__, httpx.HTTPStatusError)
    assert stats["retries"] == 2
    assert stats["rate_limit_headers"] == {"retry-after": "3"}


async def test_backoff_honors_retry_after_hint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay_s", 0.001)
    monkeypatch.setattr(settings, "llm_retry_max_delay_s", 30.0)

    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)

    attempts = 0

    async def _429_then_ok() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _resp_429({"retry-after": "5"})
        return "ok"

    result = await llm._call_with_retries(_429_then_ok, what="create", is_transient=llm._is_transient_http)
    assert result == "ok"
    assert len(sleeps) == 1 and sleeps[0] >= 5.0  # provider hint floors the backoff


async def test_non_rate_limit_error_propagates_unwrapped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_max_retries", 0)

    async def _auth_error() -> Any:
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(401, request=request, text="bad key")
        raise httpx.HTTPStatusError("401", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await llm._call_with_retries(_auth_error, what="create", is_transient=llm._is_transient_http)


async def test_prompt_budget_exceeded_fails_locally_before_any_provider_call():
    # 4000+ chars ≈ 1000+ estimated tokens against a 10-token budget: must raise BEFORE touching the
    # provider (no API key is configured in tests — reaching the endpoint would RuntimeError instead).
    with pytest.raises(llm.PromptBudgetExceeded, match="prompt_budget_exceeded"):
        await llm.complete(
            model="gpt-5.6-terra",
            system="You are a test.",
            user="x" * 4000,
            max_tokens=64,
            budget=TokenBudget(max_tokens=10_000),
            input_budget=10,
        )


def test_provider_slot_caps_openai_compatible_path(monkeypatch: pytest.MonkeyPatch):
    # The OpenAI-compatible path gets a real semaphore sized from settings; 0 disables the cap.
    async def _check() -> None:
        monkeypatch.setattr(settings, "llm_openai_concurrency", 1)
        slot = llm._provider_slot("gpt-5.6-terra")
        assert hasattr(slot, "acquire")  # a real asyncio.Semaphore, not nullcontext
        monkeypatch.setattr(settings, "llm_anthropic_concurrency", 0)
        anthropic_slot = llm._provider_slot("claude-haiku-4-5")
        assert not hasattr(anthropic_slot, "acquire")  # uncapped → nullcontext

    import asyncio

    asyncio.run(_check())
