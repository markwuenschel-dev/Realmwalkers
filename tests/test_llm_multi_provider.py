"""Unit tests for the OpenAI-compatible (gpt-*/o1-*/grok-*) dispatch path in llm.complete.

No network, no key — the httpx client is faked. Mirrors test_llm_retry.py's pattern for the existing
Anthropic path.
"""

from __future__ import annotations

import httpx
import pytest

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget


def _ok_response(
    text: str = "hello world",
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    finish_reason: str = "stop",
    cached_tokens: int | None = None,
) -> httpx.Response:
    usage: dict[str, object] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cached_tokens is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}, "finish_reason": finish_reason}],
            "usage": usage,
        },
        request=httpx.Request("POST", "https://example.test/chat/completions"),
    )


class _FakeAsyncClient:
    """Plays back a scripted sequence: raise the Exceptions, return the responses, in order."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls = 0
        self.last_json: dict[str, object] = {}

    async def post(self, path: str, *, json: dict[str, object]) -> object:
        self.calls += 1
        self.last_json = json
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch(monkeypatch, script: list[object]) -> tuple[_FakeAsyncClient, list[float]]:
    fake = _FakeAsyncClient(script)
    monkeypatch.setattr(llm, "_openai_compatible_client", lambda base_url, api_key: fake)
    monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
    monkeypatch.setattr(settings, "xai_api_key", "test-xai-key")
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    return fake, sleeps


# --- dispatch: Anthropic stays the default, only recognized prefixes route elsewhere -------------


def test_anthropic_is_the_default_for_unrecognized_model_strings():
    # Every existing caller/test/model-setting predates multi-provider support and passes a plain model
    # string never intended to select a new provider (tests even use bare placeholders like "m") --
    # anything not an explicit non-Anthropic prefix must stay on the Anthropic path.
    assert llm._is_anthropic_model("m") is True
    assert llm._is_anthropic_model("claude-sonnet-5") is True
    assert llm._is_anthropic_model("claude-haiku-4-5-20251001") is True


def test_recognized_prefixes_route_off_anthropic():
    assert llm._is_anthropic_model("gpt-4o") is False
    assert llm._is_anthropic_model("o1-preview") is False
    assert llm._is_anthropic_model("o3-mini") is False
    assert llm._is_anthropic_model("grok-4") is False


def test_grok_prefix_selects_xai_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", "xai-key")
    monkeypatch.setattr(settings, "xai_base_url", "https://api.x.ai/v1")
    base_url, api_key = llm._openai_compatible_endpoint("grok-4")
    assert base_url == "https://api.x.ai/v1"
    assert api_key == "xai-key"


def test_gpt_prefix_selects_openai_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "oa-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.openai.com/v1")
    base_url, api_key = llm._openai_compatible_endpoint("gpt-4o")
    assert base_url == "https://api.openai.com/v1"
    assert api_key == "oa-key"


def test_missing_xai_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "xai_api_key", None)
    with pytest.raises(RuntimeError, match="XAI_API_KEY"):
        llm._openai_compatible_endpoint("grok-4")


def test_missing_openai_key_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", None)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm._openai_compatible_endpoint("gpt-4o")


# --- OpenAI-compatible complete() round-trip -------------------------------------------------------


async def test_openai_compatible_call_round_trips_text_and_usage(monkeypatch):
    # No prompt_tokens_details in the response at all (e.g. a provider/model that doesn't report
    # caching) -- must default to a clean cache miss, not raise.
    fake, _ = _patch(monkeypatch, [_ok_response("hi there", prompt_tokens=15, completion_tokens=25)])

    budget = TokenBudget(max_tokens=1000)
    text, usage = await llm.complete(model="gpt-4o", system="s", user="u", max_tokens=100, budget=budget)

    assert text == "hi there"
    assert usage.input_tokens == 15
    assert usage.output_tokens == 25
    assert usage.cache_creation_tokens == 0
    assert usage.cache_read_tokens == 0
    assert fake.calls == 1
    assert budget.used == 40  # charged exactly once, only on success


async def test_openai_compatible_sends_system_and_user_as_plain_messages(monkeypatch):
    # No cache_control blocks for this path -- system stays a plain string message, not an
    # Anthropic-style cache_control block list.
    fake, _ = _patch(monkeypatch, [_ok_response()])
    await llm.complete(
        model="grok-4", system="SYSTEM PREFIX", user="u", max_tokens=100, budget=TokenBudget(max_tokens=1000)
    )

    assert fake.last_json["messages"] == [
        {"role": "system", "content": "SYSTEM PREFIX"},
        {"role": "user", "content": "u"},
    ]


# --- prompt caching: real cached-token accounting for both OpenAI and xAI --------------------------


async def test_openai_compatible_cache_hit_splits_cached_tokens_out_of_prompt_tokens(monkeypatch):
    # OpenAI/xAI report prompt_tokens INCLUSIVE of cached tokens (unlike Anthropic, which counts them
    # separately) -- input_tokens must be the UNCACHED remainder, or Usage.total/budget_cost would
    # double-count the cached portion.
    fake, _ = _patch(monkeypatch, [_ok_response(prompt_tokens=1200, cached_tokens=1024)])

    budget = TokenBudget(max_tokens=10_000)
    _text, usage = await llm.complete(model="gpt-4o", system="s", user="u", max_tokens=100, budget=budget)

    assert usage.input_tokens == 1200 - 1024
    assert usage.cache_read_tokens == 1024
    assert usage.cache_creation_tokens == 0  # neither provider reports a distinct cache-write signal
    assert fake.calls == 1


async def test_grok_cache_hit_splits_cached_tokens_out_of_prompt_tokens(monkeypatch):
    # xAI's chat API mirrors OpenAI's usage.prompt_tokens_details.cached_tokens response shape.
    fake, _ = _patch(monkeypatch, [_ok_response(prompt_tokens=2000, cached_tokens=1500)])

    _text, usage = await llm.complete(
        model="grok-4", system="s", user="u", max_tokens=100, budget=TokenBudget(max_tokens=10_000)
    )

    assert usage.input_tokens == 2000 - 1500
    assert usage.cache_read_tokens == 1500
    assert fake.calls == 1


async def test_openai_compatible_cache_miss_reports_zero_cached_tokens(monkeypatch):
    # An explicit prompt_tokens_details.cached_tokens=0 (a real miss, not merely absent from the
    # response) must round-trip as a clean miss, not error.
    fake, _ = _patch(monkeypatch, [_ok_response(prompt_tokens=500, cached_tokens=0)])

    _text, usage = await llm.complete(
        model="gpt-4o", system="s", user="u", max_tokens=100, budget=TokenBudget(max_tokens=10_000)
    )

    assert usage.input_tokens == 500
    assert usage.cache_read_tokens == 0
    assert fake.calls == 1


async def test_openai_compatible_sends_a_stable_prompt_cache_key(monkeypatch):
    # A routing hint recommended by both providers' docs to improve cache-hit rate for repeat calls
    # sharing the same static prefix -- must be present, and stable across calls with the same
    # system/stable-prefix content (so repeat calls route to the same cache-holding backend).
    fake, _ = _patch(monkeypatch, [_ok_response(), _ok_response()])

    await llm.complete(
        model="gpt-4o", system="SAME SYSTEM", user="first ask", max_tokens=100, budget=TokenBudget(max_tokens=1000)
    )
    key1 = fake.last_json["prompt_cache_key"]
    await llm.complete(
        model="gpt-4o", system="SAME SYSTEM", user="different ask", max_tokens=100, budget=TokenBudget(max_tokens=1000)
    )
    key2 = fake.last_json["prompt_cache_key"]

    assert isinstance(key1, str) and key1  # present and non-empty
    assert key1 == key2  # same stable prefix (system) -> same key, regardless of the dynamic `user` tail


async def test_openai_compatible_prompt_cache_key_changes_with_system(monkeypatch):
    fake, _ = _patch(monkeypatch, [_ok_response(), _ok_response()])

    await llm.complete(model="gpt-4o", system="SYSTEM A", user="u", max_tokens=100, budget=TokenBudget(max_tokens=1000))
    key_a = fake.last_json["prompt_cache_key"]
    await llm.complete(model="gpt-4o", system="SYSTEM B", user="u", max_tokens=100, budget=TokenBudget(max_tokens=1000))
    key_b = fake.last_json["prompt_cache_key"]

    assert key_a != key_b


async def test_openai_compatible_truncation_detected_via_finish_reason(monkeypatch):
    fake, _ = _patch(monkeypatch, [_ok_response("cut off", finish_reason="length")])
    _text, usage = await llm.complete(
        model="gpt-4o", system="s", user="u", max_tokens=100, budget=TokenBudget(max_tokens=1000)
    )
    assert usage.truncated is True


async def test_openai_compatible_retries_transient_then_succeeds(monkeypatch):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    transient = httpx.ConnectError("connection refused", request=req)
    fake, sleeps = _patch(monkeypatch, [transient, transient, _ok_response()])

    budget = TokenBudget(max_tokens=1000)
    text, _usage = await llm.complete(model="gpt-4o", system="s", user="u", max_tokens=100, budget=budget)

    assert text == "hello world"
    assert fake.calls == 3  # 2 failures + 1 success
    assert sleeps == [1.0, 2.0]


async def test_openai_compatible_non_transient_raises_immediately_without_retry(monkeypatch):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    bad_resp = httpx.Response(400, request=req)

    async def raising_post(self, path, *, json):
        raise httpx.HTTPStatusError("bad request", request=req, response=bad_resp)

    monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
    fake = _FakeAsyncClient([])
    monkeypatch.setattr(_FakeAsyncClient, "post", raising_post)
    monkeypatch.setattr(llm, "_openai_compatible_client", lambda base_url, api_key: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)

    budget = TokenBudget(max_tokens=1000)
    with pytest.raises(httpx.HTTPStatusError):
        await llm.complete(model="gpt-4o", system="s", user="u", max_tokens=100, budget=budget)

    assert sleeps == []
    assert budget.used == 0  # nothing charged on failure


async def test_openai_compatible_rate_limit_is_retried(monkeypatch):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    rate_limited = httpx.Response(429, request=req)

    async def rate_then_ok_post(self, path, *, json):
        self.calls += 1
        if self.calls == 1:
            raise httpx.HTTPStatusError("slow down", request=req, response=rate_limited)
        return _ok_response()

    fake = _FakeAsyncClient([])
    monkeypatch.setattr(_FakeAsyncClient, "post", rate_then_ok_post)
    monkeypatch.setattr(llm, "_openai_compatible_client", lambda base_url, api_key: fake)
    monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)

    text, _usage = await llm.complete(
        model="gpt-4o", system="s", user="u", max_tokens=100, budget=TokenBudget(max_tokens=1000)
    )
    assert text == "hello world"
    assert fake.calls == 2
    assert len(sleeps) == 1


# --- context-window preflight: no counting endpoint for this path, always estimates ----------------


async def test_openai_compatible_context_preflight_uses_estimate_only(monkeypatch):
    fake, _ = _patch(monkeypatch, [_ok_response()])
    seen_metadata: dict[str, object] = {}

    def fake_record(**kwargs):
        seen_metadata.update(kwargs.get("metadata", {}))

    monkeypatch.setattr(llm.telemetry, "record", fake_record)

    await llm.complete(
        model="gpt-4o",
        system="s",
        user="u",
        max_tokens=100,
        budget=TokenBudget(max_tokens=1000),
        context_window_budget=100_000,
    )
    assert seen_metadata["token_count_method"] == "estimate_only"


async def test_openai_compatible_context_preflight_still_raises_when_exceeded(monkeypatch):
    _patch(monkeypatch, [_ok_response()])
    with pytest.raises(llm.ContextWindowExceeded):
        await llm.complete(
            model="gpt-4o",
            system="s" * 10_000,
            user="u",
            max_tokens=100,
            budget=TokenBudget(max_tokens=1000),
            context_window_budget=10,
        )
