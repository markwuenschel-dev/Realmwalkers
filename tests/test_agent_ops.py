"""Agent operations panel API — presets, policies, stats."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.routers import settings as settings_router
from dominion.shared.agent_registry import AGENTS
from dominion.shared.config import settings as cfg
from dominion.shared.models import AgentOpsState
from dominion.shared.schemas import AgentPermissionsPatchIn, AgentPolicyUpdateIn, ModelSettingUpdateIn


async def test_get_agents_returns_presets_and_contracts(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_agents(s)
        assert len(out.presets) >= 4
        assert len(out.agents) == 7
        assert len(out.providers) == 6
        assert out.providers[0].status == "active"
        draft = next(a for a in out.agents if a.setting == "draft_model")
        assert draft.contract.inputs
        assert draft.policy.escalation_rules


async def test_apply_preset_sets_all_tiers_and_active(db_factory):
    original = {a.setting_key: getattr(cfg, a.setting_key) for a in AGENTS}
    try:
        async with db_factory() as s:
            out = await settings_router.apply_preset("continuity_audit", s)
            assert out.active_preset == "continuity_audit"
            review = next(a for a in out.agents if a.setting == "review_model")
            assert review.policy.semantic_escalation is True
            assert review.policy.quality_level == "quality"
    finally:
        for key, val in original.items():
            setattr(cfg, key, val)


async def test_apply_budget_preset_sets_all_tiers(db_factory):
    original = {a.setting_key: getattr(cfg, a.setting_key) for a in AGENTS}
    try:
        async with db_factory() as s:
            out = await settings_router.apply_preset("budget_mode", s)
            assert out.active_preset == "budget_mode"
            draft = next(a for a in out.agents if a.setting == "draft_model")
            assert draft.tier == "sonnet"
            review = next(a for a in out.agents if a.setting == "review_model")
            assert review.tier == "haiku"
    finally:
        for key, val in original.items():
            setattr(cfg, key, val)


async def test_set_agent_policy_updates_fallback(db_factory):
    original_fb = cfg.packet_qa_fallback_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_agent_policy(
                "packet_qa_model",
                AgentPolicyUpdateIn(fallback_tier="opus"),
                s,
            )
            qa = next(a for a in out.agents if a.setting == "packet_qa_model")
            assert qa.policy.fallback_tier == "opus"
            assert cfg.packet_qa_fallback_model == "claude-opus-4-8"
            state = await s.get(AgentOpsState, "default")
            assert state is not None and state.active_preset == "custom"
    finally:
        cfg.packet_qa_fallback_model = original_fb


async def test_agent_stats_empty_db(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_agent_stats(s)
        assert len(out.agents) == 7
        assert out.window_runs == 0


async def test_smoke_test_endpoint():
    out = await settings_router.smoke_test(None)
    assert out.results
    assert isinstance(out.all_passed, bool)


async def test_set_model_marks_custom_preset(db_factory):
    async with db_factory() as s:
        await settings_router.set_model(ModelSettingUpdateIn(setting="draft_model", tier="opus"), s)
        state = await s.get(AgentOpsState, "default")
        assert state is not None and state.active_preset == "custom"


async def test_apply_preset_unknown_raises(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException):
            await settings_router.apply_preset("nope", s)


async def test_policy_quality_and_semantic_escalation(db_factory):
    async with db_factory() as s:
        out = await settings_router.set_agent_policy(
            "draft_model",
            AgentPolicyUpdateIn(quality_level="quality", semantic_escalation=False),
            s,
        )
        draft = next(a for a in out.agents if a.setting == "draft_model")
        assert draft.policy.quality_level == "quality"
        assert draft.policy.semantic_escalation is False
        assert draft.contract.temperature == 0.5


async def test_policy_permissions_patch_merges(db_factory):
    async with db_factory() as s:
        out = await settings_router.set_agent_policy(
            "enrich_model",
            AgentPolicyUpdateIn(permissions=AgentPermissionsPatchIn(auto_run=False)),
            s,
        )
        enrich = next(a for a in out.agents if a.setting == "enrich_model")
        assert enrich.permissions.auto_run is False


async def test_pipeline_estimate_includes_usd(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_agents(s)
        assert out.pipeline_estimate.estimated_usd_per_chapter is not None
        assert out.pipeline_estimate.estimated_usd_per_chapter > 0
        draft = next(a for a in out.agents if a.setting == "draft_model")
        assert draft.estimate.estimated_usd_per_chapter is not None
