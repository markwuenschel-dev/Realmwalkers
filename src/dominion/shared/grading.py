"""Advisory QA grade object shared by the chapter- and scene-packet QA paths (Workstream G).

The graders (packet/qa.py, scene_packet/qa.py) ask the SAME single QA call for per-dimension scores;
this module parses them tolerantly (missing scores -> None, never a gate) and folds them — together
with the deterministic violations and the normalized LLM issues — into one machine-readable grade:

    {artifact_id, artifact_type, schema_version, grader, verdict,
     score{overall, canon_consistency, reader_clarity, scene_utility, specificity,
           non_contradiction, actionability},
     blocking_issues, warnings, repair_tasks, approved_for_next_stage}

The grade is ADVISORY ONLY (the PR #142 rule holds): drafting/approval gates read deterministic
severity, never this object. It is persisted at qa_warnings.grade for humans and downstream agents.

Scoring bands (``grade_verdict``, pure):
  - ``pass``               overall >= 90 and no blockers (and no repair tasks)
  - ``pass_with_warnings`` overall >= 80 and no blockers
  - ``revise_required``    overall 60-79, or repair tasks present
  - ``fail``               overall < 60, a deterministic blocker, or a canon/contract contradiction
"""

from __future__ import annotations

from typing import Any

from dominion.shared.severity import issue_gates

GRADE_SCHEMA_VERSION = 1

SCORE_DIMENSIONS: tuple[str, ...] = (
    "overall",
    "canon_consistency",
    "reader_clarity",
    "scene_utility",
    "specificity",
    "non_contradiction",
    "actionability",
)

# Issue kinds that count as a canon/contract CONTRADICTION for the fail band (actual contradictions
# only — a mere leak is repair-band, not an automatic fail).
_CONTRADICTION_KINDS: frozenset[str] = frozenset(
    {"contradiction", "canon_conflict", "timeline_contradiction", "chapter_scene_contradiction"}
)


def parse_score(value: Any) -> dict[str, int | None]:
    """Tolerantly parse the LLM's per-dimension scores: ints clamped to 0..100, anything unusable
    (missing dimension, non-numeric, bool) -> None. A missing score never fails or gates anything."""
    src = value if isinstance(value, dict) else {}
    out: dict[str, int | None] = {}
    for dim in SCORE_DIMENSIONS:
        raw = src.get(dim)
        if isinstance(raw, bool):
            out[dim] = None
        elif isinstance(raw, (int, float)):
            out[dim] = max(0, min(100, int(raw)))
        elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
            out[dim] = max(0, min(100, int(raw.strip())))
        else:
            out[dim] = None
    return out


def grade_verdict(
    *,
    overall: int | None,
    has_blockers: bool,
    has_repairs: bool,
    has_warnings: bool,
    canon_contradiction: bool,
) -> str:
    """The scoring band for a grade. Pure. A None overall (LLM gave no scores) degrades gracefully to
    the issue-derived band — missing scores are never a gate."""
    if has_blockers or canon_contradiction or (overall is not None and overall < 60):
        return "fail"
    if has_repairs or (overall is not None and overall < 80):
        return "revise_required"
    if overall is None:
        return "pass_with_warnings" if has_warnings else "pass"
    return "pass" if overall >= 90 else "pass_with_warnings"


def build_grade(
    *,
    artifact_id: Any,
    artifact_type: str,
    grader: str | None,
    qa: dict[str, Any] | None,
    violations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the grade from one parsed QA result ({verdict, issues, residual_risks, score?}) plus
    the deterministic machine-readable violation dicts. Pure. Issue partitions follow severity:
    block -> blocking_issues (deterministic only — LLM issues are capped at repair upstream),
    repair -> repair_tasks, warn/info -> warnings; freeform residual_risks become warn issues."""
    issues = [i for i in (qa or {}).get("issues") or [] if isinstance(i, dict)]
    combined = [*(v for v in violations or [] if isinstance(v, dict)), *issues]
    blocking = [c for c in combined if c.get("severity") == "block"]
    repairs = [c for c in combined if c.get("severity") == "repair"]
    warnings = [c for c in combined if c.get("severity") in ("warn", "info")]
    warnings += [
        {"kind": "residual_risk", "field": None, "detail": risk, "severity": "warn", **issue_gates("warn")}
        for risk in (qa or {}).get("residual_risks") or []
        if isinstance(risk, str) and risk.strip()
    ]
    score = parse_score((qa or {}).get("score"))
    canon_contradiction = any(
        str(c.get("kind") or "").lower() in _CONTRADICTION_KINDS
        for c in combined
        if c.get("severity") in ("block", "repair")
    )
    return {
        "artifact_id": str(artifact_id) if artifact_id is not None else None,
        "artifact_type": artifact_type,
        "schema_version": GRADE_SCHEMA_VERSION,
        "grader": grader,
        "verdict": grade_verdict(
            overall=score["overall"],
            has_blockers=bool(blocking),
            has_repairs=bool(repairs),
            has_warnings=bool(warnings),
            canon_contradiction=canon_contradiction,
        ),
        "score": score,
        "blocking_issues": blocking,
        "warnings": warnings,
        "repair_tasks": repairs,
        # ADVISORY: derived from the deterministic blocker list only; approval/drafting gates read
        # severity facts directly and never this flag (the score object can never gate drafting).
        "approved_for_next_stage": not blocking,
    }
