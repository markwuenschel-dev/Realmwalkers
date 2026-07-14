"""Unit tests for the sectioned ScenePacket author's hardening: per-section fallback floors, the repair
pass for malformed (non-truncated) slices, and per-section telemetry. No network — client is faked."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from dominion.shared.config import settings
from dominion.workers import llm, telemetry
from dominion.workers.budget import TokenBudget
from dominion.workers.scene_packet import author_sections as sp_sections
from dominion.workers.scene_packet.author import ScenePacketAuthorError

_WB = {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400}


def _complete_body() -> dict[str, Any]:
    """A body carrying every key any section owns, so a 'good' section response is complete."""
    return {
        "scene_no": 1,
        "scene_job": "Marcus intercepts",
        "scene_type": "combat",
        "chapter_position": "middle",
        "tone_pressure": "rising dread",
        "required_beats": ["land the hit"],
        "forbidden_beats": [],
        "exit_state": "both wounded",
        "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
        "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
        "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
        "pov_permissions": {"may_notice": [], "may_infer": [], "must_not_know": [], "may_be_wrong_about": []},
        "intentional_mysteries": [],
        "reviewer_false_positive_traps": [],
        "reviewer_instructions": {
            "continuity": [],
            "pacing": [],
            "dialogue": [],
            "combat": [],
            "sensory": [],
            "voice": [],
        },
        "phrases_to_avoid_echoing": [],
    }


def _good_slice(name: str) -> str:
    section = next(s for s in sp_sections._SECTIONS if s.name == name)
    body = _complete_body()
    return json.dumps({k: body[k] for k in section.keys})


class _FakeUsage:
    def __init__(self) -> None:
        self.input_tokens = 40
        self.output_tokens = 200
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class _FakeResp:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.content = [SimpleNamespace(type="text", text=text)]
        self.usage = _FakeUsage()
        self.stop_reason = stop_reason


class _Messages:
    """Routes each create() to a responder by (section_name, model, is_repair) and records the call."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def count_tokens(self, **kwargs: Any) -> Any:
        return SimpleNamespace(input_tokens=50)

    async def create(self, *, model, max_tokens, system, messages):
        blocks = messages[0]["content"]
        trailing = blocks[-1]["text"] if isinstance(blocks, list) else blocks
        section = next((s for s in sp_sections._SECTIONS if f"[section:{s.name}]" in trailing), None)
        name = section.name if section else "?"
        is_repair = "MALFORMED OUTPUT" in trailing
        self.calls.append({"section": name, "model": model, "max_tokens": max_tokens, "repair": is_repair})
        return self._responder(name=name, model=model, max_tokens=max_tokens, is_repair=is_repair)


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _patch(monkeypatch, responder) -> _Messages:
    msgs = _Messages(responder)
    monkeypatch.setattr(llm, "_client", lambda: _Client(msgs))
    # Pin the models these tests run on. `_Client` stands in for the ANTHROPIC client, and llm.py routes
    # by model-id prefix (`gpt-`/`o1-`/`grok-`/`gemini-` go out over the OpenAI-compatible httpx path,
    # everything else through `llm._client`). Inheriting the production default silently decides which
    # transport is exercised: point the default at a non-Anthropic model and this mock is bypassed, the
    # suite makes REAL billed API calls, and the escalation assertions fail because the live provider
    # succeeds where the responder was scripted to fail. The escalation logic under test is provider-
    # agnostic, so pin both rungs here and keep the test hermetic regardless of the default.
    monkeypatch.setattr(settings, "scene_packet_author_model", "claude-haiku-4-5")
    monkeypatch.setattr(settings, "scene_packet_author_fallback_model", "claude-sonnet-5")
    monkeypatch.setattr(settings, "scene_packet_author_sectioned", True)
    monkeypatch.setattr(settings, "scene_packet_context_window_budget", 500_000)
    return msgs


async def _author(**over):
    kwargs: dict[str, Any] = dict(
        pov="Marcus",
        chapter_packet_body={"chapter_job": "hold the line"},
        scene_seed={"seed_id": str(uuid.uuid4()), "scene_no": 1},
        word_budget=_WB,
        budget=TokenBudget(max_tokens=settings.scene_token_budget, hard_max_tokens=settings.scene_token_hard_budget),
    )
    kwargs.update(over)
    return await sp_sections.author_scene_packet_sectioned(**kwargs)


def _primary() -> str:
    return settings.scene_packet_author_model


def _fallback() -> str:
    return settings.scene_packet_author_fallback_model


async def test_truncated_primary_escalates_with_section_specific_floor(monkeypatch):
    def responder(*, name, model, max_tokens, is_repair):
        if name == "reviewer" and model == _primary():
            return _FakeResp('{"reviewer_instructions":', stop_reason="max_tokens")  # truncated mid-object
        return _FakeResp(_good_slice(name))

    msgs = _patch(monkeypatch, responder)
    body = await _author()

    reviewer = next(s for s in sp_sections._SECTIONS if s.name == "reviewer")
    fb_calls = [c for c in msgs.calls if c["section"] == "reviewer" and c["model"] == _fallback() and not c["repair"]]
    assert fb_calls, "reviewer should have escalated to the fallback model"
    # The truncated section gets its own headroom floor (reviewer=7000 > its 2000 cap, and > phrases=4000).
    assert fb_calls[0]["max_tokens"] == max(reviewer.max_tokens, reviewer.fallback_floor) == reviewer.fallback_floor
    assert "reviewer_instructions" in body


async def test_fallback_parse_failure_triggers_repair_that_succeeds(monkeypatch):
    def responder(*, name, model, max_tokens, is_repair):
        if name == "knowledge":
            if model == _primary():
                return _FakeResp("not json at all", stop_reason="end_turn")  # primary parse failure
            if not is_repair:
                return _FakeResp("still not json", stop_reason="end_turn")  # fallback parse failure (not truncated)
            return _FakeResp(_good_slice("knowledge"))  # repair recovers it
        return _FakeResp(_good_slice(name))

    msgs = _patch(monkeypatch, responder)
    body = await _author()

    repair_calls = [c for c in msgs.calls if c["section"] == "knowledge" and c["repair"]]
    assert len(repair_calls) == 1 and repair_calls[0]["model"] == _fallback()
    assert "known_before_scene" in body and body["word_budget"]["target"] == 1500


async def test_repair_only_for_non_truncation(monkeypatch):
    # A truncation is missing content — repair can't invent it, so it must NOT run; the packet fails closed.
    def responder(*, name, model, max_tokens, is_repair):
        if name == "knowledge":
            return _FakeResp('{"known_before_scene":', stop_reason="max_tokens")  # always truncated
        return _FakeResp(_good_slice(name))

    msgs = _patch(monkeypatch, responder)
    with pytest.raises(ScenePacketAuthorError, match="knowledge"):
        await _author()
    assert not any(c["repair"] for c in msgs.calls)  # no repair attempted on a truncation


async def test_repair_failure_raises_with_section_name(monkeypatch):
    def responder(*, name, model, max_tokens, is_repair):
        if name == "knowledge":
            return _FakeResp("garbage", stop_reason="end_turn")  # primary, fallback, AND repair all malformed
        return _FakeResp(_good_slice(name))

    msgs = _patch(monkeypatch, responder)
    with pytest.raises(ScenePacketAuthorError, match="repair failed for section 'knowledge'"):
        await _author()
    assert any(c["repair"] and c["section"] == "knowledge" for c in msgs.calls)


async def test_section_telemetry_records_name_and_attempt_kind(monkeypatch):
    def responder(*, name, model, max_tokens, is_repair):
        return _FakeResp(_good_slice(name))

    _patch(monkeypatch, responder)
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(telemetry.CallContext(sink=sink, stage="scene_packet_author")):
        await _author()
    metas = [r.metadata or {} for r in sink.records]
    assert metas and all(m.get("section_name") for m in metas)
    assert {m.get("section_attempt_kind") for m in metas} == {"primary"}  # all clean → only primary attempts
    assert {m.get("section_name") for m in metas} == {s.name for s in sp_sections._SECTIONS}
