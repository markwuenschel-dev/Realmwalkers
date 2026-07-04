"""Desk Control Round — agents wiring (Phase 3). Deterministic: llm.complete is always mocked.

Covers:
- review lanes + summaries send the review_model quality knobs (temperature AND effort; no suffix);
- complete_with_rate_limit_fallback: 429 -> one retry on the configured fallback, honoring
  never_fallback and absent-fallback (re-raise), and never retrying non-429 errors;
- the monolithic scene-packet author escalates through attempt_with_escalation on an unparseable
  primary and still fails loud (ScenePacketAuthorError) when the fallback also fails;
- the Agent Ops honesty flags (AgentControlsOut) are pinned and populated per agent row;
- presets no longer carry the dead review_model semantic_escalation hint.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from dominion.shared import agent_ops
from dominion.shared.agent_policy import load_runtime_policies, quality_effort, quality_temperature
from dominion.shared.agent_registry import AGENTS, PRESET_BY_ID, PRESETS
from dominion.shared.config import settings
from dominion.shared.schemas import AgentControlsOut, AgentOpsAgentOut
from dominion.workers import llm, llm_escalation
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.llm import LlmRateLimited
from dominion.workers.memory import summaries
from dominion.workers.reviewers.pacing import _SYSTEM as PACING_SYSTEM
from dominion.workers.reviewers.pacing import pacing_reviewer
from dominion.workers.scene_packet import author as sp_author
from dominion.workers.scene_packet.author import ScenePacketAuthorError


@pytest.fixture(autouse=True)
def _default_policies() -> None:
    """Pin registry-default runtime policies (balanced quality, registry never_fallback tiers)."""
    load_runtime_policies({})


def _ctx(**overrides: object) -> SceneContext:
    base: dict[str, object] = dict(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov="Marcus",
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        beat_text="Marcus tests his eyes.",
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
    )
    base.update(overrides)
    return SceneContext(**base)  # type: ignore[arg-type]


def _capture(monkeypatch: pytest.MonkeyPatch, response: str = "[]") -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        calls.append(kwargs)
        return response, Usage(5, 5)

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


# --- (a) review quality is live -------------------------------------------------------------------


async def test_review_lane_sends_temperature_and_effort(monkeypatch: pytest.MonkeyPatch):
    calls = _capture(monkeypatch)
    flags = await pacing_reviewer.review("word " * 400, _ctx())
    assert flags == []
    (kwargs,) = calls
    assert kwargs["model"] == settings.review_model
    assert kwargs["temperature"] == quality_temperature("review_model") == 0.7  # balanced default
    assert kwargs["effort"] == quality_effort("review_model") == "medium"
    assert kwargs["system"] == PACING_SYSTEM  # deliberately NO quality prompt_suffix on reviewers


async def test_summary_fold_sends_temperature_and_effort(monkeypatch: pytest.MonkeyPatch):
    calls = _capture(monkeypatch, response="Marcus won.")
    out = await summaries._summarize(None, "Marcus won the duel.", "the whole story so far")
    assert out == "Marcus won."
    (kwargs,) = calls
    assert kwargs["model"] == settings.review_model
    assert kwargs["temperature"] == quality_temperature("review_model")
    assert kwargs["effort"] == quality_effort("review_model")


# --- (b) rate-limit-only fallback helper -----------------------------------------------------------


def _usage() -> Usage:
    return Usage(input_tokens=5, output_tokens=5)


async def test_rate_limit_fallback_retries_once_with_fallback_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: "fallback-model")
    models: list[str] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        models.append(kwargs["model"])
        if kwargs["model"] == "primary-model":
            raise LlmRateLimited("provider rate limit (429) persisted", attempts=4)
        return "ok from fallback", _usage()

    monkeypatch.setattr(llm, "complete", fake_complete)
    text, _u = await llm_escalation.complete_with_rate_limit_fallback(
        setting_key="review_model",
        model="primary-model",
        system="s",
        user="u",
        max_tokens=10,
        budget=TokenBudget(max_tokens=1_000),
    )
    assert text == "ok from fallback"
    assert models == ["primary-model", "fallback-model"]  # exactly ONE retry, on the fallback


async def test_rate_limit_fallback_honors_never_fallback_tiers(monkeypatch: pytest.MonkeyPatch):
    # review_model's registry default never_fallback is ("haiku",) — a haiku fallback must not run.
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: "claude-haiku-4-5")
    models: list[str] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        models.append(kwargs["model"])
        raise LlmRateLimited("provider rate limit (429) persisted", attempts=4)

    monkeypatch.setattr(llm, "complete", fake_complete)
    with pytest.raises(LlmRateLimited):
        await llm_escalation.complete_with_rate_limit_fallback(
            setting_key="review_model",
            model="claude-sonnet-5",
            system="s",
            user="u",
            max_tokens=10,
            budget=TokenBudget(max_tokens=1_000),
        )
    assert models == ["claude-sonnet-5"]  # blocked tier -> original 429 re-raised, no second call


@pytest.mark.parametrize("fallback", ["", "primary-model"])
async def test_rate_limit_fallback_reraises_when_no_usable_fallback(monkeypatch: pytest.MonkeyPatch, fallback: str):
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: fallback)
    models: list[str] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        models.append(kwargs["model"])
        raise LlmRateLimited("provider rate limit (429) persisted", attempts=4)

    monkeypatch.setattr(llm, "complete", fake_complete)
    with pytest.raises(LlmRateLimited):
        await llm_escalation.complete_with_rate_limit_fallback(
            setting_key="review_model",
            model="primary-model",
            system="s",
            user="u",
            max_tokens=10,
            budget=TokenBudget(max_tokens=1_000),
        )
    assert models == ["primary-model"]


async def test_rate_limit_fallback_never_retries_non_429_errors(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: "fallback-model")
    models: list[str] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        models.append(kwargs["model"])
        raise ValueError("malformed request — not provider state")

    monkeypatch.setattr(llm, "complete", fake_complete)
    with pytest.raises(ValueError):
        await llm_escalation.complete_with_rate_limit_fallback(
            setting_key="review_model",
            model="primary-model",
            system="s",
            user="u",
            max_tokens=10,
            budget=TokenBudget(max_tokens=1_000),
        )
    assert models == ["primary-model"]  # only rate limits trigger the hop


# --- (b) scene-packet author rides attempt_with_escalation -----------------------------------------

_VALID_BODY: dict[str, Any] = {
    "scene_no": 1,
    "word_budget": {"target": 900},
    "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
    "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
    "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
}


async def _author() -> dict[str, Any]:
    return await sp_author.author_scene_packet(
        pov="Marcus",
        chapter_packet_body={"chapter_job": "hold the line"},
        scene_seed={"seed_id": str(uuid.uuid4()), "scene_no": 1},
        word_budget={"target": 1200},
        budget=TokenBudget(max_tokens=100_000),
    )


async def test_scene_packet_author_escalates_unparseable_primary_to_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "scene_packet_author_model", "primary-model")
    monkeypatch.setattr(settings, "scene_packet_author_fallback_model", "fallback-model")
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        calls.append(kwargs)
        if kwargs["model"] == "primary-model":
            return "sorry, I cannot emit JSON today", _usage()
        return json.dumps(_VALID_BODY), _usage()

    monkeypatch.setattr(llm, "complete", fake_complete)
    body = await _author()
    assert [c["model"] for c in calls] == ["primary-model", "fallback-model"]
    assert calls[1]["max_tokens"] >= 12_000  # fallback gets the extra token headroom
    assert body["word_budget"] == {"target": 1200}  # planner's numbers re-stamped server-side


async def test_scene_packet_author_fails_loud_when_fallback_also_unparseable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "scene_packet_author_model", "primary-model")
    monkeypatch.setattr(settings, "scene_packet_author_fallback_model", "fallback-model")

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        return "still not JSON", _usage()

    monkeypatch.setattr(llm, "complete", fake_complete)
    with pytest.raises(ScenePacketAuthorError, match="fallback"):
        await _author()


# --- (c) honesty flags ------------------------------------------------------------------------------


def test_honesty_maps_are_pinned():
    assert agent_ops.QUALITY_LIVE == {"draft_model", "review_model"}
    assert agent_ops.SEMANTIC_LIVE == {"packet_qa_model", "scene_packet_qa_model"}
    assert agent_ops.AUTO_RUN_LIVE == {"enrich_model", "review_model"}
    assert agent_ops.FALLBACK_MODE == {"review_model": "rate_limit_only", "enrich_model": "rate_limit_only"}


def test_agent_ops_rows_carry_honesty_controls():
    rows: dict[str, AgentOpsAgentOut] = {a.setting_key: agent_ops._agent_ops_row(a, None) for a in AGENTS}
    assert set(rows) == {a.setting_key for a in AGENTS}
    for key, row in rows.items():
        assert row.controls.quality_live is (key in {"draft_model", "review_model"})
        assert row.controls.semantic_escalation_live is (key in {"packet_qa_model", "scene_packet_qa_model"})
        assert row.controls.auto_run_live is (key in {"enrich_model", "review_model"})
    assert rows["review_model"].controls.fallback_mode == "rate_limit_only"
    assert rows["enrich_model"].controls.fallback_mode == "rate_limit_only"
    assert rows["draft_model"].controls.fallback_mode == "escalation"
    assert rows["scene_packet_author_model"].controls.fallback_mode == "escalation"


def test_controls_default_to_all_dead():
    c = AgentControlsOut()
    assert (c.quality_live, c.semantic_escalation_live, c.auto_run_live) == (False, False, False)
    assert c.fallback_mode == "escalation"


# --- (d) presets ------------------------------------------------------------------------------------


def test_presets_drop_the_dead_review_semantic_escalation_hint():
    for preset in PRESETS:
        assert "semantic_escalation" not in (preset.policy_hints.get("review_model") or {}), preset.id
    # quality hints survive — quality IS live for the review lanes now.
    assert PRESET_BY_ID["high_quality_chapter"].policy_hints["review_model"] == {"quality_level": "quality"}
    assert PRESET_BY_ID["continuity_audit"].policy_hints["review_model"] == {"quality_level": "quality"}
    # QA-gate semantic hints stay (semantic escalation is live there).
    assert PRESET_BY_ID["high_quality_chapter"].policy_hints["packet_qa_model"] == {"semantic_escalation": True}
