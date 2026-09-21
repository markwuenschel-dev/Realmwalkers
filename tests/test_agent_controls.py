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
    # prose_suggestion_model joined on 2026-09-20: `read_through/suggest.py` genuinely reads
    # quality_temperature/quality_effort for it, so the page must report the knob as live.
    assert agent_ops.QUALITY_LIVE == {"draft_model", "review_model", "prose_suggestion_model"}
    assert agent_ops.SEMANTIC_LIVE == {"packet_qa_model", "scene_packet_qa_model"}
    assert agent_ops.AUTO_RUN_LIVE == {"enrich_model", "review_model"}
    assert agent_ops.FALLBACK_MODE == {"review_model": "rate_limit_only", "enrich_model": "rate_limit_only"}


def test_agent_ops_rows_carry_honesty_controls():
    rows: dict[str, AgentOpsAgentOut] = {a.setting_key: agent_ops._agent_ops_row(a, None) for a in AGENTS}
    assert set(rows) == {a.setting_key for a in AGENTS}
    for key, row in rows.items():
        assert row.controls.quality_live is (key in {"draft_model", "review_model", "prose_suggestion_model"})
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


# --- (d) the tier vocabulary -------------------------------------------------------------------


def test_every_tier_in_the_vocabulary_is_ranked_and_has_a_latency_band():
    """The three maps that define a tier live in two modules and are hand-maintained. A tier present
    in one and missing from another does not fail to import — it silently sorts or estimates wrong."""
    from typing import get_args

    from dominion.shared import agent_registry as reg
    from dominion.shared.model_pricing import TIER_LATENCY_SEC

    vocabulary = set(get_args(reg.Tier))
    assert set(reg._TIER_RANK) == vocabulary, "a tier with no rank cannot be ordered against the others"
    assert set(TIER_LATENCY_SEC) == vocabulary, "a tier with no latency band estimates at the default"


def test_fable_outranks_opus_and_a_provider_without_one_falls_back_to_opus():
    """`fable` is the frontier band. Only Anthropic and OpenAI ship one, so every other provider must
    round DOWN to its strongest available tier rather than erroring or silently picking a cheap one."""
    from dominion.shared.agent_registry import _TIER_RANK, PROVIDER_TIERS, model_for_tier, resolve_tier_for_provider

    assert _TIER_RANK["fable"] > _TIER_RANK["opus"]
    assert model_for_tier("fable", "anthropic") == "claude-fable-5-1"
    assert model_for_tier("fable", "openai") == "gpt-6-astra"
    for provider in PROVIDER_TIERS:
        resolved = resolve_tier_for_provider("fable", provider)
        if "fable" in PROVIDER_TIERS[provider]:
            assert resolved == "fable"
        else:
            assert resolved == "opus", f"{provider} should round down to opus, got {resolved}"


def test_fable_5_1_takes_effort_despite_not_matching_the_fable_5_entry():
    """`supports_effort` compares `model.split("-20")[0]` against an allowlist, so "claude-fable-5-1"
    does NOT match the "claude-fable-5" entry. Without its own entry the app would silently stop
    sending `effort` to the strongest model it offers — the same trap claude-opus-latest hit."""
    from dominion.shared.agent_registry import supports_effort, supports_temperature

    assert supports_effort("claude-fable-5-1") is True
    # Flagship Anthropic models 400 on `temperature`; the allowlist is deliberately not extended.
    assert supports_temperature("claude-fable-5-1") is False


def test_the_frontier_models_are_priced_at_twice_opus():
    from dominion.shared.model_pricing import pricing_for_model

    fable = pricing_for_model("claude-fable-5-1")
    opus = pricing_for_model("claude-opus-latest")
    assert (fable.input, fable.output, fable.cache_read) == (10.0, 50.0, 0.25)
    assert fable.input == 2 * opus.input and fable.output == 2 * opus.output
    assert pricing_for_model("gpt-6-astra").input == 10.0


def test_a_fable_agent_is_counted_as_frontier_not_as_the_cheapest_tier():
    """The bucket used to be an if/elif/else ending in `else: haiku += n`, so any tier added later was
    silently counted as the cheapest and rendered as "fast-tier calls" — the most expensive band in
    the book reported as the least."""
    from dominion.shared import agent_ops
    from dominion.shared.agent_registry import AGENTS

    rows = [agent_ops._agent_ops_row(a, None) for a in AGENTS]
    before = agent_ops._pipeline_estimate(rows)
    drafter = next(r for r in rows if r.setting == "draft_model")
    was = drafter.tier
    drafter.tier = "fable"
    after = agent_ops._pipeline_estimate(rows)

    calls = next(a for a in AGENTS if a.setting_key == "draft_model").estimate.typical_calls_per_chapter
    assert calls > 0, "this test is vacuous unless the drafter actually makes calls"
    # The drafter's calls move OUT of whatever band it was in and INTO the frontier band. Asserted as
    # a delta because the other roles legitimately occupy the other buckets.
    assert after.fable_calls == before.fable_calls + calls
    assert getattr(after, f"{was}_calls") == getattr(before, f"{was}_calls") - calls
    assert after.total_estimated_calls == before.total_estimated_calls, "nothing should be lost"


def test_a_dated_frontier_id_still_resolves_to_its_tier():
    """`provider_and_tier_of` falls back to a substring scan for ids that predate or postdate the
    catalog. Without "fable" in that scan a dated snapshot resolves to NO tier, which unhighlights the
    button, mis-estimates latency, and remaps to haiku in the length guard."""
    from dominion.shared.agent_registry import provider_and_tier_of

    assert provider_and_tier_of("claude-fable-5-1-20260915") == ("anthropic", "fable")
    assert provider_and_tier_of("claude-haiku-4-5-20251001") == ("anthropic", "haiku")
