"""Runtime model selection and agent operations (presets, policies, stats, smoke tests)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException

from dominion.api.agent_smoke import run_smoke_test
from dominion.api.deps import SessionDep
from dominion.shared import agent_ops
from dominion.shared.agent_registry import PROVIDER_TIERS, ROLE_KEYS, TIERS
from dominion.shared.enums import RepairAuthorityLevel
from dominion.shared.schemas import (
    AgentGlobalsUpdateIn,
    AgentOpsOut,
    AgentPolicyUpdateIn,
    AgentStatsListOut,
    AutonomyOut,
    AutonomyUpdateIn,
    CustomPresetCreateIn,
    ModelSettingOut,
    ModelSettingsOut,
    ModelSettingUpdateIn,
    SmokeTestIn,
    SmokeTestOut,
    SweeperStatusOut,
)
from dominion.workers import sweeper

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
    return ModelSettingsOut(agents=agents, tiers=TIERS, provider_tiers=PROVIDER_TIERS)


@router.put("/models", response_model=ModelSettingOut)
async def set_model(body: ModelSettingUpdateIn, session: SessionDep) -> ModelSettingOut:
    """Point one agent role at a provider + tier (e.g. anthropic/opus, openai/sonnet). Applies live + persists."""
    if body.setting not in ROLE_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown agent setting '{body.setting}'")
    if body.provider not in PROVIDER_TIERS:
        raise HTTPException(status_code=422, detail=f"unknown provider '{body.provider}'")
    if body.tier not in PROVIDER_TIERS[body.provider]:
        raise HTTPException(status_code=422, detail=f"provider '{body.provider}' has no model for tier '{body.tier}'")
    try:
        out = await agent_ops.apply_tier_to_agent(session, body.setting, body.tier, body.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await agent_ops.set_active_preset(session, "custom")
    await session.commit()
    log.info("settings.model_changed", setting=body.setting, tier=body.tier, provider=body.provider)
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
            fallback_provider=body.fallback_provider,
            never_fallback=body.never_fallback,
            semantic_escalation=body.semantic_escalation,
            quality_level=body.quality_level,
            backend=body.backend,
            permissions=body.permissions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/agents/stats", response_model=AgentStatsListOut)
async def get_agent_stats(session: SessionDep) -> AgentStatsListOut:
    """Per-agent health stats from recent llm_calls."""
    return await agent_ops.build_agent_stats(session)


def _autonomy_out(cfg: sweeper.SweeperConfig, heartbeat: SweeperStatusOut | None = None) -> AutonomyOut:
    return AutonomyOut(
        autonomy_enabled=cfg.autonomy_enabled,
        interval_s=cfg.interval_s,
        stale_window_s=cfg.stale_window_s,
        authority_ceiling=cfg.ceiling,
        max_attempts=cfg.max_attempts,
        retention_days=cfg.retention_days,
        heartbeat=heartbeat,
    )


@router.get("/autonomy", response_model=AutonomyOut)
async def get_autonomy(session: SessionDep) -> AutonomyOut:
    """Autonomous self-repair sweeper settings (kill switch, cadence, authority ceiling, retention),
    plus the live sweeper heartbeat so the Settings screen can show the loop is alive and what it did."""
    cfg = await sweeper.load_config(session)
    heartbeat = SweeperStatusOut(**await sweeper.sweeper_status(session))
    return _autonomy_out(cfg, heartbeat)


@router.put("/autonomy", response_model=AutonomyOut)
async def set_autonomy(body: AutonomyUpdateIn, session: SessionDep) -> AutonomyOut:
    """Update the sweeper switches. Persisted as KV rows and read live on the next tick."""
    if body.authority_ceiling is not None and body.authority_ceiling not in {e.value for e in RepairAuthorityLevel}:
        raise HTTPException(status_code=422, detail=f"unknown authority ceiling '{body.authority_ceiling}'")
    await sweeper.save_config(
        session,
        autonomy_enabled=body.autonomy_enabled,
        interval_s=body.interval_s,
        stale_window_s=body.stale_window_s,
        authority_ceiling=body.authority_ceiling,
        max_attempts=body.max_attempts,
        retention_days=body.retention_days,
    )
    await session.commit()
    log.info("settings.autonomy_changed", **body.model_dump(exclude_none=True))
    return _autonomy_out(await sweeper.load_config(session))


@router.post("/agents/smoke-test", response_model=SmokeTestOut)
async def smoke_test(body: SmokeTestIn | None = None) -> SmokeTestOut:
    """Offline fixture smoke test, or optional live API pings with cost warning."""
    agents = body.agents if body else None
    live = body.live if body else False
    return await run_smoke_test(agents=agents, live=live)
