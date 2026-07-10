"""Deterministic validation, fingerprinting, and identity for the SceneFidelity contract (Lane 1).

Everything here is pure and import-light: inputs are plain dicts (a ScenePacket body), outputs are
value objects. It depends only on ``shared.severity`` and this package's own ``models`` — never on
``scene_packet`` — so the consumer (``scene_packet.validation``, Lane 2) imports THIS at module scope
without an import cycle. ``FidelityViolation`` mirrors ``ScenePacketViolation`` field-for-field, so the
packet validator adapts a batch with ``ScenePacketViolation(**v.as_dict_core())`` (or passes ``as_dict``
straight through).

Structural validity of an active requirement is unconditional (ADR 0004/0022/0023): every violation here
is ``block`` severity, because a malformed active requirement must not reach an approved packet.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from dominion.shared.severity import Severity, issue_gates
from dominion.workers.scene_fidelity.models import (
    CLOSED_MODES,
    EVIDENCE_KINDS,
    ClauseEnforcement,
    ClauseResult,
    FidelityMode,
    PostDraftPolicy,
    active_requirements,
)

_POLICIES: frozenset[str] = frozenset(p.value for p in PostDraftPolicy)
_ENFORCEMENTS: frozenset[str] = frozenset(e.value for e in ClauseEnforcement)


@dataclass(frozen=True)
class FidelityViolation:
    """One deterministic breach of the active fidelity contract. Structurally identical to
    ``scene_packet.validation.ScenePacketViolation`` (kind, field, detail, severity) so Lane 2 adapts it
    with zero mapping. Always ``block`` for active requirements."""

    kind: str
    field: str | None
    detail: str
    severity: Severity = "block"

    def as_dict_core(self) -> dict[str, Any]:
        """The four fields a ``ScenePacketViolation`` is constructed from."""
        return {"kind": self.kind, "field": self.field, "detail": self.detail, "severity": self.severity}

    def as_dict(self) -> dict[str, Any]:
        """Serialized form matching ``ScenePacketViolation.as_dict()`` (adds gate booleans)."""
        return {**self.as_dict_core(), **issue_gates(self.severity)}


def validate_active_requirements(body: Mapping[str, Any]) -> list[FidelityViolation]:
    """Deterministically validate the active fidelity contract in a ScenePacket body.

    Returns ``[]`` for an inert/legacy body (no ``fidelity_requirements``), or every structural breach
    when active requirements are present: version gate, unknown mode, duplicate/absent IDs, invalid
    policy/enforcement, empty clauses, a hard clause missing its typed criterion, malformed criteria, and
    dependency defects (missing target, self-reference, cycle). Never raises on a malformed body.
    """
    violations: list[FidelityViolation] = []
    raw_reqs = body.get("fidelity_requirements")
    if raw_reqs is None:
        return violations  # inert / legacy packet
    if not isinstance(raw_reqs, list):
        return [
            FidelityViolation(
                "fidelity_requirements_malformed", "fidelity_requirements", "fidelity_requirements must be a list"
            )
        ]
    if not raw_reqs:
        return violations  # empty list == no active requirements == inert

    # Active requirements are present: the forward-only activation gate requires the version marker.
    if body.get("fidelity_contract_version") != 1:
        violations.append(
            FidelityViolation(
                "fidelity_contract_version_required",
                "fidelity_contract_version",
                "active fidelity_requirements require fidelity_contract_version: 1",
            )
        )

    seen_req_ids: set[str] = set()
    for ri, req in enumerate(raw_reqs):
        path = f"fidelity_requirements[{ri}]"
        if not isinstance(req, dict):
            violations.append(FidelityViolation("fidelity_requirement_malformed", path, "requirement is not an object"))
            continue

        rid = req.get("requirement_id")
        if not isinstance(rid, str) or not rid.strip():
            violations.append(
                FidelityViolation(
                    "fidelity_requirement_missing_id", f"{path}.requirement_id", "requirement_id is required"
                )
            )
        elif rid in seen_req_ids:
            violations.append(
                FidelityViolation(
                    "fidelity_duplicate_requirement_id", f"{path}.requirement_id", f"duplicate requirement_id {rid!r}"
                )
            )
        else:
            seen_req_ids.add(rid)

        mode = req.get("mode")
        if mode not in CLOSED_MODES:
            violations.append(
                FidelityViolation(
                    "fidelity_unknown_mode",
                    f"{path}.mode",
                    f"unknown mode {mode!r}; the closed registry is {sorted(CLOSED_MODES)}",
                )
            )

        policy = req.get("post_draft_policy")
        if policy not in _POLICIES:
            violations.append(
                FidelityViolation(
                    "fidelity_invalid_post_draft_policy",
                    f"{path}.post_draft_policy",
                    f"post_draft_policy must be one of {sorted(_POLICIES)}, got {policy!r}",
                )
            )

        clauses = req.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            violations.append(
                FidelityViolation(
                    "fidelity_requirement_has_no_clauses",
                    f"{path}.clauses",
                    "a requirement must declare at least one clause",
                )
            )
            clauses = []

        clause_violations, clause_ids, deps_map = _validate_clauses(clauses, path)
        violations.extend(clause_violations)
        violations.extend(_validate_dependencies(clause_ids, deps_map, path))

        if mode in CLOSED_MODES:
            violations.extend(_MODE_VALIDATORS[FidelityMode(mode)](req, path))

    return violations


def _validate_clauses(
    clauses: list[Any], req_path: str
) -> tuple[list[FidelityViolation], list[str], dict[str, list[str]]]:
    violations: list[FidelityViolation] = []
    seen: set[str] = set()
    clause_ids: list[str] = []
    deps_map: dict[str, list[str]] = {}

    for ci, clause in enumerate(clauses):
        cpath = f"{req_path}.clauses[{ci}]"
        if not isinstance(clause, dict):
            violations.append(FidelityViolation("fidelity_clause_malformed", cpath, "clause is not an object"))
            continue

        cid = clause.get("clause_id")
        cid_str = cid if isinstance(cid, str) and cid.strip() else None
        if cid_str is None:
            violations.append(
                FidelityViolation("fidelity_clause_missing_id", f"{cpath}.clause_id", "clause_id is required")
            )
        elif cid_str in seen:
            violations.append(
                FidelityViolation(
                    "fidelity_duplicate_clause_id", f"{cpath}.clause_id", f"duplicate clause_id {cid_str!r}"
                )
            )
        else:
            seen.add(cid_str)
            clause_ids.append(cid_str)

        enforcement = clause.get("enforcement")
        if enforcement not in _ENFORCEMENTS:
            violations.append(
                FidelityViolation(
                    "fidelity_clause_invalid_enforcement",
                    f"{cpath}.enforcement",
                    f"enforcement must be one of {sorted(_ENFORCEMENTS)}, got {enforcement!r}",
                )
            )

        statement = clause.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            violations.append(
                FidelityViolation(
                    "fidelity_clause_missing_statement", f"{cpath}.statement", "clause statement is required"
                )
            )

        crit = clause.get("satisfaction_criterion")
        if enforcement == ClauseEnforcement.HARD.value:
            if crit is None:
                violations.append(
                    FidelityViolation(
                        "fidelity_hard_clause_missing_criterion",
                        f"{cpath}.satisfaction_criterion",
                        "a hard clause requires exactly one typed satisfaction_criterion (ADR 0023)",
                    )
                )
            else:
                violations.extend(_validate_criterion(crit, cpath))
        elif crit is not None:
            # A standard clause MAY omit its criterion, but if present it must still be well-formed.
            violations.extend(_validate_criterion(crit, cpath))

        deps = clause.get("depends_on_clause_ids", [])
        if deps in (None, []):
            deps = []
        elif not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            violations.append(
                FidelityViolation(
                    "fidelity_clause_invalid_dependencies",
                    f"{cpath}.depends_on_clause_ids",
                    "depends_on_clause_ids must be a list of clause_id strings",
                )
            )
            deps = []
        if cid_str is not None:
            deps_map[cid_str] = deps

    return violations, clause_ids, deps_map


def _validate_criterion(crit: Any, cpath: str) -> list[FidelityViolation]:
    field = f"{cpath}.satisfaction_criterion"
    if not isinstance(crit, dict):
        return [FidelityViolation("fidelity_criterion_malformed", field, "satisfaction_criterion is not an object")]
    out: list[FidelityViolation] = []
    ek = crit.get("evidence_kind")
    if ek not in EVIDENCE_KINDS:
        out.append(
            FidelityViolation(
                "fidelity_criterion_unknown_evidence_kind",
                f"{field}.evidence_kind",
                f"evidence_kind must be one of {sorted(EVIDENCE_KINDS)}, got {ek!r}",
            )
        )
    st = crit.get("statement")
    if not isinstance(st, str) or not st.strip():
        out.append(
            FidelityViolation(
                "fidelity_criterion_empty_statement", f"{field}.statement", "criterion statement must be non-empty"
            )
        )
    return out


def _validate_dependencies(
    clause_ids: list[str], deps_map: dict[str, list[str]], req_path: str
) -> list[FidelityViolation]:
    """Missing target / self-reference / cycle detection over the within-requirement clause graph (ADR 0012)."""
    violations: list[FidelityViolation] = []
    known = set(clause_ids)
    # Edges used for cycle detection exclude self-loops and dangling targets (each reported separately).
    graph: dict[str, list[str]] = {}
    for cid in clause_ids:
        edges: list[str] = []
        for dep in deps_map.get(cid, []):
            if dep == cid:
                violations.append(
                    FidelityViolation(
                        "fidelity_clause_self_dependency", f"{req_path}", f"clause {cid!r} depends on itself"
                    )
                )
            elif dep not in known:
                violations.append(
                    FidelityViolation(
                        "fidelity_clause_dependency_missing_target",
                        f"{req_path}",
                        f"clause {cid!r} depends on unknown clause_id {dep!r}",
                    )
                )
            else:
                edges.append(dep)
        graph[cid] = edges

    if _has_cycle(graph):
        violations.append(
            FidelityViolation(
                "fidelity_clause_dependency_cycle",
                f"{req_path}",
                "clause dependencies form a cycle; the dependency graph must be acyclic (ADR 0012)",
            )
        )
    return violations


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)

    def visit(node: str) -> bool:
        color[node] = GREY
        for nxt in graph.get(node, []):
            if color.get(nxt) == GREY:
                return True
            if color.get(nxt) == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in graph)


# Per-mode contract validators (ADR 0011). The five modes currently share the base clause contract, so
# each maps to the shared no-op; this registry is the single place a mode grows its own required shape
# (e.g. an intimacy consent-gate field, a combat geometry list) without a union of near-identical models.
def _no_extra_mode_rules(_req: dict[str, Any], _path: str) -> list[FidelityViolation]:
    return []


_MODE_VALIDATORS: dict[FidelityMode, Callable[[dict[str, Any], str], list[FidelityViolation]]] = {
    FidelityMode.RELATIONSHIP_TURN: _no_extra_mode_rules,
    FidelityMode.INTIMACY_BLOCKING: _no_extra_mode_rules,
    FidelityMode.COMBAT_BLOCKING: _no_extra_mode_rules,
    FidelityMode.SPATIAL_AFFORDANCE: _no_extra_mode_rules,
    FidelityMode.READER_MOVIE: _no_extra_mode_rules,
}


def fidelity_contract_fingerprint(body: Mapping[str, Any]) -> str:
    """A stable, order-independent fingerprint of the ACTIVE contract (ADR 0010).

    Any semantic edit to the active requirements — mode, policy, enforcement, statement, criterion, or
    dependencies — changes the fingerprint and therefore stales prior reports (ADR 0024). A pure reorder
    does NOT (identity is by content and stable clause_id, never array position — ADR 0006). Inert bodies
    fingerprint the empty contract.
    """
    normalized = _normalize_requirements(active_requirements(body))
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_requirements(reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for req in reqs:
        raw_clauses = req.get("clauses")
        clauses = raw_clauses if isinstance(raw_clauses, list) else []
        norm_clauses = []
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            crit = clause.get("satisfaction_criterion")
            norm_crit = (
                {"evidence_kind": crit.get("evidence_kind"), "statement": crit.get("statement")}
                if isinstance(crit, dict)
                else None
            )
            deps = clause.get("depends_on_clause_ids") or []
            norm_clauses.append(
                {
                    "clause_id": clause.get("clause_id"),
                    "enforcement": clause.get("enforcement"),
                    "statement": clause.get("statement"),
                    "satisfaction_criterion": norm_crit,
                    "depends_on_clause_ids": sorted(d for d in deps if isinstance(d, str)),
                }
            )
        norm_clauses.sort(key=lambda c: str(c.get("clause_id")))
        out.append(
            {
                "requirement_id": req.get("requirement_id"),
                "mode": req.get("mode"),
                "post_draft_policy": req.get("post_draft_policy"),
                "clauses": norm_clauses,
            }
        )
    out.sort(key=lambda r: str(r.get("requirement_id")))
    return out


def finding_signature(*, requirement_id: str, clause_id: str, result: ClauseResult | str) -> str:
    """A deterministic signature identifying one report finding, for report-projection idempotency.

    Keyed by ``(requirement_id, clause_id, result)`` — one evaluation per clause per report, so this is
    unique within a report. The Critique idempotency index is scoped by ``source_artifact_id`` (the
    report), so the same signature across two reports is expected and harmless (ADR 0021)."""
    canonical = json.dumps(
        {"requirement_id": requirement_id, "clause_id": clause_id, "result": str(result)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
