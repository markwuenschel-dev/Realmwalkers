"""Unit tests for the advisory QA grade object (Workstream G — pure, no DB, no LLM).

Locks the rules: scores parse tolerantly (missing -> None, never a gate), the scoring bands are
deterministic, and the grade object is ADVISORY — it never gates drafting (blocking stays reserved
for the deterministic true-blocker list, per the PR #142 LLM-advisory rule).
"""

from __future__ import annotations

from dominion.shared.grading import SCORE_DIMENSIONS, build_grade, grade_verdict, parse_score
from dominion.shared.severity import issue_gates

# --- parse_score: tolerant, clamped, never fails ---------------------------------------------------


def test_parse_score_tolerant():
    parsed = parse_score({"overall": 87, "canon_consistency": "95", "reader_clarity": 140, "scene_utility": -3})
    assert parsed["overall"] == 87
    assert parsed["canon_consistency"] == 95
    assert parsed["reader_clarity"] == 100  # clamped
    assert parsed["scene_utility"] == 0  # clamped
    assert parsed["specificity"] is None and parsed["actionability"] is None  # missing -> None, never a gate


def test_parse_score_garbage_is_all_none():
    for garbage in (None, "high", ["nope"], {"overall": True, "specificity": "great"}):
        parsed = parse_score(garbage)
        assert set(parsed) == set(SCORE_DIMENSIONS)
        assert all(v is None for v in parsed.values())


# --- grade_verdict: the scoring bands ---------------------------------------------------------------


def test_grade_verdict_bands():
    kw = dict(has_blockers=False, has_repairs=False, has_warnings=False, canon_contradiction=False)
    assert grade_verdict(overall=95, **kw) == "pass"
    assert grade_verdict(overall=90, **kw) == "pass"
    assert grade_verdict(overall=85, **kw) == "pass_with_warnings"
    assert grade_verdict(overall=80, **kw) == "pass_with_warnings"
    assert grade_verdict(overall=79, **kw) == "revise_required"
    assert grade_verdict(overall=60, **kw) == "revise_required"
    assert grade_verdict(overall=59, **kw) == "fail"
    # repair tasks force revise_required even at a high overall
    assert grade_verdict(overall=95, **{**kw, "has_repairs": True}) == "revise_required"
    # canon/contract contradiction or a deterministic blocker is a fail regardless of score
    assert grade_verdict(overall=95, **{**kw, "canon_contradiction": True}) == "fail"
    assert grade_verdict(overall=95, **{**kw, "has_blockers": True}) == "fail"
    # missing scores degrade to the issue-derived band — never a gate
    assert grade_verdict(overall=None, **kw) == "pass"
    assert grade_verdict(overall=None, **{**kw, "has_warnings": True}) == "pass_with_warnings"
    assert grade_verdict(overall=None, **{**kw, "has_repairs": True}) == "revise_required"


# --- build_grade: the full Workstream-G object ------------------------------------------------------


def _qa(**over):
    base = {
        "verdict": "approve_warn",
        "residual_risks": ["do not name Serra"],
        "issues": [
            {"kind": "leak", "field": "scene_seeds", "detail": "x", "severity": "repair", **issue_gates("repair")},
            {"kind": "vague", "field": None, "detail": "y", "severity": "warn", **issue_gates("warn")},
        ],
        "score": {"overall": 88, "canon_consistency": 90},
    }
    base.update(over)
    return base


def test_build_grade_shape_and_partitions():
    violations = [{"kind": "roster_double_bucketed", "field": "x", "detail": "d", "severity": "repair"}]
    grade = build_grade(
        artifact_id="abc", artifact_type="chapter_packet", grader="model-x", qa=_qa(), violations=violations
    )
    assert grade["artifact_id"] == "abc"
    assert grade["artifact_type"] == "chapter_packet"
    assert grade["schema_version"] == 1
    assert grade["grader"] == "model-x"
    assert grade["score"]["overall"] == 88 and grade["score"]["reader_clarity"] is None
    assert [r["kind"] for r in grade["repair_tasks"]] == ["roster_double_bucketed", "leak"]
    kinds = [w["kind"] for w in grade["warnings"]]
    assert kinds == ["vague", "residual_risk"]  # freeform residual risks normalize to warn issues
    assert grade["blocking_issues"] == []
    # repair tasks present -> revise_required, but the packet is still approvable for the next stage
    assert grade["verdict"] == "revise_required"
    assert grade["approved_for_next_stage"] is True


def test_build_grade_is_advisory_llm_can_never_block():
    # Even a QA result full of repair issues and a rock-bottom score produces approved_for_next_stage
    # True when there is no DETERMINISTIC blocker — the score object never gates drafting.
    grade = build_grade(
        artifact_id=None,
        artifact_type="scene_packet",
        grader="model-x",
        qa=_qa(score={"overall": 10}),
        violations=[],
    )
    assert grade["verdict"] == "fail"
    assert grade["approved_for_next_stage"] is True
    assert grade["blocking_issues"] == []

    # A deterministic block-severity violation is the ONLY thing that flips it.
    blocked = build_grade(
        artifact_id=None,
        artifact_type="scene_packet",
        grader="model-x",
        qa=_qa(),
        violations=[{"kind": "invalid_body", "field": None, "detail": "d", "severity": "block"}],
    )
    assert blocked["verdict"] == "fail"
    assert blocked["approved_for_next_stage"] is False
    assert [b["kind"] for b in blocked["blocking_issues"]] == ["invalid_body"]


def test_build_grade_canon_contradiction_fails():
    qa = _qa(
        score={"overall": 92},
        issues=[{"kind": "timeline_contradiction", "field": None, "detail": "d", "severity": "repair"}],
        residual_risks=[],
    )
    grade = build_grade(artifact_id=None, artifact_type="chapter_packet", grader="m", qa=qa, violations=[])
    assert grade["verdict"] == "fail"


def test_build_grade_without_qa_or_scores():
    grade = build_grade(artifact_id=None, artifact_type="chapter_packet", grader=None, qa=None, violations=[])
    assert grade["verdict"] == "pass"
    assert all(v is None for v in grade["score"].values())
    assert grade["approved_for_next_stage"] is True
