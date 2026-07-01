"""Agent operations Phase 3 — custom presets, globals, reviewer telemetry stages."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.agent_smoke import estimate_live_smoke_cost_usd
from dominion.api.routers import settings as settings_router
from dominion.shared.agent_ops import _custom_preset_id
from dominion.shared.reviewer_telemetry import reviewer_telemetry_stage
from dominion.shared.schemas import AgentGlobalsUpdateIn, CustomPresetCreateIn, ModelSettingUpdateIn, SmokeTestIn


def test_reviewer_telemetry_stage_names():
    assert reviewer_telemetry_stage("continuity") == "reviewer_continuity"
    assert reviewer_telemetry_stage("voice") == "reviewer_voice"


def test_custom_preset_id_slug():
    assert _custom_preset_id("My Fast Iteration").startswith("user:")
    assert "my_fast_iteration" in _custom_preset_id("My Fast Iteration")


def test_estimate_live_smoke_cost_positive():
    assert estimate_live_smoke_cost_usd(["packet_qa_model"]) > 0


async def test_save_and_apply_custom_preset(db_factory):
    async with db_factory() as s:
        await settings_router.set_model(ModelSettingUpdateIn(setting="review_model", tier="sonnet"), s)
        saved = await settings_router.save_custom_preset(CustomPresetCreateIn(label="Sonnet Review"), s)
        custom_id = next(p.id for p in saved.presets if p.is_custom)
        await settings_router.set_model(ModelSettingUpdateIn(setting="review_model", tier="haiku"), s)
        restored = await settings_router.apply_preset(custom_id, s)
        review = next(a for a in restored.agents if a.setting == "review_model")
        assert review.tier == "sonnet"


async def test_custom_preset_snapshot_preserves_primary_model_provider(db_factory):
    # Regression: _capture_snapshot/_apply_snapshot used to record only the tier, so restoring a
    # snapshot captured with a non-Anthropic primary model silently restored the Anthropic model
    # for that tier instead (e.g. "opus" -> claude-opus-4-8 instead of the saved gpt-5.5).
    async with db_factory() as s:
        await settings_router.set_model(ModelSettingUpdateIn(setting="review_model", tier="opus", provider="openai"), s)
        saved = await settings_router.save_custom_preset(CustomPresetCreateIn(label="OpenAI Review"), s)
        custom_id = next(p.id for p in saved.presets if p.is_custom)
        await settings_router.set_model(ModelSettingUpdateIn(setting="review_model", tier="haiku"), s)
        restored = await settings_router.apply_preset(custom_id, s)
        review = next(a for a in restored.agents if a.setting == "review_model")
        assert review.provider == "openai"
        assert review.model == "gpt-5.5"


async def test_delete_custom_preset(db_factory):
    async with db_factory() as s:
        saved = await settings_router.save_custom_preset(CustomPresetCreateIn(label="To Delete"), s)
        pid = next(p.id for p in saved.presets if p.is_custom)
        out = await settings_router.delete_custom_preset(pid, s)
        assert not any(p.id == pid for p in out.presets)


async def test_delete_builtin_preset_rejected(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.delete_custom_preset("fast_drafting", s)


async def test_apply_globals(db_factory):
    async with db_factory() as s:
        out = await settings_router.set_agent_globals(
            AgentGlobalsUpdateIn(scene_token_budget=55_000, scene_time_budget_s=240),
            s,
        )
        assert out.globals.scene_token_budget == 55_000
        assert out.globals.scene_time_budget_s == 240


async def test_smoke_test_live_mode_offline():
    out = await settings_router.smoke_test(SmokeTestIn(live=False))
    assert out.mode == "offline"
    assert out.live_warning is None
