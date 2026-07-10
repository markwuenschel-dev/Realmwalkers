"""Operational projection schemas: the strict Critique payload, policy outcomes, and triage results.

The immutable ``SceneFidelityReport`` Artifact is the complete evidence record; a
``Critique(reviewer="scene_fidelity")`` is an operational projection of ONE finding, and its payload is
this strict versioned schema (ADR 0021). Strict payload validation applies ONLY to the
``scene_fidelity`` reviewer (ADR 0025), so ``extra="forbid"`` here never touches legacy critiques.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dominion.workers.scene_fidelity.models import ClauseResult, EvidenceAnchor, FidelityMode


class SceneFidelityCritiquePayload(BaseModel):
    """The strict, versioned payload persisted on a ``Critique(reviewer="scene_fidelity")``.

    ``draft_attempt_id``, ``source_artifact_id``, and ``finding_signature`` are DIRECT provenance IDs that
    MUST equal the Critique row's own columns (verified in Lane 1 tests and enforced in Lane 5). The
    remaining fields carry the projected finding plus the currentness inputs (packet id, prose hash,
    fingerprint) the Desk and Production triage read without re-opening the report Artifact.
    """

    model_config = ConfigDict(extra="forbid")

    payload_schema_version: int = 1

    # Direct provenance — must match the Critique columns of the same name (ADR 0021).
    draft_attempt_id: uuid.UUID
    source_artifact_id: uuid.UUID  # the immutable SceneFidelityReport Artifact
    finding_signature: str

    # The projected finding.
    requirement_id: str
    clause_id: str
    mode: FidelityMode
    result: ClauseResult
    evidence_anchors: list[EvidenceAnchor]
    explanation: str

    # Currentness inputs (ADR 0010), carried so triage/UI decide staleness without the full report.
    scene_packet_id: uuid.UUID
    prose_hash: str
    packet_contract_fingerprint: str


class CritiqueProjection(BaseModel):
    """One report finding mapped to a would-be Critique. ``severity`` is only ``warn`` or ``repair`` — a
    fidelity Critique never carries ``block`` (export holds are operational, not prose failures)."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["warn", "repair"]
    note: str
    payload: dict[str, Any]
    finding_signature: str


class PolicyOutcome(BaseModel):
    """The deterministic outcome of the locked policy matrix for one clause evaluation (ADR 0019).

    ``diagnostic``: report-only (invalid anchor, or a true-negative). ``warning``: a warning Critique
    (advisory requirement or standard clause). ``repair_eligible``: hard + export_required direct
    contradiction / corroborated omission → Critique + Repair Preview. ``operational_hold``: missing /
    stale / indeterminate / blocked / failed export-required evaluation — never a prose failure.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["diagnostic", "warning", "repair_eligible", "operational_hold"]
    reason: str


class TriageResult(BaseModel):
    """What one Production Run triage pass produced: successor/new Issue IDs and any operational holds."""

    model_config = ConfigDict(extra="forbid")

    created_issue_ids: list[uuid.UUID]
    operational_holds: list[str]
