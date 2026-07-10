"""SceneFidelity — a contract-first production subsystem behind ScenePacket.

Active, author-approved ScenePacket fidelity contracts are deterministically validated and projected
into drafter context; post-draft, bounded mode adapters produce immutable report Artifacts with
per-hard-clause coverage. Deterministic code owns packet approval, policy mapping, currentness, and
export readiness — LLMs only report evidence (see docs/adr/00*-scene-fidelity-*.md).

This package is the single shared type vocabulary every later lane imports. It is forward-only and inert
unless an approved packet carries ``fidelity_contract_version: 1`` and active requirements.
"""

from __future__ import annotations

from dominion.workers.scene_fidelity.contract import (
    fidelity_contract_fingerprint,
    finding_signature,
    validate_active_requirements,
)
from dominion.workers.scene_fidelity.drafter_projection import project_fidelity_for_drafter
from dominion.workers.scene_fidelity.models import (
    CLOSED_MODES,
    AnchorKind,
    ClauseEnforcement,
    ClauseEvaluation,
    ClauseResult,
    EvidenceAnchor,
    EvidenceKind,
    FidelityClause,
    FidelityMode,
    FidelityRequirement,
    PostDraftPolicy,
    SatisfactionCriterion,
    SceneFidelityReport,
    active_requirements,
    is_fidelity_active,
)
from dominion.workers.scene_fidelity.payloads import (
    CritiqueProjection,
    PolicyOutcome,
    SceneFidelityCritiquePayload,
    TriageResult,
)

__all__ = [
    "CLOSED_MODES",
    "AnchorKind",
    "ClauseEnforcement",
    "ClauseEvaluation",
    "ClauseResult",
    "CritiqueProjection",
    "EvidenceAnchor",
    "EvidenceKind",
    "FidelityClause",
    "FidelityMode",
    "FidelityRequirement",
    "PolicyOutcome",
    "PostDraftPolicy",
    "SatisfactionCriterion",
    "SceneFidelityCritiquePayload",
    "SceneFidelityReport",
    "TriageResult",
    "active_requirements",
    "fidelity_contract_fingerprint",
    "finding_signature",
    "is_fidelity_active",
    "project_fidelity_for_drafter",
    "validate_active_requirements",
]
