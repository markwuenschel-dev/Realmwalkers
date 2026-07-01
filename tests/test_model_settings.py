"""Runtime model selection: PUT mutates the live settings + persists, GET lists every role, startup
re-applies the saved override. Settings is a process-global singleton, so we restore what we touch."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.routers import settings as settings_router
from dominion.shared.config import settings as cfg
from dominion.shared.models import ModelOverride
from dominion.shared.schemas import ModelSettingUpdateIn


async def test_set_model_applies_live_and_persists_and_reloads(db_factory):
    original = cfg.draft_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="opus"),
                s,
            )
            assert out.model == "claude-opus-4-8" and out.tier == "opus"
            assert cfg.draft_model == "claude-opus-4-8"  # live mutation
            row = await s.get(ModelOverride, "draft_model")
            assert row is not None and row.model == "claude-opus-4-8"  # persisted

            # startup re-apply restores the saved choice over a fresh-default settings
            cfg.draft_model = "claude-sonnet-5"
            applied = await settings_router.apply_model_overrides(s)
            assert applied >= 1 and cfg.draft_model == "claude-opus-4-8"
    finally:
        cfg.draft_model = original


async def test_get_models_lists_roles_and_tiers(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_models(s)
        keys = {a.setting for a in out.agents}
        assert {
            "draft_model",
            "review_model",
            "enrich_model",
            "packet_author_model",
            "packet_qa_model",
            "scene_packet_author_model",
            "scene_packet_qa_model",  # per-scene contract stage
        } <= keys
        assert out.tiers["opus"] == "claude-opus-4-8"
        assert out.tiers["haiku"] == "claude-haiku-4-5"


async def test_scene_packet_author_model_is_settable_from_tab(db_factory):
    """Regression: the per-scene Author/QA stage was hardwired to its config default and absent from the
    models tab, so picking Haiku there never reached it. It must now be a real, switchable role."""
    original = cfg.scene_packet_author_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_model(
                ModelSettingUpdateIn(setting="scene_packet_author_model", tier="sonnet"),
                s,
            )
            assert out.model == "claude-sonnet-5"
            assert cfg.scene_packet_author_model == "claude-sonnet-5"  # live mutation reaches the stage
    finally:
        cfg.scene_packet_author_model = original


async def test_set_model_rejects_unknown_setting_or_tier(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.set_model(ModelSettingUpdateIn(setting="nope_model", tier="opus"), s)
        with pytest.raises(HTTPException):
            await settings_router.set_model(ModelSettingUpdateIn(setting="draft_model", tier="gpt"), s)


# --- multi-provider: PUT/GET carry a provider alongside the tier -----------------------------------


async def test_get_models_lists_provider_tiers(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_models(s)
        assert out.provider_tiers["anthropic"]["opus"] == "claude-opus-4-8"
        assert out.provider_tiers["openai"]["opus"] == "gpt-5.5"
        assert out.provider_tiers["openai"]["sonnet"] == "gpt-5.4-mini"
        assert out.provider_tiers["openai"]["haiku"] == "gpt-5.4-nano"
        assert out.provider_tiers["xai"]["opus"] == "grok-4.3"
        assert "haiku" not in out.provider_tiers["xai"]  # xAI only ships one model today


async def test_set_model_defaults_to_anthropic_provider(db_factory):
    """Callers that predate multi-provider support (no provider in the body) must keep resolving
    to Anthropic, unchanged."""
    original = cfg.draft_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="opus"),
                s,
            )
            assert out.provider == "anthropic"
            assert out.model == "claude-opus-4-8"
    finally:
        cfg.draft_model = original


async def test_set_model_accepts_openai_provider(db_factory):
    original = cfg.draft_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="sonnet", provider="openai"),
                s,
            )
            assert out.model == "gpt-5.4-mini"
            assert out.provider == "openai"
            assert out.tier == "sonnet"
            assert cfg.draft_model == "gpt-5.4-mini"  # live mutation
    finally:
        cfg.draft_model = original


async def test_set_model_accepts_xai_provider(db_factory):
    original = cfg.draft_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="opus", provider="xai"),
                s,
            )
            assert out.model == "grok-4.3"
            assert out.provider == "xai"
    finally:
        cfg.draft_model = original


async def test_set_model_rejects_unknown_provider(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="opus", provider="mistral"), s
            )


async def test_set_model_rejects_tier_the_provider_does_not_have(db_factory):
    """xAI only ships one model (slotted at opus) -- haiku/sonnet must 422, not silently fall back."""
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.set_model(
                ModelSettingUpdateIn(setting="draft_model", tier="haiku", provider="xai"), s
            )
