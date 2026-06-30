"""Tests for semantic risk scoring (agent ops Phase 2)."""

from __future__ import annotations

from dominion.shared.enums import PacketVerdict, Severity
from dominion.shared.risk_scorer import (
    RiskLevel,
    qa_result_preferred,
    score_qa_result,
    score_reviewer_flags,
    should_semantic_escalate,
)


def test_score_qa_block_is_high():
    assert score_qa_result({"verdict": PacketVerdict.BLOCK_DRAFTING}) == RiskLevel.HIGH


def test_score_qa_revise_is_medium():
    assert score_qa_result({"verdict": PacketVerdict.REVISE_REQUIRED}) == RiskLevel.MEDIUM


def test_score_qa_canon_issues_elevate():
    qa = {
        "verdict": PacketVerdict.APPROVE_WARN,
        "issues": [{"kind": "canon_leak", "severity": "block"}, {"kind": "timeline_contradiction"}],
    }
    assert score_qa_result(qa) == RiskLevel.HIGH


def test_should_semantic_escalate_medium_and_high():
    assert should_semantic_escalate(RiskLevel.LOW) is False
    assert should_semantic_escalate(RiskLevel.MEDIUM) is True
    assert should_semantic_escalate(RiskLevel.HIGH) is True


def test_qa_result_preferred_picks_lower_risk():
    primary = {"verdict": PacketVerdict.REVISE_REQUIRED, "issues": []}
    fallback = {"verdict": PacketVerdict.APPROVE_WARN, "issues": []}
    assert qa_result_preferred(primary, fallback) is fallback


def test_score_reviewer_hard_flags():
    class Flag:
        def __init__(self, severity):
            self.severity = severity

    assert score_reviewer_flags([Flag(Severity.HARD)]) == RiskLevel.HIGH
