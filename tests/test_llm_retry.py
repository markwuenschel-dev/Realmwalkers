"""Unit tests for llm.complete transient-error retry (no network, no key — client is faked)."""
from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _ok_response(text: str = "hello world", *, in_tok: int = 10, out_tok: int = 20) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        content=[SimpleNamespace(type="text", text=text)],
    )


class _FakeMessages:
    """Plays back a scripted sequence: raise the Exceptions, return the responses, in order."""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.calls = 0

    async def create(self, **_kwargs: object) -> object:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, script: list[object]) -> None:
        self.messages = _FakeMessages(script)


def _patch(monkeypatch, script: list[object]) -> tuple[_FakeClient, list[float]]:
    fake = _FakeClient(script)
    monkeypatch.setattr(llm, "_client", lambda: fake)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    return fake, sleeps


async def test_retries_transient_then_succeeds(monkeypatch):
    transient = anthropic.APITimeoutError(request=_REQ)
    fake, sleeps = _patch(monkeypatch, [transient, transient, _ok_response()])

    budget = TokenBudget(max_tokens=1000)
    text, usage = await llm.complete(model="m", system="s", user="u", max_tokens=100, budget=budget)

    assert text == "hello world"
    assert fake.messages.calls == 3            # 2 failures + 1 success
    assert sleeps == [1.0, 2.0]                 # base * 2**attempt, with the defaults
    assert budget.used == 30                    # charged exactly once, only on success
    assert usage.total == 30


async def test_non_transient_raises_immediately_without_retry(monkeypatch):
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=_REQ), body=None)
    fake, sleeps = _patch(monkeypatch, [bad, _ok_response()])

    budget = TokenBudget(max_tokens=1000)
    with pytest.raises(anthropic.BadRequestError):
        await llm.complete(model="m", system="s", user="u", max_tokens=100, budget=budget)

    assert fake.messages.calls == 1            # no retry on a 400
    assert sleeps == []
    assert budget.used == 0                     # nothing charged on failure


async def test_gives_up_and_reraises_after_max_retries(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 3)
    fake, sleeps = _patch(monkeypatch, [anthropic.APITimeoutError(request=_REQ)] * 10)

    budget = TokenBudget(max_tokens=1000)
    with pytest.raises(anthropic.APITimeoutError):
        await llm.complete(model="m", system="s", user="u", max_tokens=100, budget=budget)

    assert fake.messages.calls == 4            # initial attempt + 3 retries
    assert sleeps == [1.0, 2.0, 4.0]           # one backoff per retry, exponential


async def test_rate_limit_and_server_errors_are_retried(monkeypatch):
    rate = anthropic.RateLimitError("slow", response=httpx.Response(429, request=_REQ), body=None)
    server = anthropic.InternalServerError("boom", response=httpx.Response(503, request=_REQ), body=None)
    fake, sleeps = _patch(monkeypatch, [rate, server, _ok_response()])

    budget = TokenBudget(max_tokens=1000)
    text, _usage = await llm.complete(model="m", system="s", user="u", max_tokens=100, budget=budget)

    assert text == "hello world"
    assert fake.messages.calls == 3
    assert len(sleeps) == 2
