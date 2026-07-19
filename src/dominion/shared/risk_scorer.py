"""Semantic risk scoring for QA outputs and reviewer flags — drives Phase 2 escalation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from dominion.shared.enums import PacketVerdict, ScenePacketVerdict, Severity
from dominion.shared.severity import is_blocking

CANON_CONFLICT_KINDS: frozenset[str] = frozenset(
    {
        "canon_leak",
        "canon_conflict",
        "timeline_contradiction",
        "premature_reveal",
        "roster_change",
        "future_knowledge_leak",
        "pov_knowledge_leak",
        "reader_context_gap",
        "contradiction",
    }
)

_RISK_RANK = {"low": 0, "medium": 1, "high": 2}


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _verdict_value(verdict: Any) -> str:
    if verdict is None:
        return ""
    if hasattr(verdict, "value"):
        return str(verdict.value).lower()
    return str(verdict).lower()


def score_qa_result(qa: dict[str, Any]) -> RiskLevel:
    """Score a packet or scene-packet QA result for semantic escalation."""
    verdict_s = _verdict_value(qa.get("verdict"))
    if verdict_s in (
        PacketVerdict.BLOCK_DRAFTING.value,
        ScenePacketVerdict.BLOCK_DRAFTING.value,
        "block",
    ):
        return RiskLevel.HIGH
    if verdict_s in (PacketVerdict.REVISE_REQUIRED.value, ScenePacketVerdict.REVISE_REQUIRED.value):
        return RiskLevel.MEDIUM

    issues = qa.get("issues") or []
    block_count = 0
    canon_count = 0
    if isinstance(issues, list):
        for item in issues:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).lower()
            sev = str(item.get("severity", "")).lower()
            # "repair" is the capped form of an LLM-claimed block (parse demotes it) — same risk weight.
            if sev in ("block", "repair") or kind in CANON_CONFLICT_KINDS:
                block_count += 1
                if kind in CANON_CONFLICT_KINDS:
                    canon_count += 1

    if block_count >= 2 or canon_count >= 2:
        return RiskLevel.HIGH
    if block_count >= 1 or canon_count >= 1:
        return RiskLevel.MEDIUM

    risks = qa.get("residual_risks") or []
    if isinstance(risks, list) and len(risks) >= 5:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def score_reviewer_flags(flags: list[Any]) -> RiskLevel:
    """Elevate when advisory reviewers surface HARD findings or many warnings."""
    if not flags:
        return RiskLevel.LOW
    hard = warn = 0
    for flag in flags:
        sev = getattr(flag, "severity", None) or (flag.get("severity") if isinstance(flag, dict) else None)
        if is_blocking(sev):
            hard += 1
        elif sev == Severity.WARN or str(sev).lower() == "warn":
            warn += 1
    if hard >= 1:
        return RiskLevel.HIGH
    if warn >= 3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def should_semantic_escalate(level: RiskLevel) -> bool:
    return level in (RiskLevel.MEDIUM, RiskLevel.HIGH)


def qa_result_preferred(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pick the lower-risk QA result; ties favor the fallback attempt."""
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    p_rank = _RISK_RANK[score_qa_result(primary).value]
    f_rank = _RISK_RANK[score_qa_result(fallback).value]
    if f_rank <= p_rank:
        return fallback
    return primary
