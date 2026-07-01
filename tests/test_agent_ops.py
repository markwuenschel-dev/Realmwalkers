"""Agent operations panel API — presets, policies, stats."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.routers import settings as settings_router
from dominion.shared import agent_ops
from dominion.shared.agent_registry import AGENTS
from dominion.shared.config import settings as cfg
from dominion.shared.models import AgentOpsState, AgentPolicyOverride
from dominion.shared.schemas import AgentPermissionsPatchIn, AgentPolicyUpdateIn, ModelSettingUpdateIn


async def test_get_agents_returns_presets_and_contracts(db_factory):
    async with db_factory() as s:
        out = await settings_router.get_agents(s)
        assert len(out.presets) >= 4
        assert len(out.agents) == 7
        assert out.provider_tiers["anthropic"]["opus"] == "claude-opus-4-8"
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


async def test_apply_preset_preserves_openai_provider(db_factory):
    """Regression: apply_preset called apply_tier_to_agent without a provider, which defaults to
    Anthropic -- silently reverting any agent the user had pointed at openai/xai back to Anthropic
    on every preset apply. Presets only encode a tier (quality level); the provider an agent is
    already on must survive applying one."""
    original = {a.setting_key: getattr(cfg, a.setting_key) for a in AGENTS}
    try:
        async with db_factory() as s:
            await agent_ops.apply_tier_to_agent(s, "draft_model", "sonnet", "openai")
            out = await settings_router.apply_preset("high_quality_chapter", s)
            draft = next(a for a in out.agents if a.setting == "draft_model")
            assert draft.provider == "openai"  # preset changes tier only, not provider
            assert draft.tier == "opus"  # high_quality_chapter wants draft_model at opus
            assert draft.model == "gpt-5.5"
    finally:
        for key, val in original.items():
            setattr(cfg, key, val)


async def test_apply_preset_preserves_xai_provider_when_tier_available(db_factory):
    original = {a.setting_key: getattr(cfg, a.setting_key) for a in AGENTS}
    try:
        async with db_factory() as s:
            await agent_ops.apply_tier_to_agent(s, "draft_model", "opus", "xai")
            out = await settings_router.apply_preset("fast_drafting", s)  # also wants draft_model=opus
            draft = next(a for a in out.agents if a.setting == "draft_model")
            assert draft.provider == "xai"
            assert draft.model == "grok-4.3"
    finally:
        for key, val in original.items():
            setattr(cfg, key, val)


async def test_apply_preset_clamps_tier_instead_of_reverting_provider(db_factory):
    """The exact bug-report scenario: an agent sits on xai/opus; the preset wants a tier xAI doesn't
    ship (fast_drafting wants review_model=haiku, xAI only has opus). Must stay on xai and resolve
    to its nearest available tier -- not raise, and not silently switch back to Anthropic."""
    original = {a.setting_key: getattr(cfg, a.setting_key) for a in AGENTS}
    try:
        async with db_factory() as s:
            await agent_ops.apply_tier_to_agent(s, "review_model", "opus", "xai")
            out = await settings_router.apply_preset("fast_drafting", s)
            review = next(a for a in out.agents if a.setting == "review_model")
            assert review.provider == "xai"
            assert review.model == "grok-4.3"
            assert review.tier == "opus"
    finally:
        for key, val in original.items():
            setattr(cfg, key, val)


async def test_merge_policy_hints_resolves_fallback_tier_against_current_provider(db_factory):
    """Same bug class as the primary-tier one above: a preset policy hint's fallback_tier used to
    resolve via model_for_tier(fb_tier) with no provider arg -- defaulting to Anthropic regardless
    of the agent's actual provider. (No built-in preset sets this hint today, so this exercises the
    path directly against the shape a future preset would use.)"""
    original_model = cfg.packet_qa_model
    original_fb = cfg.packet_qa_fallback_model
    try:
        async with db_factory() as s:
            await agent_ops.apply_tier_to_agent(s, "packet_qa_model", "opus", "xai")
            await agent_ops._merge_policy_hints(s, {"packet_qa_model": {"fallback_tier": "haiku"}})
            await s.commit()
            override = await s.get(AgentPolicyOverride, "packet_qa_model")
            assert override is not None
            assert override.policy_json["fallback_provider"] == "xai"
            assert override.policy_json["fallback_tier"] == "opus"  # clamped: xai has no haiku
            assert cfg.packet_qa_fallback_model == "grok-4.3"
    finally:
        cfg.packet_qa_model = original_model
        cfg.packet_qa_fallback_model = original_fb


async def test_set_agent_policy_fallback_defaults_to_agents_current_provider(db_factory):
    """Same bug class again: PUT /settings/agents/{setting}/policy hardcoded fallback_provider to
    "anthropic" whenever the caller omitted it, instead of resolving against the agent's own
    current provider."""
    original_model = cfg.packet_qa_model
    original_fb = cfg.packet_qa_fallback_model
    try:
        async with db_factory() as s:
            await agent_ops.apply_tier_to_agent(s, "packet_qa_model", "sonnet", "openai")
            out = await settings_router.set_agent_policy(
                "packet_qa_model",
                AgentPolicyUpdateIn(fallback_tier="haiku"),  # no fallback_provider given
                s,
            )
            qa = next(a for a in out.agents if a.setting == "packet_qa_model")
            assert qa.policy.fallback_provider == "openai"
            assert qa.policy.fallback_tier == "haiku"
            assert cfg.packet_qa_fallback_model == "gpt-5.4-nano"
    finally:
        cfg.packet_qa_model = original_model
        cfg.packet_qa_fallback_model = original_fb


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
            assert qa.policy.fallback_provider == "anthropic"
            assert cfg.packet_qa_fallback_model == "claude-opus-4-8"
            state = await s.get(AgentOpsState, "default")
            assert state is not None and state.active_preset == "custom"
    finally:
        cfg.packet_qa_fallback_model = original_fb


async def test_set_agent_policy_accepts_openai_fallback_provider(db_factory):
    original_fb = cfg.packet_qa_fallback_model
    try:
        async with db_factory() as s:
            out = await settings_router.set_agent_policy(
                "packet_qa_model",
                AgentPolicyUpdateIn(fallback_tier="sonnet", fallback_provider="openai"),
                s,
            )
            qa = next(a for a in out.agents if a.setting == "packet_qa_model")
            assert qa.policy.fallback_tier == "sonnet"
            assert qa.policy.fallback_provider == "openai"
            assert cfg.packet_qa_fallback_model == "gpt-5.4-mini"
    finally:
        cfg.packet_qa_fallback_model = original_fb


async def test_set_agent_policy_rejects_tier_the_fallback_provider_lacks(db_factory):
    # xAI only ships one model, slotted at opus -- haiku/sonnet fallback must raise, not silently fall back.
    async with db_factory() as s:
        with pytest.raises(ValueError, match="xai"):
            await agent_ops.apply_agent_policy(s, "packet_qa_model", fallback_tier="haiku", fallback_provider="xai")


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
