"""Runtime agent policy resolved from registry defaults + persisted overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dominion.shared.agent_registry import AGENT_BY_KEY, AGENTS, AgentDefinition, AgentPermissions

QualityLevel = Literal["fast", "balanced", "quality"]
AgentBackend = Literal["llm", "agent_cli"]
_BACKENDS: frozenset[str] = frozenset({"llm", "agent_cli"})

# temperature steers older / Haiku Anthropic + OpenAI/xAI; effort (output_config.effort) steers the
# Anthropic flagship models that dropped sampling params (Opus 4.7+, Sonnet 5, Fable 5). llm.complete
# picks whichever the target model accepts, so a preset's quality_level lands as a real knob either way.
QUALITY_PROFILES: dict[QualityLevel, dict[str, Any]] = {
    "fast": {
        "temperature": 0.9,
        "effort": "low",
        "prompt_suffix": " Favor speed; minor polish gaps are acceptable.",
    },
    "balanced": {"temperature": 0.7, "effort": "medium", "prompt_suffix": ""},
    "quality": {
        "temperature": 0.5,
        "effort": "high",
        "prompt_suffix": " Take extra care with canon fidelity, voice consistency, and contract boundaries.",
    },
}

_RUNTIME: dict[str, ResolvedAgentPolicy] = {}


@dataclass(frozen=True)
class ResolvedAgentPolicy:
    setting_key: str
    auto_run: bool
    require_approval: bool
    can_modify_packet: bool
    can_block_downstream: bool
    can_write_summaries: bool
    can_update_canon: bool
    can_only_suggest: bool
    quality_level: QualityLevel
    semantic_escalation: bool
    # "llm" (default) calls the Anthropic/OpenAI HTTP API; "agent_cli" routes this role's generation
    # through the Claude Code CLI subprocess (workers/agent_cli.py). Orthogonal to the model choice.
    backend: AgentBackend
    temperature: float
    effort: str  # Anthropic output_config.effort (low/medium/high) for models that reject `temperature`
    prompt_suffix: str
    never_fallback_tiers: frozenset[str]


def _permissions_from_json(agent: AgentDefinition, pj: dict[str, Any]) -> AgentPermissions:
    base = agent.permissions
    perm = pj.get("permissions") or {}
    if not isinstance(perm, dict):
        return base
    return AgentPermissions(
        auto_run=bool(perm.get("auto_run", base.auto_run)),
        require_approval=bool(perm.get("require_approval", base.require_approval)),
        can_modify_packet=bool(perm.get("can_modify_packet", base.can_modify_packet)),
        can_block_downstream=bool(perm.get("can_block_downstream", base.can_block_downstream)),
        can_write_summaries=bool(perm.get("can_write_summaries", base.can_write_summaries)),
        can_update_canon=bool(perm.get("can_update_canon", base.can_update_canon)),
        can_only_suggest=bool(perm.get("can_only_suggest", base.can_only_suggest)),
    )


def resolve_policy(agent: AgentDefinition, policy_json: dict[str, Any] | None) -> ResolvedAgentPolicy:
    pj = policy_json or {}
    perms = _permissions_from_json(agent, pj)
    ql = pj.get("quality_level", "balanced")
    if ql not in QUALITY_PROFILES:
        ql = "balanced"
    profile = QUALITY_PROFILES[ql]  # type: ignore[index]
    never_fb = pj.get("never_fallback")
    if never_fb is None:
        never_set = frozenset(agent.never_fallback_tiers)
    else:
        never_set = frozenset(str(t) for t in never_fb)
    semantic_default = agent.setting_key in ("packet_qa_model", "scene_packet_qa_model", "review_model")
    backend = pj.get("backend", "llm")
    if backend not in _BACKENDS:
        backend = "llm"
    return ResolvedAgentPolicy(
        setting_key=agent.setting_key,
        auto_run=perms.auto_run,
        require_approval=perms.require_approval,
        can_modify_packet=perms.can_modify_packet,
        can_block_downstream=perms.can_block_downstream,
        can_write_summaries=perms.can_write_summaries,
        can_update_canon=perms.can_update_canon,
        can_only_suggest=perms.can_only_suggest,
        quality_level=ql,  # type: ignore[arg-type]
        semantic_escalation=bool(pj.get("semantic_escalation", semantic_default)),
        backend=backend,  # type: ignore[arg-type]
        temperature=float(profile["temperature"]),
        effort=str(profile["effort"]),
        prompt_suffix=str(profile.get("prompt_suffix", "")),
        never_fallback_tiers=never_set,
    )


def load_runtime_policies(policy_rows: dict[str, dict[str, Any]]) -> None:
    """Rebuild the in-process policy cache (called on startup and after policy API updates)."""
    _RUNTIME.clear()
    for agent in AGENTS:
        pj = policy_rows.get(agent.setting_key) or {}
        _RUNTIME[agent.setting_key] = resolve_policy(agent, pj)


def get_runtime_policy(setting_key: str) -> ResolvedAgentPolicy:
    cached = _RUNTIME.get(setting_key)
    if cached is not None:
        return cached
    agent = AGENT_BY_KEY.get(setting_key)
    if agent is None:
        raise KeyError(setting_key)
    return resolve_policy(agent, None)


def agent_auto_run(setting_key: str) -> bool:
    return get_runtime_policy(setting_key).auto_run


def agent_backend(setting_key: str) -> str:
    """The resolved generation backend for a role ("llm" or "agent_cli"). Unknown keys fall back to
    "llm" so a caller threading an unregistered setting_key never breaks — the safe, current behavior."""
    try:
        return get_runtime_policy(setting_key).backend
    except KeyError:
        return "llm"


def quality_temperature(setting_key: str) -> float:
    return get_runtime_policy(setting_key).temperature


def quality_effort(setting_key: str) -> str:
    return get_runtime_policy(setting_key).effort


def quality_prompt_suffix(setting_key: str) -> str:
    return get_runtime_policy(setting_key).prompt_suffix
