"""Unit tests for llm.complete's real Anthropic token-counting preflight (no network — client faked).

The context-window gate is the count from messages.count_tokens (input only) plus the output allowance,
counted BEFORE messages.create against the exact same model/system/messages. The local ceil(len/4)
estimate is kept only for attribution and as the explicit, telemetry-recorded fallback.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from dominion.shared.config import settings
from dominion.workers import llm, telemetry
from dominion.workers.budget import TokenBudget
from dominion.workers.llm import ContextWindowExceeded

_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _ok_response(text: str = "{}", *, in_tok: int = 10, out_tok: int = 20) -> SimpleNamespace:
    return SimpleNamespace(
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
    )


class _FakeMessages:
    """Records the order of count_tokens vs. create calls and the kwargs each received. `count_script`
    plays back exceptions / int counts in order; otherwise count_tokens returns `count_value`."""

    def __init__(self, *, count_script: list[object] | None = None, count_value: int = 100) -> None:
        self.events: list[str] = []
        self.count_kwargs: dict[str, object] = {}
        self.create_kwargs: dict[str, object] = {}
        self._count_script = list(count_script or [])
        self._count_value = count_value

    async def count_tokens(self, **kwargs: object) -> object:
        self.events.append("count")
        self.count_kwargs = kwargs
        if self._count_script:
            item = self._count_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(input_tokens=item)
        return SimpleNamespace(input_tokens=self._count_value)

    async def create(self, **kwargs: object) -> object:
        self.events.append("create")
        self.create_kwargs = kwargs
        return _ok_response()


class _FakeClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _patch(monkeypatch, messages: _FakeMessages) -> list[float]:
    monkeypatch.setattr(llm, "_client", lambda: _FakeClient(messages))
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    return sleeps


async def _complete(**over):
    kwargs: dict[str, object] = dict(
        model="m",
        system="s",
        user="u",
        max_tokens=50,
        budget=TokenBudget(max_tokens=10_000),
        context_window_budget=200_000,
    )
    kwargs.update(over)
    return await llm.complete(**kwargs)


async def test_counts_tokens_before_create(monkeypatch):
    msgs = _FakeMessages(count_value=100)
    _patch(monkeypatch, msgs)
    await _complete()
    assert msgs.events == ["count", "create"]  # preflight first, then generation


async def test_create_skipped_when_preflight_exceeds_window(monkeypatch):
    msgs = _FakeMessages(count_value=500)
    _patch(monkeypatch, msgs)
    with pytest.raises(ContextWindowExceeded, match="context window preflight exceeded"):
        await _complete(max_tokens=600, context_window_budget=1000)  # 500 + 600 = 1100 > 1000
    assert msgs.events == ["count"]  # create never reached


async def test_count_payload_matches_create_payload(monkeypatch):
    msgs = _FakeMessages(count_value=100)
    _patch(monkeypatch, msgs)
    await _complete(model="mymodel", system="SYSTEM")
    assert msgs.count_kwargs["model"] == msgs.create_kwargs["model"] == "mymodel"
    assert msgs.count_kwargs["system"] == msgs.create_kwargs["system"]
    assert msgs.count_kwargs["messages"] == msgs.create_kwargs["messages"]


async def test_temperature_excluded_from_count_but_sent_to_create(monkeypatch):
    msgs = _FakeMessages(count_value=100)
    _patch(monkeypatch, msgs)
    await _complete(temperature=0.7)
    assert "temperature" not in msgs.count_kwargs
    assert msgs.create_kwargs["temperature"] == 0.7


async def test_transient_count_error_retries_then_succeeds(monkeypatch):
    transient = anthropic.APITimeoutError(request=_REQ)
    msgs = _FakeMessages(count_script=[transient, transient, 100])
    sleeps = _patch(monkeypatch, msgs)
    await _complete()
    assert msgs.events.count("count") == 3  # 2 failures + 1 success
    assert msgs.events[-1] == "create"
    assert sleeps == [1.0, 2.0]  # base * 2**attempt, same backoff as create


async def test_permanent_count_error_fails_closed_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_token_counting_fail_closed", True)
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=_REQ), body=None)
    msgs = _FakeMessages(count_script=[bad])
    _patch(monkeypatch, msgs)
    with pytest.raises(ContextWindowExceeded, match="preflight unavailable"):
        await _complete()
    assert "create" not in msgs.events  # never generated on a fail-closed count error


async def test_permanent_count_error_falls_back_to_estimate_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_token_counting_fail_closed", False)
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=_REQ), body=None)
    msgs = _FakeMessages(count_script=[bad])
    _patch(monkeypatch, msgs)
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(telemetry.CallContext(sink=sink, stage="test")):
        await _complete()
    assert "create" in msgs.events  # proceeds after the recorded fallback
    md = sink.records[0].metadata or {}
    assert md["token_count_method"] == "local_estimate"
    assert md["token_count_error"] and "BadRequest" in md["token_count_error"]


async def test_telemetry_records_preflight_fields(monkeypatch):
    msgs = _FakeMessages(count_value=1234)
    _patch(monkeypatch, msgs)
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(telemetry.CallContext(sink=sink, stage="test")):
        await _complete(max_tokens=50)
    md = sink.records[0].metadata or {}
    assert md["preflight_input_tokens"] == 1234
    assert md["preflight_output_allowance"] == 50
    assert md["preflight_total"] == 1234 + 50
    assert md["token_count_method"] == "anthropic"


async def test_no_count_when_no_context_window_budget(monkeypatch):
    msgs = _FakeMessages()
    _patch(monkeypatch, msgs)
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(telemetry.CallContext(sink=sink, stage="test")):
        await _complete(context_window_budget=None)
    assert msgs.events == ["create"]  # nothing to gate → no count_tokens call
    md = sink.records[0].metadata or {}
    assert md["token_count_method"] == "disabled"
    assert md["preflight_total"] is None


async def test_counting_disabled_flag_skips_count_and_gates_on_estimate(monkeypatch):
    monkeypatch.setattr(settings, "llm_token_counting_enabled", False)
    msgs = _FakeMessages()
    _patch(monkeypatch, msgs)
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(telemetry.CallContext(sink=sink, stage="test")):
        await _complete()
    assert msgs.events == ["create"]  # count_tokens skipped when the feature flag is off
    md = sink.records[0].metadata or {}
    assert md["token_count_method"] == "disabled"
    assert md["preflight_total"] is not None  # estimate still gated the window
