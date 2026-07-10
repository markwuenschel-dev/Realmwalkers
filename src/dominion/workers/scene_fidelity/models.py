"""The SceneFidelity typed contract — modes, clauses, criteria, evaluations, and the report.

This is the ONLY shared type vocabulary all later lanes import (Lane 1). The five modes are a closed
registry (ADR 0011); a clause belongs to exactly one mode. A hard clause carries exactly one typed
``SatisfactionCriterion`` (ADR 0023). Evaluations and reports identify the exact ``(requirement_id,
clause_id)`` — never a mutable array position (ADR 0006). LLMs fill these shapes with evidence;
deterministic code (contract.py / policy.py) owns every authority decision.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field


class FidelityMode(StrEnum):
    """Closed registry of the five typed fidelity modes (ADR 0011). No sixth mode ships without an ADR."""

    RELATIONSHIP_TURN = "relationship_turn"
    INTIMACY_BLOCKING = "intimacy_blocking"
    COMBAT_BLOCKING = "combat_blocking"
    SPATIAL_AFFORDANCE = "spatial_affordance"
    READER_MOVIE = "reader_movie"


CLOSED_MODES: frozenset[str] = frozenset(m.value for m in FidelityMode)


class PostDraftPolicy(StrEnum):
    """What a satisfied/lost post-draft finding is allowed to do (ADR 0004). Structural validity of an
    active requirement is unconditional regardless of this value."""

    ADVISORY = "advisory"  # warnings only
    EXPORT_REQUIRED = "export_required"  # eligible losses can hold Production Run completion / export


class ClauseEnforcement(StrEnum):
    """Per-clause enforcement (ADR 0007). A hard clause requires one typed satisfaction criterion and can
    create repair work; a standard clause is advisory and may omit its criterion."""

    STANDARD = "standard"
    HARD = "hard"


class ClauseResult(StrEnum):
    """The merged report's verdict for one clause. Every active hard clause gets exactly one of these,
    including when an adapter fails or a dependency blocks it (ADR 0022) — a hard clause is NEVER omitted.

    Only SATISFIED (with positive prose evidence) verifies a clause; only LOST is a prose failure that can
    become repair work. INDETERMINATE / BLOCKED_BY_DEPENDENCY / NOT_EVALUATED / ADAPTER_FAILED are
    operational states that become incomplete-evaluation holds, never prose failures."""

    SATISFIED = "satisfied"
    LOST = "lost"
    INDETERMINATE = "indeterminate"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    NOT_EVALUATED = "not_evaluated"
    ADAPTER_FAILED = "adapter_failed"


# The closed evidence kinds a satisfaction criterion may cite (ADR 0023) and the anchor kinds an
# evaluation may attach to prose (ADR 0008). Kept as Literals so Pydantic rejects anything else.
EvidenceKind = Literal[
    "action",
    "dialogue",
    "interiority",
    "sequence",
    "spatial_relation",
    "sensory_anchor",
    "state_change",
    "absence_or_restraint",
]
AnchorKind = Literal["contradiction", "expected_beat", "transition", "satisfaction"]

# Runtime sets of the closed Literal vocabularies, for deterministic validation without Pydantic.
EVIDENCE_KINDS: frozenset[str] = frozenset(get_args(EvidenceKind))
ANCHOR_KINDS: frozenset[str] = frozenset(get_args(AnchorKind))


class SatisfactionCriterion(BaseModel):
    """The one typed, author-readable bar a hard clause is verified against (ADR 0023). One criterion may
    cite multiple prose anchors; multiple observable claims must be split into dependent clauses."""

    model_config = ConfigDict(extra="forbid")

    evidence_kind: EvidenceKind
    statement: str


class FidelityClause(BaseModel):
    """One atomic preservation claim with a stable, server-minted ``clause_id`` (ADR 0006)."""

    model_config = ConfigDict(extra="forbid")

    clause_id: str
    enforcement: ClauseEnforcement
    statement: str
    satisfaction_criterion: SatisfactionCriterion | None = None
    depends_on_clause_ids: list[str] = Field(default_factory=list)


class FidelityRequirement(BaseModel):
    """One active requirement in a mode. All five modes share this shape at the contract layer; per-mode
    contract rules are dispatched by ``mode`` in contract.py (see ``_MODE_VALIDATORS``) rather than via a
    union of near-identical subclasses, keeping the discrimination in one place (ADR 0011)."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    mode: FidelityMode
    post_draft_policy: PostDraftPolicy
    clauses: list[FidelityClause]


class EvidenceAnchor(BaseModel):
    """A prose span an evaluation cites. Omission evidence anchors the nearest expected-beat or transition
    span (absence has no quote of its own) and relies on the report's full-prose hash (ADR 0008)."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    quote: str
    anchor_kind: AnchorKind


class ClauseEvaluation(BaseModel):
    """One clause's merged result. Carries both IDs plus the evaluated prose hash and packet fingerprint
    so currentness is decidable from the evaluation alone (ADR 0010)."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    clause_id: str
    mode: FidelityMode
    result: ClauseResult
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    explanation: str
    evaluated_prose_hash: str
    packet_contract_fingerprint: str


class SceneFidelityReport(BaseModel):
    """The immutable per-evaluation record (projected 1:1 into an Artifact by Lane 3B). One clause
    evaluation per active clause, with complete hard-clause coverage (ADR 0022)."""

    model_config = ConfigDict(extra="forbid")

    report_schema_version: int
    scene_id: uuid.UUID
    draft_attempt_id: uuid.UUID
    scene_packet_id: uuid.UUID
    prose_hash: str
    packet_contract_fingerprint: str
    clause_evaluations: list[ClauseEvaluation]
    # Model/prompt/facade provenance (ADR 0014): requested + actual model, fallback use, prompt/facade/
    # schema versions, per-mode adapter status, and the trigger. Provenance only — never authority.
    evaluation_telemetry: dict[str, Any] = Field(default_factory=dict)


def is_fidelity_active(body: Mapping[str, Any]) -> bool:
    """True only for a forward-only active contract: ``fidelity_contract_version: 1`` AND a non-empty
    ``fidelity_requirements`` list (ADR 0025). A legacy packet, a bare version, or suggestions-only body
    is inert. Note this is the *shape* gate; malformed active requirements are still caught by
    ``validate_active_requirements`` and block approval."""
    reqs = body.get("fidelity_requirements")
    return body.get("fidelity_contract_version") == 1 and isinstance(reqs, list) and len(reqs) > 0


def active_requirements(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The raw active requirement dicts, or ``[]`` when inert. Never raises on a malformed body."""
    if not is_fidelity_active(body):
        return []
    reqs = body.get("fidelity_requirements")
    return [r for r in reqs if isinstance(r, dict)] if isinstance(reqs, list) else []
