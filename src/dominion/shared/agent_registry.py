"""Single source of truth for agent roles, contracts, presets, and telemetry stage mapping.

The Agent Operations panel and settings API read from here instead of duplicating ROLES/tier maps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Tier = Literal["haiku", "sonnet", "opus"]
CostBand = Literal["low", "medium", "high"]
SpeedBand = Literal["fast", "medium", "slow"]

TIERS: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
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
        stages=("reviewers", "summary"),
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
    ),
)

PRESET_BY_ID: dict[str, AgentPreset] = {p.id: p for p in PRESETS}
BUILTIN_PRESET_IDS: frozenset[str] = frozenset(PRESET_BY_ID)


def tier_of(model_id: str | None) -> Tier | None:
    """Which tier a configured model id belongs to (by family)."""
    for tier in ("opus", "sonnet", "haiku"):
        if tier in (model_id or ""):
            return tier
    return None


def model_for_tier(tier: str) -> str | None:
    return TIERS.get(tier)


def fallback_attr(setting_key: str) -> str | None:
    return FALLBACK_ATTR.get(setting_key)


def capability_warnings(setting_key: str, primary_tier: str | None, fallback_tier: str | None) -> list[str]:
    """Lightweight advisory strings for the ops panel."""
    warnings: list[str] = []
    if setting_key == "review_model" and primary_tier == "haiku":
        warnings.append("Haiku may miss cross-scene continuity issues for long chapters.")
    if setting_key == "packet_qa_model" and primary_tier == "opus" and not fallback_tier:
        warnings.append("Opus is probably unnecessary for packet validation unless escalation is enabled.")
    if setting_key == "scene_packet_author_model" and primary_tier == "haiku":
        warnings.append("Haiku is default for per-scene contracts; bump to Sonnet for high canon density.")
    return warnings
