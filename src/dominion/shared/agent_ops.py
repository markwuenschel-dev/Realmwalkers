"""Business logic for the Agent Operations panel — builds API responses and applies presets/policies."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import load_runtime_policies, resolve_policy
from dominion.shared.agent_registry import (
    AGENTS,
    BUILTIN_PRESET_IDS,
    EDITORIAL_AGENTS,
    FALLBACK_ATTR,
    PRESET_BY_ID,
    PRESETS,
    PROVIDER_TIERS,
    TIERS,
    AgentDefinition,
    capability_warnings,
    model_for_tier,
    provider_of,
    resolve_tier_for_provider,
    tier_of,
)
from dominion.shared.config import settings
from dominion.shared.model_pricing import estimate_agent_chapter_usd, estimate_pipeline_chapter_usd
from dominion.shared.models import (
    AgentCustomPreset,
    AgentOpsState,
    AgentPolicyOverride,
    Approval,
    ChapterPacket,
    Critique,
    LlmCall,
    ModelOverride,
    Scene,
    ScenePacket,
)
from dominion.shared.schemas import (
    AgentContractOut,
    AgentControlsOut,
    AgentEstimateOut,
    AgentGlobalsOut,
    AgentGlobalsUpdateIn,
    AgentOpsAgentOut,
    AgentOpsOut,
    AgentPermissionsOut,
    AgentPermissionsPatchIn,
    AgentPolicyOut,
    AgentPresetOut,
    AgentStatsListOut,
    AgentStatsOut,
    CustomPresetCreateIn,
    EditorialAgentOut,
    EscalationRuleOut,
    ModelSettingOut,
    PipelineEstimateOut,
)

_OPS_STATE_ID = "default"
_CUSTOM_PRESET_PREFIX = "user:"
_ESCALATION_DESCRIPTIONS: dict[str, str] = {
    "truncated": "Escalates when output is cut off at max_tokens",
    "unparseable": "Escalates when the response cannot be parsed",
    "canon_conflict": "Escalates when QA finds canon/timeline conflicts",
    "high_qa_risk": "Escalates on REVISE_REQUIRED or multiple repair-severity issues",
    "reviewer_hard_flags": "Escalates when reviewers emit HARD severity flags",
}

_COST_RANK = {"low": 0, "medium": 1, "high": 2}
_SPEED_RANK = {"fast": 0, "medium": 1, "slow": 2}
_PASS_VERDICTS = frozenset({"approve", "approve_warn"})

# --- honesty flags (Desk Control Round) -----------------------------------------------------------
# Which control surfaces are actually WIRED to runtime behavior today, per the code audit. These are
# pinned here (not inferred) so the ops panel can never claim a knob is live that no worker reads:
# - quality_level (temperature/effort) steers only the drafter and the review/summary lanes;
# - semantic escalation fires only inside the two QA gates' attempt_with_escalation calls;
# - auto_run is consulted only for the enrichment and review lanes;
# - the review/enrich lanes fall back on provider 429s only (rate_limit_only); every other wrapped
#   agent uses full structural/semantic escalation ("escalation").
QUALITY_LIVE: frozenset[str] = frozenset({"draft_model", "review_model"})
SEMANTIC_LIVE: frozenset[str] = frozenset({"packet_qa_model", "scene_packet_qa_model"})
AUTO_RUN_LIVE: frozenset[str] = frozenset({"enrich_model", "review_model"})
FALLBACK_MODE: dict[str, str] = {"review_model": "rate_limit_only", "enrich_model": "rate_limit_only"}
_DEFAULT_FALLBACK_MODE = "escalation"


def _controls_out(setting_key: str) -> AgentControlsOut:
    return AgentControlsOut(
        quality_live=setting_key in QUALITY_LIVE,
        semantic_escalation_live=setting_key in SEMANTIC_LIVE,
        auto_run_live=setting_key in AUTO_RUN_LIVE,
        fallback_mode=FALLBACK_MODE.get(setting_key, _DEFAULT_FALLBACK_MODE),
    )


def _escalation_rules(agent: AgentDefinition) -> list[EscalationRuleOut]:
    return [
        EscalationRuleOut(trigger=t, description=_ESCALATION_DESCRIPTIONS.get(t, t)) for t in agent.escalation_triggers
    ]


def _resolved(agent: AgentDefinition, override: AgentPolicyOverride | None) -> Any:
    return resolve_policy(agent, override.policy_json if override else None)


def _policy_from_live(agent: AgentDefinition, override: AgentPolicyOverride | None) -> AgentPolicyOut:
    primary_model = getattr(settings, agent.setting_key)
    fallback_attr = FALLBACK_ATTR.get(agent.setting_key, "")
    fallback_model = (getattr(settings, fallback_attr, "") or "").strip() or None
    resolved = _resolved(agent, override)
    never_fb = list(resolved.never_fallback_tiers)
    if override and override.policy_json and override.policy_json.get("fallback_tier"):
        fb_tier = override.policy_json["fallback_tier"]
        fb_provider = override.policy_json.get("fallback_provider") or "anthropic"
        fallback_model = model_for_tier(fb_tier, fb_provider)
    return AgentPolicyOut(
        setting=agent.setting_key,
        primary_tier=tier_of(primary_model),
        primary_model=primary_model,
        fallback_tier=tier_of(fallback_model) if fallback_model else None,
        fallback_model=fallback_model,
        fallback_provider=provider_of(fallback_model) if fallback_model else None,
        never_fallback=[str(t) for t in never_fb],
        escalation_rules=_escalation_rules(agent),
        semantic_escalation=resolved.semantic_escalation,
        quality_level=resolved.quality_level,
        backend=resolved.backend,
    )


def _permissions_out(agent: AgentDefinition, override: AgentPolicyOverride | None) -> AgentPermissionsOut:
    resolved = _resolved(agent, override)
    return AgentPermissionsOut(
        auto_run=resolved.auto_run,
        require_approval=resolved.require_approval,
        can_modify_packet=resolved.can_modify_packet,
        can_block_downstream=resolved.can_block_downstream,
        can_write_summaries=resolved.can_write_summaries,
        can_update_canon=resolved.can_update_canon,
        can_only_suggest=resolved.can_only_suggest,
    )


def _estimate_out(agent: AgentDefinition) -> AgentEstimateOut:
    model = getattr(settings, agent.setting_key)
    usd, latency = estimate_agent_chapter_usd(agent.setting_key, model)
    return AgentEstimateOut(
        cost_band=agent.estimate.cost_band,
        speed_band=agent.estimate.speed_band,
        typical_calls_per_chapter=agent.estimate.typical_calls_per_chapter,
        estimated_usd_per_chapter=usd,
        estimated_latency_sec_per_chapter=latency,
    )


def _agent_ops_row(agent: AgentDefinition, override: AgentPolicyOverride | None) -> AgentOpsAgentOut:
    primary_model = getattr(settings, agent.setting_key)
    primary_tier = tier_of(primary_model)
    policy = _policy_from_live(agent, override)
    resolved = _resolved(agent, override)
    return AgentOpsAgentOut(
        setting=agent.setting_key,
        label=agent.label,
        description=agent.description,
        model=primary_model,
        tier=primary_tier,
        provider=provider_of(primary_model),
        policy=policy,
        contract=AgentContractOut(
            inputs=list(agent.contract.inputs),
            outputs=list(agent.contract.outputs),
            temperature=resolved.temperature,
            max_retries=agent.contract.max_retries,
            context_load=agent.contract.context_load,
            uses_memory=agent.contract.uses_memory,
            writes_artifacts=agent.contract.writes_artifacts,
            requires_approval=agent.contract.requires_approval,
        ),
        permissions=_permissions_out(agent, override),
        estimate=_estimate_out(agent),
        warnings=capability_warnings(
            agent.setting_key,
            primary_tier,
            policy.fallback_tier,
            semantic_escalation=resolved.semantic_escalation,
        ),
        controls=_controls_out(agent.setting_key),
    )


def _pipeline_estimate(agents: list[AgentOpsAgentOut]) -> PipelineEstimateOut:
    opus = sonnet = haiku = total = 0
    max_cost = 0
    max_speed = 0
    agent_models = {row.setting: row.model for row in agents}
    for row in agents:
        agent_def = next(a for a in AGENTS if a.setting_key == row.setting)
        n = agent_def.estimate.typical_calls_per_chapter
        total += n
        tier = row.tier or "sonnet"
        if tier == "opus":
            opus += n
        elif tier == "sonnet":
            sonnet += n
        else:
            haiku += n
        max_cost = max(max_cost, _COST_RANK.get(agent_def.estimate.cost_band, 1))
        max_speed = max(max_speed, _SPEED_RANK.get(agent_def.estimate.speed_band, 1))
    cost_labels = {v: k for k, v in _COST_RANK.items()}
    speed_labels = {v: k for k, v in _SPEED_RANK.items()}
    usd_high, usd_low, seq_latency = estimate_pipeline_chapter_usd(agent_models)
    return PipelineEstimateOut(
        cost_band=cost_labels[max_cost],
        latency_band=speed_labels[max_speed],
        summary=f"~{total} LLM calls per chapter (estimated)",
        opus_calls=opus,
        sonnet_calls=sonnet,
        haiku_calls=haiku,
        total_estimated_calls=total,
        estimated_usd_per_chapter=usd_high,
        estimated_usd_low_per_chapter=usd_low,
        estimated_latency_sec_per_chapter=seq_latency,
    )


def _editorial_agents_out() -> list[EditorialAgentOut]:
    """Read-only roster of the deterministic editorial agents (no model, $0). Purely metadata from
    EDITORIAL_AGENTS -- these never enter model resolution and carry no policy/tier/estimate."""
    return [
        EditorialAgentOut(name=ea.name, label=ea.label, description=ea.description, stage=ea.stage)
        for ea in EDITORIAL_AGENTS
    ]


def _sync_runtime_policies(policy_map: dict[str, AgentPolicyOverride]) -> None:
    load_runtime_policies({k: (v.policy_json or {}) for k, v in policy_map.items()})


async def get_active_preset(session: AsyncSession) -> str | None:
    row = await session.get(AgentOpsState, _OPS_STATE_ID)
    return row.active_preset if row else None


async def set_active_preset(session: AsyncSession, preset_id: str | None) -> None:
    row = await session.get(AgentOpsState, _OPS_STATE_ID)
    if row is None:
        session.add(AgentOpsState(id=_OPS_STATE_ID, active_preset=preset_id))
    else:
        row.active_preset = preset_id


async def load_policy_overrides(session: AsyncSession) -> dict[str, AgentPolicyOverride]:
    rows = (await session.execute(select(AgentPolicyOverride))).scalars().all()
    return {r.setting_name: r for r in rows}


def _slugify_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return (slug or "preset")[:48]


def _custom_preset_id(label: str) -> str:
    return f"{_CUSTOM_PRESET_PREFIX}{_slugify_label(label)}"


async def _load_custom_presets(session: AsyncSession) -> list[AgentCustomPreset]:
    return list((await session.execute(select(AgentCustomPreset).order_by(AgentCustomPreset.label))).scalars())


async def _capture_snapshot(session: AsyncSession) -> dict[str, Any]:
    policy_map = await load_policy_overrides(session)
    tiers: dict[str, str] = {}
    providers: dict[str, str] = {}
    for agent in AGENTS:
        model = getattr(settings, agent.setting_key)
        t = tier_of(model)
        if t:
            tiers[agent.setting_key] = t
            providers[agent.setting_key] = provider_of(model)
    policies = {k: dict(v.policy_json or {}) for k, v in policy_map.items()}
    return {"tiers": tiers, "providers": providers, "policies": policies}


async def _apply_snapshot(session: AsyncSession, snapshot: dict[str, Any]) -> None:
    tiers = snapshot.get("tiers") or {}
    providers = snapshot.get("providers") or {}
    for setting_key, tier in tiers.items():
        provider = providers.get(setting_key, "anthropic")
        if setting_key in {a.setting_key for a in AGENTS} and tier in PROVIDER_TIERS.get(provider, {}):
            await apply_tier_to_agent(session, setting_key, tier, provider)
    policies = snapshot.get("policies") or {}
    for setting_key, pj in policies.items():
        if setting_key not in {a.setting_key for a in AGENTS}:
            continue
        existing = await session.get(AgentPolicyOverride, setting_key)
        if existing is None:
            session.add(AgentPolicyOverride(setting_name=setting_key, policy_json=dict(pj)))
        else:
            existing.policy_json = dict(pj)
        fb_tier = pj.get("fallback_tier")
        if fb_tier:
            fb_provider = pj.get("fallback_provider") or "anthropic"
            attr = FALLBACK_ATTR.get(setting_key)
            if attr:
                setattr(settings, attr, model_for_tier(fb_tier, fb_provider) or "")


def _globals_out(row: AgentOpsState | None) -> AgentGlobalsOut:
    gj = (row.globals_json if row else None) or {}
    return AgentGlobalsOut(
        scene_token_budget=int(gj.get("scene_token_budget", settings.scene_token_budget)),
        scene_time_budget_s=int(gj.get("scene_time_budget_s", settings.scene_time_budget_s)),
    )


def _apply_globals_to_settings(gj: dict[str, Any]) -> None:
    if "scene_token_budget" in gj:
        settings.scene_token_budget = int(gj["scene_token_budget"])
    if "scene_time_budget_s" in gj:
        settings.scene_time_budget_s = int(gj["scene_time_budget_s"])


async def apply_globals(session: AsyncSession, body: AgentGlobalsUpdateIn) -> AgentOpsOut:
    row = await session.get(AgentOpsState, _OPS_STATE_ID)
    merged = dict(row.globals_json or {}) if row else {}
    if body.scene_token_budget is not None:
        if body.scene_token_budget < 5_000 or body.scene_token_budget > 500_000:
            raise ValueError("scene_token_budget must be between 5000 and 500000")
        merged["scene_token_budget"] = body.scene_token_budget
    if body.scene_time_budget_s is not None:
        if body.scene_time_budget_s < 30 or body.scene_time_budget_s > 3600:
            raise ValueError("scene_time_budget_s must be between 30 and 3600")
        merged["scene_time_budget_s"] = body.scene_time_budget_s
    if row is None:
        session.add(AgentOpsState(id=_OPS_STATE_ID, globals_json=merged))
    else:
        row.globals_json = merged
    _apply_globals_to_settings(merged)
    await set_active_preset(session, "custom")
    await session.commit()
    return await build_agent_ops(session)


async def save_custom_preset(session: AsyncSession, body: CustomPresetCreateIn) -> AgentOpsOut:
    label = body.label.strip()
    if not label:
        raise ValueError("label is required")
    preset_id = _custom_preset_id(label)
    existing = await session.get(AgentCustomPreset, preset_id)
    snapshot = await _capture_snapshot(session)
    if existing is None:
        session.add(
            AgentCustomPreset(
                id=preset_id,
                label=label,
                description=body.description,
                snapshot_json=snapshot,
            )
        )
    else:
        existing.label = label
        existing.description = body.description
        existing.snapshot_json = snapshot
    await set_active_preset(session, preset_id)
    await session.commit()
    return await build_agent_ops(session)


async def delete_custom_preset(session: AsyncSession, preset_id: str) -> AgentOpsOut:
    if not preset_id.startswith(_CUSTOM_PRESET_PREFIX):
        raise ValueError("only user presets can be deleted")
    row = await session.get(AgentCustomPreset, preset_id)
    if row is None:
        raise ValueError(f"unknown preset '{preset_id}'")
    await session.delete(row)
    active = await get_active_preset(session)
    if active == preset_id:
        await set_active_preset(session, "custom")
    await session.commit()
    return await build_agent_ops(session)


async def build_agent_ops(session: AsyncSession) -> AgentOpsOut:
    policy_map = await load_policy_overrides(session)
    _sync_runtime_policies(policy_map)
    active = await get_active_preset(session)
    agents = [_agent_ops_row(a, policy_map.get(a.setting_key)) for a in AGENTS]
    ops_row = await session.get(AgentOpsState, _OPS_STATE_ID)
    presets = [
        AgentPresetOut(
            id=p.id,
            label=p.label,
            description=p.description,
            cost_band=p.cost_band,
            latency_band=p.latency_band,
            best_for=p.best_for,
            is_custom=False,
        )
        for p in PRESETS
    ]
    for cp in await _load_custom_presets(session):
        presets.append(
            AgentPresetOut(
                id=cp.id,
                label=cp.label,
                description=cp.description or "Saved custom configuration",
                cost_band="custom",
                latency_band="custom",
                best_for="your saved agent ops snapshot",
                is_custom=True,
            )
        )
    return AgentOpsOut(
        active_preset=active,
        presets=presets,
        agents=agents,
        editorial_agents=_editorial_agents_out(),
        pipeline_estimate=_pipeline_estimate(agents),
        tiers=TIERS,
        provider_tiers=PROVIDER_TIERS,
        globals=_globals_out(ops_row),
    )


def model_setting_out(agent: AgentDefinition) -> ModelSettingOut:
    model = getattr(settings, agent.setting_key)
    return ModelSettingOut(
        setting=agent.setting_key,
        label=agent.label,
        description=agent.description,
        model=model,
        tier=tier_of(model),
        provider=provider_of(model),
    )


async def _merge_policy_hints(session: AsyncSession, hints: dict[str, dict[str, Any]]) -> None:
    """Apply preset policy hints into persisted AgentPolicyOverride rows."""
    role_keys = {a.setting_key for a in AGENTS}
    for setting_key, hint in hints.items():
        if setting_key not in role_keys:
            continue
        existing = await session.get(AgentPolicyOverride, setting_key)
        merged = {**(existing.policy_json if existing else {}), **hint}
        fb_tier = hint.get("fallback_tier")
        if fb_tier is not None:
            attr = FALLBACK_ATTR.get(setting_key)
            if fb_tier:
                # Hints only name a tier (like preset.tiers) -- resolve it against this agent's
                # CURRENT provider rather than defaulting to Anthropic, so a fallback hint doesn't
                # undo a provider the user picked on this agent.
                fb_provider = provider_of(getattr(settings, setting_key))
                fb_tier = resolve_tier_for_provider(fb_tier, fb_provider)
                merged["fallback_tier"] = fb_tier
                merged["fallback_provider"] = fb_provider
                if attr:
                    setattr(settings, attr, model_for_tier(fb_tier, fb_provider) or "")
            else:
                merged["fallback_provider"] = None
                if attr:
                    setattr(settings, attr, "")
        if existing is None:
            session.add(AgentPolicyOverride(setting_name=setting_key, policy_json=merged))
        else:
            existing.policy_json = merged


async def apply_tier_to_agent(
    session: AsyncSession, setting_key: str, tier: str, provider: str = "anthropic"
) -> ModelSettingOut:
    model = model_for_tier(tier, provider)
    if model is None:
        raise ValueError(f"provider '{provider}' has no model for tier '{tier}'")
    setattr(settings, setting_key, model)
    existing = await session.get(ModelOverride, setting_key)
    if existing is None:
        session.add(ModelOverride(setting_name=setting_key, model=model))
    else:
        existing.model = model
    agent = next(a for a in AGENTS if a.setting_key == setting_key)
    return model_setting_out(agent)


async def apply_preset(session: AsyncSession, preset_id: str) -> AgentOpsOut:
    if preset_id in BUILTIN_PRESET_IDS:
        preset = PRESET_BY_ID[preset_id]
        for setting_key, tier in preset.tiers.items():
            # Built-in presets only name a tier (quality level), never a provider -- resolve it
            # against each agent's CURRENT provider so applying a preset changes quality without
            # silently reverting an agent the user put on openai/xai back to Anthropic. When that
            # provider doesn't ship the exact tier (e.g. xAI only has "opus" today), resolve to its
            # nearest available tier instead of erroring or switching provider.
            current_provider = provider_of(getattr(settings, setting_key))
            resolved_tier = resolve_tier_for_provider(tier, current_provider)
            await apply_tier_to_agent(session, setting_key, resolved_tier, current_provider)
        if preset.policy_hints:
            await _merge_policy_hints(session, preset.policy_hints)
    elif preset_id.startswith(_CUSTOM_PRESET_PREFIX):
        row = await session.get(AgentCustomPreset, preset_id)
        if row is None:
            raise ValueError(f"unknown preset '{preset_id}'")
        await _apply_snapshot(session, row.snapshot_json)
    else:
        raise ValueError(f"unknown preset '{preset_id}'")
    await set_active_preset(session, preset_id)
    await session.commit()
    return await build_agent_ops(session)


async def apply_agent_policy(
    session: AsyncSession,
    setting_key: str,
    *,
    fallback_tier: str | None = None,
    fallback_provider: str | None = None,
    never_fallback: list[str] | None = None,
    semantic_escalation: bool | None = None,
    quality_level: str | None = None,
    backend: str | None = None,
    permissions: AgentPermissionsPatchIn | None = None,
) -> AgentOpsOut:
    agent = next((a for a in AGENTS if a.setting_key == setting_key), None)
    if agent is None:
        raise ValueError(f"unknown agent setting '{setting_key}'")

    existing = await session.get(AgentPolicyOverride, setting_key)
    policy_json: dict[str, Any] = {}
    if fallback_tier is not None:
        fb_model = ""
        if fallback_tier:
            if not fallback_provider:
                # Caller named a tier without pinning a provider -- default to this agent's CURRENT
                # primary provider (not hardcoded Anthropic), so picking "just a fallback tier"
                # preserves whatever provider the agent is already on. An explicit, wrong provider
                # (below) still 422s rather than being silently substituted.
                fallback_provider = provider_of(getattr(settings, setting_key))
            if fallback_provider not in PROVIDER_TIERS:
                raise ValueError(f"unknown provider '{fallback_provider}'")
            if fallback_tier not in PROVIDER_TIERS[fallback_provider]:
                raise ValueError(f"provider '{fallback_provider}' has no model for tier '{fallback_tier}'")
            # fallback_provider is a concrete str here (narrowed above), so model_for_tier is well-typed.
            fb_model = model_for_tier(fallback_tier, fallback_provider) or ""
        policy_json["fallback_tier"] = fallback_tier or None
        policy_json["fallback_provider"] = fallback_provider if fallback_tier else None
        attr = FALLBACK_ATTR.get(setting_key)
        if attr:
            setattr(settings, attr, fb_model)
    if never_fallback is not None:
        policy_json["never_fallback"] = never_fallback
    if semantic_escalation is not None:
        policy_json["semantic_escalation"] = semantic_escalation
    if quality_level is not None:
        if quality_level not in ("fast", "balanced", "quality"):
            raise ValueError("quality_level must be fast, balanced, or quality")
        policy_json["quality_level"] = quality_level
    if backend is not None:
        if backend not in ("llm", "agent_cli"):
            raise ValueError("backend must be llm or agent_cli")
        # The CLI can only drive an Anthropic model — guard here so the role can't be flipped to the
        # agent_cli backend while pointed at an OpenAI/xAI/Gemini model (the CLI has no such model).
        if backend == "agent_cli" and provider_of(getattr(settings, setting_key)) != "anthropic":
            raise ValueError("agent_cli backend requires an Anthropic model for this role")
        policy_json["backend"] = backend
    if permissions is not None:
        perm_patch = permissions.model_dump(exclude_none=True)
        if perm_patch:
            existing_perms = (existing.policy_json if existing else {}).get("permissions") or {}
            policy_json["permissions"] = {**existing_perms, **perm_patch}

    merged = {**(existing.policy_json if existing else {}), **policy_json}
    if existing is None:
        session.add(AgentPolicyOverride(setting_name=setting_key, policy_json=merged))
    else:
        existing.policy_json = merged

    await set_active_preset(session, "custom")
    await session.commit()
    return await build_agent_ops(session)


async def apply_model_overrides(session: AsyncSession) -> int:
    """Load persisted primary models + policy overrides into live settings."""
    from dominion.shared.agent_registry import ROLE_KEYS

    rows = (await session.execute(select(ModelOverride))).scalars().all()
    applied = 0
    for row in rows:
        if row.setting_name in ROLE_KEYS:
            setattr(settings, row.setting_name, row.model)
            applied += 1

    policy_rows = (await session.execute(select(AgentPolicyOverride))).scalars().all()
    policy_map = {r.setting_name: r for r in policy_rows}
    for row in policy_rows:
        if row.setting_name not in ROLE_KEYS:
            continue
        pj = row.policy_json or {}
        fb_tier = pj.get("fallback_tier")
        if fb_tier:
            fb_provider = pj.get("fallback_provider") or "anthropic"
            attr = FALLBACK_ATTR.get(row.setting_name)
            if attr:
                setattr(settings, attr, model_for_tier(fb_tier, fb_provider) or "")
        applied += 1
    _sync_runtime_policies(policy_map)
    ops_row = await session.get(AgentOpsState, _OPS_STATE_ID)
    if ops_row and ops_row.globals_json:
        _apply_globals_to_settings(ops_row.globals_json)
        applied += 1
    return applied


def _format_pass_rate(passed: int, total: int) -> str | None:
    if total == 0:
        return None
    return f"{round(100 * passed / total)}%"


async def _qa_pass_rates(session: AsyncSession, cutoff: datetime) -> dict[str, str | None]:
    """Join verdict / approval tables for per-agent pass rates in the stats window."""
    packet_verdicts = list(
        (await session.execute(select(ChapterPacket.qa_verdict).where(ChapterPacket.created_at >= cutoff))).scalars()
    )
    scene_verdicts = list(
        (await session.execute(select(ScenePacket.qa_verdict).where(ScenePacket.created_at >= cutoff))).scalars()
    )
    approvals = list((await session.execute(select(Approval.decision).where(Approval.decided_at >= cutoff))).scalars())
    scene_ids = list((await session.execute(select(Scene.id).where(Scene.created_at >= cutoff))).scalars())
    hard_by_scene: set[Any] = set()
    if scene_ids:
        hard_rows = (
            await session.execute(
                select(Critique.scene_id).where(
                    Critique.scene_id.in_(scene_ids),
                    Critique.severity.in_(("hard", "block")),
                    Critique.reviewer.notin_(("length",)),
                )
            )
        ).scalars()
        hard_by_scene = set(hard_rows)

    packet_pass = sum(1 for v in packet_verdicts if v and v in _PASS_VERDICTS)
    scene_pass = sum(1 for v in scene_verdicts if v and v in _PASS_VERDICTS)
    draft_pass = sum(1 for d in approvals if d == "approve")
    review_pass = len(scene_ids) - len(hard_by_scene) if scene_ids else 0

    return {
        "packet_qa_model": _format_pass_rate(packet_pass, len([v for v in packet_verdicts if v])),
        "scene_packet_qa_model": _format_pass_rate(scene_pass, len([v for v in scene_verdicts if v])),
        "draft_model": _format_pass_rate(draft_pass, len(approvals)),
        "review_model": _format_pass_rate(review_pass, len(scene_ids)),
    }


async def build_agent_stats(session: AsyncSession) -> AgentStatsListOut:
    from dominion.shared.agent_registry import STAGE_TO_SETTING

    cutoff = datetime.now(UTC) - timedelta(days=7)
    rows = list(
        (
            await session.execute(
                select(LlmCall).where(LlmCall.created_at >= cutoff).order_by(LlmCall.created_at.desc())
            )
        ).scalars()
    )
    run_ids: list[Any] = []
    seen: set[Any] = set()
    for r in rows:
        if r.run_id and r.run_id not in seen:
            seen.add(r.run_id)
            run_ids.append(r.run_id)
        if len(run_ids) >= 20:
            break
    if run_ids:
        rows = [r for r in rows if r.run_id in run_ids]

    by_setting: dict[str, list[LlmCall]] = {a.setting_key: [] for a in AGENTS}
    for call in rows:
        sk = STAGE_TO_SETTING.get(call.stage)
        if sk:
            by_setting.setdefault(sk, []).append(call)

    pass_rates = await _qa_pass_rates(session, cutoff)

    stats: list[AgentStatsOut] = []
    for agent in AGENTS:
        calls = by_setting.get(agent.setting_key, [])
        n = len(calls)
        qa_rate = pass_rates.get(agent.setting_key)
        if n == 0:
            stats.append(AgentStatsOut(setting=agent.setting_key, label=agent.label, qa_pass_rate=qa_rate or "—"))
            continue
        latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
        tokens = [c.input_tokens + c.output_tokens for c in calls]
        escalations = sum(1 for c in calls if (c.metadata_ or {}).get("fallback_attempt"))
        errors = sum(1 for c in calls if c.error)
        truncs = sum(1 for c in calls if c.truncated)
        stats.append(
            AgentStatsOut(
                setting=agent.setting_key,
                label=agent.label,
                calls=n,
                avg_latency_ms=int(sum(latencies) / len(latencies)) if latencies else None,
                avg_tokens=int(sum(tokens) / len(tokens)) if tokens else None,
                escalation_rate=round(escalations / n, 3),
                error_rate=round(errors / n, 3),
                truncation_rate=round(truncs / n, 3),
                qa_pass_rate=qa_rate or "—",
            )
        )
    return AgentStatsListOut(agents=stats, window_runs=len(run_ids))
