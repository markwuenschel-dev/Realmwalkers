"""Single source of truth for agent roles, contracts, presets, and telemetry stage mapping.

The Agent Operations panel and settings API read from here instead of duplicating ROLES/tier maps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from dominion.shared.reviewer_telemetry import LEGACY_REVIEWERS_STAGE, REVIEWER_TELEMETRY_STAGES

Tier = Literal["haiku", "sonnet", "opus"]
Provider = Literal["anthropic", "openai", "google", "xai"]
CostBand = Literal["low", "medium", "high"]
SpeedBand = Literal["fast", "medium", "slow"]

# Per-provider tier -> model id. Anthropic covers all three tiers; other providers fill in whichever
# tiers they have a model for (xAI currently ships one general model, slotted at "opus").
# Typed as plain str keys (not Provider/Tier) so it assigns directly into the dict[str, dict[str, str]]
# API schemas below without invariance friction.
PROVIDER_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "haiku": "claude-haiku-4-5",
        "sonnet": "claude-sonnet-5",
        "opus": "claude-opus-4-8",
    },
    "openai": {
        "haiku": "gpt-5.4-nano",
        "sonnet": "gpt-5.4-mini",
        "opus": "gpt-5.5",
    },
    "google": {
        "sonnet": "gemini-3.5-flash",
        "opus": "gemini-3.1-pro-preview",
    },
    "xai": {
        "opus": "grok-4.3",
    },
}

PROVIDER_LABELS: dict[Provider, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "xai": "xAI",
}

# Legacy alias: the settings API predates multi-provider support and only ever resolved tiers
# against Anthropic models. Keep it pointing at the Anthropic tiers for backward compatibility.
TIERS: dict[str, str] = PROVIDER_TIERS["anthropic"]

_MODEL_TO_PROVIDER_TIER: dict[str, tuple[str, str]] = {
    model: (provider, tier) for provider, tiers in PROVIDER_TIERS.items() for tier, model in tiers.items()
}

# Maps primary `settings` attribute -> fallback `settings` attribute.
FALLBACK_ATTR: dict[str, str] = {
    "draft_model": "draft_fallback_model",
    "review_model": "review_fallback_model",
    "enrich_model": "enrich_fallback_model",
    "packet_author_model": "packet_author_fallback_model",
    "packet_qa_model": "packet_qa_fallback_model",
    "scene_packet_author_model": "scene_packet_author_fallback_model",
    "scene_packet_qa_model": "scene_packet_qa_fallback_model",
}

STRUCTURAL_ESCALATION_TRIGGERS: tuple[str, ...] = ("truncated", "unparseable")
SEMANTIC_ESCALATION_TRIGGERS: tuple[str, ...] = (
    "canon_conflict",
    "high_qa_risk",
    "reviewer_hard_flags",
)
QA_ESCALATION_TRIGGERS: tuple[str, ...] = STRUCTURAL_ESCALATION_TRIGGERS + SEMANTIC_ESCALATION_TRIGGERS


@dataclass(frozen=True)
class AgentContract:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    temperature: float | None = None
    max_retries: int = 3
    context_load: str = ""
    uses_memory: bool = False
    writes_artifacts: bool = False
    requires_approval: bool = False


@dataclass(frozen=True)
class AgentPermissions:
    auto_run: bool = True
    require_approval: bool = False
    can_modify_packet: bool = False
    can_block_downstream: bool = False
    can_write_summaries: bool = False
    can_update_canon: bool = False
    can_only_suggest: bool = True


@dataclass(frozen=True)
class AgentEstimate:
    cost_band: CostBand
    speed_band: SpeedBand
    typical_calls_per_chapter: int


@dataclass(frozen=True)
class AgentDefinition:
    setting_key: str
    label: str
    description: str
    stages: tuple[str, ...]
    contract: AgentContract
    permissions: AgentPermissions
    default_primary_tier: Tier
    default_fallback_tier: Tier | None
    never_fallback_tiers: tuple[Tier, ...] = ()
    escalation_triggers: tuple[str, ...] = STRUCTURAL_ESCALATION_TRIGGERS
    estimate: AgentEstimate = field(
        default_factory=lambda: AgentEstimate(cost_band="medium", speed_band="medium", typical_calls_per_chapter=1)
    )


@dataclass(frozen=True)
class AgentPreset:
    id: str
    label: str
    description: str
    cost_band: CostBand
    latency_band: SpeedBand
    best_for: str
    tiers: dict[str, Tier]
    policy_hints: dict[str, dict[str, Any]] = field(default_factory=dict)


AGENTS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        setting_key="draft_model",
        label="Drafter & planner",
        description="Writes scene prose and proposes the gate-1 beats",
        stages=("drafter", "beats", "chapter_title"),
        contract=AgentContract(
            inputs=("chapter outline", "approved packet", "prior summaries", "beat contract"),
            outputs=("scene prose", "gate-1 beats", "chapter title"),
            context_load="Full packet + outline",
            uses_memory=True,
            writes_artifacts=True,
            requires_approval=True,
        ),
        permissions=AgentPermissions(
            require_approval=True,
            can_only_suggest=False,
        ),
        default_primary_tier="sonnet",
        default_fallback_tier="haiku",
        never_fallback_tiers=(),
        estimate=AgentEstimate(cost_band="high", speed_band="slow", typical_calls_per_chapter=14),
    ),
    AgentDefinition(
        setting_key="review_model",
        label="Reviewers & summaries",
        description="Continuity / combat / pacing / voice reviewers + rolling summaries",
        stages=(LEGACY_REVIEWERS_STAGE, *REVIEWER_TELEMETRY_STAGES, "summary"),
        contract=AgentContract(
            inputs=("draft prose", "scene packet", "canon context"),
            outputs=("advisory critiques", "rolling summaries"),
            context_load="Draft + packet",
            uses_memory=True,
            writes_artifacts=True,
        ),
        permissions=AgentPermissions(can_block_downstream=False, can_write_summaries=True),
        default_primary_tier="haiku",
        default_fallback_tier="sonnet",
        never_fallback_tiers=("haiku",),
        estimate=AgentEstimate(cost_band="low", speed_band="fast", typical_calls_per_chapter=60),
    ),
    AgentDefinition(
        setting_key="enrich_model",
        label="Enrichment specialists",
        description="Combat / sensory / dialogue enrichment passes",
        stages=("enrichment",),
        contract=AgentContract(
            inputs=("draft spine", "scene tags"),
            outputs=("enriched prose layers",),
            context_load="Draft spine",
            writes_artifacts=True,
        ),
        permissions=AgentPermissions(can_only_suggest=False),
        default_primary_tier="haiku",
        default_fallback_tier="sonnet",
        estimate=AgentEstimate(cost_band="low", speed_band="fast", typical_calls_per_chapter=36),
    ),
    AgentDefinition(
        setting_key="packet_author_model",
        label="Packet author",
        description="Authors the chapter knowledge packet from canon + outline",
        stages=("packet_author",),
        contract=AgentContract(
            inputs=("canon snippets", "chapter outline", "prior chapter state"),
            outputs=("chapter knowledge packet JSON",),
            context_load="Canon + outline",
            writes_artifacts=True,
            requires_approval=True,
        ),
        permissions=AgentPermissions(can_modify_packet=True, can_only_suggest=True),
        default_primary_tier="sonnet",
        default_fallback_tier="opus",
        estimate=AgentEstimate(cost_band="medium", speed_band="medium", typical_calls_per_chapter=1),
    ),
    AgentDefinition(
        setting_key="packet_qa_model",
        label="Packet QA",
        description="Validates the proposed packet before approval",
        stages=("packet_qa",),
        contract=AgentContract(
            inputs=("proposed packet",),
            outputs=("verdict JSON", "residual risks"),
            context_load="Packet only",
            requires_approval=True,
        ),
        permissions=AgentPermissions(can_block_downstream=True, can_only_suggest=True),
        default_primary_tier="haiku",
        default_fallback_tier="sonnet",
        escalation_triggers=QA_ESCALATION_TRIGGERS,
        estimate=AgentEstimate(cost_band="low", speed_band="fast", typical_calls_per_chapter=1),
    ),
    AgentDefinition(
        setting_key="scene_packet_author_model",
        label="ScenePacket author",
        description="Localizes the chapter packet into each scene's reader/POV/reveal contract (once per scene)",
        stages=("scene_packet_author", "scene_packet_author_prefix_prime"),
        contract=AgentContract(
            inputs=("chapter packet", "scene seed", "word budget"),
            outputs=("scene packet JSON",),
            context_load="Packet + scene seed",
            writes_artifacts=True,
            requires_approval=True,
        ),
        permissions=AgentPermissions(can_modify_packet=True, can_only_suggest=True),
        default_primary_tier="haiku",
        default_fallback_tier="sonnet",
        estimate=AgentEstimate(cost_band="low", speed_band="fast", typical_calls_per_chapter=12),
    ),
    AgentDefinition(
        setting_key="scene_packet_qa_model",
        label="ScenePacket QA",
        description="Attacks each scene packet before approval (once per scene)",
        stages=("scene_packet_qa", "scene_packet_qa_prefix_prime"),
        contract=AgentContract(
            inputs=("scene packet", "chapter packet"),
            outputs=("verdict JSON", "issues"),
            context_load="Packet only",
            requires_approval=True,
        ),
        permissions=AgentPermissions(can_block_downstream=True, can_only_suggest=True),
        default_primary_tier="haiku",
        default_fallback_tier="sonnet",
        escalation_triggers=QA_ESCALATION_TRIGGERS,
        estimate=AgentEstimate(cost_band="low", speed_band="fast", typical_calls_per_chapter=12),
    ),
)

AGENT_BY_KEY: dict[str, AgentDefinition] = {a.setting_key: a for a in AGENTS}
ROLE_KEYS: frozenset[str] = frozenset(AGENT_BY_KEY)

STAGE_TO_SETTING: dict[str, str] = {}
for _agent in AGENTS:
    for _stage in _agent.stages:
        STAGE_TO_SETTING[_stage] = _agent.setting_key

PRESETS: tuple[AgentPreset, ...] = (
    AgentPreset(
        id="fast_drafting",
        label="Fast Drafting",
        description="Cheap reviewers and enrichment; Opus only for the drafter.",
        cost_band="medium",
        latency_band="fast",
        best_for="iteration drafts / speed over polish",
        tiers={
            "draft_model": "opus",
            "review_model": "haiku",
            "enrich_model": "haiku",
            "packet_author_model": "sonnet",
            "packet_qa_model": "haiku",
            "scene_packet_author_model": "haiku",
            "scene_packet_qa_model": "haiku",
        },
        policy_hints={
            "draft_model": {"quality_level": "fast"},
            "review_model": {"semantic_escalation": False},
        },
    ),
    AgentPreset(
        id="high_quality_chapter",
        label="High Quality Chapter",
        description="Opus drafter, Sonnet reviewers and packet QA.",
        cost_band="high",
        latency_band="medium",
        best_for="final pass / canon-sensitive chapters",
        tiers={
            "draft_model": "opus",
            "review_model": "sonnet",
            "enrich_model": "haiku",
            "packet_author_model": "sonnet",
            "packet_qa_model": "sonnet",
            "scene_packet_author_model": "haiku",
            "scene_packet_qa_model": "sonnet",
        },
        policy_hints={
            "draft_model": {"quality_level": "quality"},
            "review_model": {"semantic_escalation": True, "quality_level": "quality"},
            "packet_qa_model": {"semantic_escalation": True},
            "scene_packet_qa_model": {"semantic_escalation": True},
        },
    ),
    AgentPreset(
        id="continuity_audit",
        label="Continuity Audit",
        description="Prioritize reviewers, packet QA, and ScenePacket QA.",
        cost_band="medium",
        latency_band="medium",
        best_for="continuity risk / reveal-heavy chapters",
        tiers={
            "draft_model": "sonnet",
            "review_model": "sonnet",
            "enrich_model": "haiku",
            "packet_author_model": "sonnet",
            "packet_qa_model": "sonnet",
            "scene_packet_author_model": "haiku",
            "scene_packet_qa_model": "sonnet",
        },
        policy_hints={
            "review_model": {"semantic_escalation": True, "quality_level": "quality"},
            "packet_qa_model": {"semantic_escalation": True},
            "scene_packet_qa_model": {"semantic_escalation": True},
        },
    ),
    AgentPreset(
        id="budget_mode",
        label="Budget Mode",
        description="Haiku/Sonnet everywhere; Sonnet for planner and packet author only.",
        cost_band="low",
        latency_band="fast",
        best_for="exploratory chapters / cost control",
        tiers={
            "draft_model": "sonnet",
            "review_model": "haiku",
            "enrich_model": "haiku",
            "packet_author_model": "sonnet",
            "packet_qa_model": "haiku",
            "scene_packet_author_model": "haiku",
            "scene_packet_qa_model": "haiku",
        },
        policy_hints={
            "draft_model": {"quality_level": "fast"},
            "review_model": {"semantic_escalation": False},
        },
    ),
)

PRESET_BY_ID: dict[str, AgentPreset] = {p.id: p for p in PRESETS}
BUILTIN_PRESET_IDS: frozenset[str] = frozenset(PRESET_BY_ID)


def provider_and_tier_of(model_id: str | None) -> tuple[str, str] | None:
    """Which (provider, tier) a configured model id belongs to.

    Checks the exact catalog first, then falls back to substring-matching a tier name against
    Anthropic model ids (covers dated ids like "claude-haiku-4-5-20251001" that predate the catalog).
    """
    hit = _MODEL_TO_PROVIDER_TIER.get(model_id or "")
    if hit is not None:
        return hit
    for tier in ("opus", "sonnet", "haiku"):
        if tier in (model_id or ""):
            return ("anthropic", tier)
    return None


def tier_of(model_id: str | None) -> str | None:
    """Which tier a configured model id belongs to (by family)."""
    hit = provider_and_tier_of(model_id)
    return hit[1] if hit else None


def provider_of(model_id: str | None) -> str:
    """Which provider a configured model id belongs to. Unknown ids default to Anthropic."""
    hit = provider_and_tier_of(model_id)
    return hit[0] if hit else "anthropic"


def model_for_tier(tier: str, provider: str = "anthropic") -> str | None:
    return PROVIDER_TIERS.get(provider, {}).get(tier)


_TIER_RANK: dict[str, int] = {"haiku": 0, "sonnet": 1, "opus": 2}


def resolve_tier_for_provider(tier: str, provider: str) -> str:
    """Nearest tier a given provider can actually serve.

    Providers don't all cover the same tiers (xAI ships one model today, slotted at "opus"). When
    `provider` has `tier` exactly, it's returned unchanged. Otherwise this resolves to that
    provider's nearest tier by quality rank (haiku < sonnet < opus), rounding UP on a tie, so a
    provider with partial coverage still gets a sensible, same-provider model instead of an error
    or a silent switch to a different provider.

    This is for automatic resolution paths (built-in presets, preset policy hints) where only a
    tier name is known and no human is in the loop to pick a provider. Direct, human-driven picks
    (`set_model`, an explicit `apply_agent_policy` fallback) validate the exact (tier, provider)
    pair instead and never call this — an explicit request for a combination the provider doesn't
    have should 422, not get silently substituted.
    """
    available = PROVIDER_TIERS.get(provider) or {}
    if tier in available:
        return tier
    if not available:
        return tier
    target = _TIER_RANK.get(tier, 1)
    return min(available, key=lambda t: (abs(_TIER_RANK.get(t, 1) - target), -_TIER_RANK.get(t, 1)))


# Anthropic removed the sampling params (temperature/top_p/top_k) on its flagship models: Opus 4.7+,
# Sonnet 5, and Fable 5 return a 400 ("temperature is deprecated for this model") if `temperature` is
# sent. Only older / Haiku Anthropic models still accept it. This is an ALLOWLIST on purpose: any
# Anthropic model NOT listed (including future ones) is treated as not accepting temperature, so a
# model upgrade can never silently re-introduce the 400.
_ANTHROPIC_TEMPERATURE_MODELS: frozenset[str] = frozenset({"claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6"})


def supports_temperature(model: str | None) -> bool:
    """Whether `model` accepts the `temperature` sampling parameter.

    OpenAI and xAI models accept it. Anthropic accepts it only on the older / Haiku models in
    `_ANTHROPIC_TEMPERATURE_MODELS`; its flagship models (Opus 4.7+, Sonnet 5, Fable 5) 400 on it, so
    callers must omit `temperature` for those and steer via prompt / effort instead.
    """
    if not model:
        return False
    if provider_of(model) != "anthropic":
        return True
    base = model.split("-20", 1)[0]  # tolerate dated ids like "claude-haiku-4-5-20251001"
    return base in _ANTHROPIC_TEMPERATURE_MODELS


# output_config.effort (low/medium/high/…) is Anthropic-only and supported on Opus 4.5+, Sonnet 5,
# Sonnet 4.6, and Fable 5 -- but NOT Haiku 4.5 or Sonnet 4.5 (those 400). Allowlist so unknown / future
# models default to no effort (safe: the model just runs at its own default effort). It's the complement
# of the temperature allowlist above: the flagship models reject `temperature` and take effort instead.
_ANTHROPIC_EFFORT_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-fable-5",
    }
)


def supports_effort(model: str | None) -> bool:
    """Whether `model` accepts the Anthropic `output_config.effort` param (low/medium/high/…). Non-
    Anthropic providers don't take this param on the app's chat path, so they're always False."""
    if not model:
        return False
    if provider_of(model) != "anthropic":
        return False
    base = model.split("-20", 1)[0]
    return base in _ANTHROPIC_EFFORT_MODELS


def fallback_attr(setting_key: str) -> str | None:
    return FALLBACK_ATTR.get(setting_key)


@dataclass(frozen=True)
class _CapabilityWarningContext:
    setting_key: str
    primary_tier: str | None
    fallback_tier: str | None
    semantic_escalation: bool | None = None


def _warn_review_haiku(ctx: _CapabilityWarningContext) -> bool:
    return ctx.setting_key == "review_model" and ctx.primary_tier == "haiku"


def _warn_packet_qa_opus_no_fallback(ctx: _CapabilityWarningContext) -> bool:
    return ctx.setting_key == "packet_qa_model" and ctx.primary_tier == "opus" and not ctx.fallback_tier


def _warn_scene_packet_author_haiku(ctx: _CapabilityWarningContext) -> bool:
    return ctx.setting_key == "scene_packet_author_model" and ctx.primary_tier == "haiku"


def _warn_draft_sonnet(ctx: _CapabilityWarningContext) -> bool:
    return ctx.setting_key == "draft_model" and ctx.primary_tier == "sonnet"


def _warn_scene_packet_qa_haiku_no_semantic(ctx: _CapabilityWarningContext) -> bool:
    return (
        ctx.setting_key == "scene_packet_qa_model" and ctx.primary_tier == "haiku" and ctx.semantic_escalation is False
    )


_CAPABILITY_WARNING_RULES: tuple[tuple[Callable[[_CapabilityWarningContext], bool], str], ...] = (
    (_warn_review_haiku, "Haiku may miss cross-scene continuity issues for long chapters."),
    (
        _warn_packet_qa_opus_no_fallback,
        "Opus is probably unnecessary for packet validation unless escalation is enabled.",
    ),
    (
        _warn_scene_packet_author_haiku,
        "Haiku is default for per-scene contracts; bump to Sonnet for high canon density.",
    ),
    (_warn_draft_sonnet, "May need more passes than Opus for voice lock-in."),
    (
        _warn_scene_packet_qa_haiku_no_semantic,
        "Structural-only QA; canon conflicts may slip through.",
    ),
)


def capability_warnings(
    setting_key: str,
    primary_tier: str | None,
    fallback_tier: str | None,
    *,
    semantic_escalation: bool | None = None,
) -> list[str]:
    """Lightweight advisory strings for the ops panel."""
    ctx = _CapabilityWarningContext(
        setting_key=setting_key,
        primary_tier=primary_tier,
        fallback_tier=fallback_tier,
        semantic_escalation=semantic_escalation,
    )
    return [message for predicate, message in _CAPABILITY_WARNING_RULES if predicate(ctx)]
