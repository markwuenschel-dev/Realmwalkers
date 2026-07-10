"""Project the ACTIVE fidelity contract into high-salience drafter sections (Lane 3A).

Only author-approved clauses appear (suggestions and legacy packets project nothing). Clauses are emitted
in dependency order — a prerequisite always before the clause that depends on it (ADR 0012) — and split
into the four sections the drafter renders: ``must_preserve`` (positive obligations), ``must_not``
(restraint, from ``absence_or_restraint`` criteria), ``scene_state`` (spatial coherence, from
``spatial_relation`` criteria), and ``establish_before_payoff`` (explicit prereq → payoff pairs).

The output carries statements only — never clause_ids — because it becomes author-facing prompt text
(ADR 0016). Identity stays server-side.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dominion.workers.scene_fidelity.models import active_requirements


def project_fidelity_for_drafter(body: Mapping[str, Any]) -> dict[str, Any]:
    """Sectioned, dependency-ordered drafter view of the active fidelity contract, or empty sections for
    an inert/suggestions-only/legacy body."""
    must_preserve: list[str] = []
    must_not: list[str] = []
    scene_state: list[str] = []
    establish: list[dict[str, str]] = []

    for req in active_requirements(body):
        clauses = [c for c in (req.get("clauses") or []) if isinstance(c, dict)]
        by_id = {c.get("clause_id"): c for c in clauses if isinstance(c.get("clause_id"), str)}
        for clause in _dependency_ordered(clauses, by_id):
            statement = str(clause.get("statement") or "").strip()
            if not statement:
                continue
            kind = _criterion_kind(clause)
            if kind == "absence_or_restraint":
                must_not.append(statement)
            elif kind == "spatial_relation":
                scene_state.append(statement)
            else:
                must_preserve.append(statement)
            for dep_id in clause.get("depends_on_clause_ids") or []:
                prereq = by_id.get(dep_id)
                if isinstance(prereq, dict):
                    prereq_stmt = str(prereq.get("statement") or "").strip()
                    if prereq_stmt:
                        establish.append({"establish": prereq_stmt, "before": statement})

    if not (must_preserve or must_not or scene_state or establish):
        return {}  # inert / suggestions-only / legacy — the drafter renders nothing
    return {
        "must_preserve": must_preserve,
        "must_not": must_not,
        "scene_state": scene_state,
        "establish_before_payoff": establish,
    }


def _criterion_kind(clause: dict[str, Any]) -> str | None:
    crit = clause.get("satisfaction_criterion")
    return crit.get("evidence_kind") if isinstance(crit, dict) else None


def _dependency_ordered(clauses: list[dict[str, Any]], by_id: dict[Any, dict[str, Any]]) -> list[dict[str, Any]]:
    """DFS post-order over within-requirement dependencies: a clause's prerequisites are emitted before
    it. Robust to cycles (each node is visited once), which validation rejects at approval anyway."""
    visited: set[Any] = set()
    order: list[dict[str, Any]] = []

    def visit(clause: dict[str, Any]) -> None:
        cid = clause.get("clause_id")
        if cid in visited:
            return
        visited.add(cid)
        for dep_id in clause.get("depends_on_clause_ids") or []:
            dep = by_id.get(dep_id)
            if isinstance(dep, dict):
                visit(dep)
        order.append(clause)

    for clause in clauses:
        visit(clause)
    return order
