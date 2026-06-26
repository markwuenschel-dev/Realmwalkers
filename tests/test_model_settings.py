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
                ModelSettingUpdateIn(setting="draft_model", tier="opus"), s,
            )
            assert out.model == "claude-opus-4-8" and out.tier == "opus"
            assert cfg.draft_model == "claude-opus-4-8"          # live mutation
            row = await s.get(ModelOverride, "draft_model")
            assert row is not None and row.model == "claude-opus-4-8"   # persisted

            # startup re-apply restores the saved choice over a fresh-default settings
            cfg.draft_model = "claude-sonnet-4-6"
            applied = await settings_router.apply_model_overrides(s)
            assert applied >= 1 and cfg.draft_model == "claude-opus-4-8"
    finally:
        cfg.draft_model = original


async def test_get_models_lists_roles_and_tiers(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_models(s)
        keys = {a.setting for a in out.agents}
        assert {"draft_model", "review_model", "enrich_model", "packet_author_model", "packet_qa_model"} <= keys
        assert out.tiers["opus"] == "claude-opus-4-8"
        assert out.tiers["haiku"] == "claude-haiku-4-5"


async def test_set_model_rejects_unknown_setting_or_tier(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.set_model(ModelSettingUpdateIn(setting="nope_model", tier="opus"), s)
        with pytest.raises(HTTPException):
            await settings_router.set_model(ModelSettingUpdateIn(setting="draft_model", tier="gpt"), s)
