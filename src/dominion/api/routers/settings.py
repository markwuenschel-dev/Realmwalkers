"""Runtime model selection and agent operations (presets, policies, stats, smoke tests)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from dominion.api.agent_smoke import run_smoke_test
from dominion.api.deps import SessionDep
from dominion.shared import agent_ops
from dominion.shared.agent_registry import ROLE_KEYS, TIERS
from dominion.shared.schemas import (
    AgentGlobalsUpdateIn,
    AgentOpsOut,
    AgentPolicyUpdateIn,
    AgentStatsListOut,
    CustomPresetCreateIn,
    ModelSettingOut,
    ModelSettingsOut,
    ModelSettingUpdateIn,
    SmokeTestIn,
    SmokeTestOut,
)

log = structlog.get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])


async def apply_model_overrides(session) -> int:
    """Load persisted overrides into live settings. Called on app startup."""
    return await agent_ops.apply_model_overrides(session)


@router.get("/models", response_model=ModelSettingsOut)
async def get_models(session: SessionDep) -> ModelSettingsOut:
    """Every customizable agent's current model + tier (legacy endpoint)."""
    from dominion.shared.agent_registry import AGENTS

    agents = [agent_ops.model_setting_out(a) for a in AGENTS]
    return ModelSettingsOut(agents=agents, tiers=TIERS)


@router.put("/models", response_model=ModelSettingOut)
async def set_model(body: ModelSettingUpdateIn, session: SessionDep) -> ModelSettingOut:
    """Point one agent role at Haiku / Sonnet / Opus. Applies live + persists."""
    if body.setting not in ROLE_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown agent setting '{body.setting}'")
    if body.tier not in TIERS:
        raise HTTPException(status_code=422, detail="tier must be haiku, sonnet, or opus")
    try:
        out = await agent_ops.apply_tier_to_agent(session, body.setting, body.tier)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await agent_ops.set_active_preset(session, "custom")
    await session.commit()
    log.info("settings.model_changed", setting=body.setting, tier=body.tier)
    return out


@router.get("/agents", response_model=AgentOpsOut)
async def get_agents(session: SessionDep) -> AgentOpsOut:
    """Full agent operations panel state."""
    return await agent_ops.build_agent_ops(session)


@router.put("/presets/{preset_id}", response_model=AgentOpsOut)
async def apply_preset(preset_id: str, session: SessionDep) -> AgentOpsOut:
    """Apply a built-in or saved custom preset to all agent roles."""
    try:
        return await agent_ops.apply_preset(session, preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/presets/custom", response_model=AgentOpsOut)
async def save_custom_preset(body: CustomPresetCreateIn, session: SessionDep) -> AgentOpsOut:
    """Save the current agent ops configuration as a named custom preset."""
    try:
        return await agent_ops.save_custom_preset(session, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/presets/{preset_id}", response_model=AgentOpsOut)
async def delete_custom_preset(preset_id: str, session: SessionDep) -> AgentOpsOut:
    """Delete a user-saved custom preset (user:… ids only)."""
    try:
        return await agent_ops.delete_custom_preset(session, preset_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/agents/globals", response_model=AgentOpsOut)
async def set_agent_globals(body: AgentGlobalsUpdateIn, session: SessionDep) -> AgentOpsOut:
    """Update global scene token and wall-clock budgets."""
    try:
        return await agent_ops.apply_globals(session, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/agents/{setting}/policy", response_model=AgentOpsOut)
async def set_agent_policy(setting: str, body: AgentPolicyUpdateIn, session: SessionDep) -> AgentOpsOut:
    """Update fallback chain / never-fallback tiers for one agent."""
    if setting not in ROLE_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown agent setting '{setting}'")
    try:
        return await agent_ops.apply_agent_policy(
            session,
            setting,
            fallback_tier=body.fallback_tier,
            never_fallback=body.never_fallback,
            semantic_escalation=body.semantic_escalation,
            quality_level=body.quality_level,
            permissions=body.permissions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/agents/stats", response_model=AgentStatsListOut)
async def get_agent_stats(session: SessionDep) -> AgentStatsListOut:
    """Per-agent health stats from recent llm_calls."""
    return await agent_ops.build_agent_stats(session)


@router.post("/agents/smoke-test", response_model=SmokeTestOut)
async def smoke_test(body: SmokeTestIn | None = None) -> SmokeTestOut:
    """Offline fixture smoke test, or optional live API pings with cost warning."""
    agents = body.agents if body else None
    live = body.live if body else False
    return await run_smoke_test(agents=agents, live=live)
