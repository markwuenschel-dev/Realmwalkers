"""Server-side normalization of author actions on SceneFidelity requirements (packet layer).

The author never mints identity and never activates a suggestion in place. These deterministic helpers
own identity (ADR 0006) and the refine-vs-replace boundary (ADR 0024): accepting a suggestion or
replacing a requirement mints fresh server IDs (remapping dependencies); refinement preserves identity
for non-semantic clarification only and rejects any mode / policy / enforcement / criterion-meaning
change. Lane 7's API calls these; they are pure (body in, body + violations out) and depend only on the
Lane 1 contract, so importing them never pulls scene_packet back into scene_fidelity.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from dominion.workers.scene_fidelity.contract import FidelityViolation, validate_active_requirements

ACTIVE_KEY = "fidelity_requirements"
SUGGESTED_KEY = "suggested_fidelity_requirements"


def _mint(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def mint_identity(requirement: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``requirement`` with a fresh server-minted requirement_id and clause_ids, with
    every ``depends_on_clause_ids`` remapped to the new clause_ids (ADR 0006). A dependency that does not
    resolve to one of this requirement's clauses is dropped from the remap — callers that must reject
    dangling deps validate the raw requirement BEFORE minting (see ``replace_requirement``)."""
    new_req = deepcopy(requirement)
    new_req["requirement_id"] = _mint("req")
    clauses = new_req.get("clauses")
    clauses = clauses if isinstance(clauses, list) else []
    id_map: dict[str, str] = {}
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        old = clause.get("clause_id")
        new = _mint("cl")
        if isinstance(old, str):
            id_map[old] = new
        clause["clause_id"] = new
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        deps = clause.get("depends_on_clause_ids") or []
        clause["depends_on_clause_ids"] = [id_map[d] for d in deps if isinstance(d, str) and d in id_map]
    return new_req


def _active(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (body.get(ACTIVE_KEY) or []) if isinstance(r, dict)]


def _find(active: list[dict[str, Any]], requirement_id: str) -> int | None:
    for i, req in enumerate(active):
        if req.get("requirement_id") == requirement_id:
            return i
    return None


def accept_suggestions(
    body: dict[str, Any], *, requirement_ids: list[str] | None = None
) -> tuple[dict[str, Any], list[FidelityViolation]]:
    """Promote suggested requirements into the active contract with freshly minted identities and set the
    forward-only version marker (ADR 0005/0006). Suggestions are copied, never activated in place. A
    structurally invalid suggestion is rejected (nothing is promoted, violations returned)."""
    suggestions = [s for s in (body.get(SUGGESTED_KEY) or []) if isinstance(s, dict)]
    chosen = (
        suggestions
        if requirement_ids is None
        else [s for s in suggestions if s.get("requirement_id") in requirement_ids]
    )
    if not chosen:
        return body, []
    violations = validate_active_requirements({"fidelity_contract_version": 1, "fidelity_requirements": chosen})
    if violations:
        return body, violations
    remaining = [s for s in suggestions if s not in chosen]
    active = _active(body) + [mint_identity(s) for s in chosen]
    new_body = {**body, ACTIVE_KEY: active, SUGGESTED_KEY: remaining, "fidelity_contract_version": 1}
    return new_body, []


def refine_requirement(
    body: dict[str, Any], requirement_id: str, updated: dict[str, Any]
) -> tuple[dict[str, Any], list[FidelityViolation]]:
    """Refine an active requirement in place, preserving its identity (ADR 0024). Only non-semantic
    clarification is allowed: statements may change, but mode, post_draft_policy, the clause set, any
    clause's enforcement, and any criterion's evidence_kind may NOT — those are identity changes that
    require ``replace_requirement``."""
    active = _active(body)
    idx = _find(active, requirement_id)
    if idx is None:
        return body, [
            FidelityViolation(
                "fidelity_refine_unknown_requirement", "requirement_id", f"no active requirement {requirement_id!r}"
            )
        ]

    conflicts = _refine_conflicts(active[idx], updated)
    if conflicts:
        return body, conflicts

    refined = {**deepcopy(updated), "requirement_id": requirement_id}
    struct = validate_active_requirements({"fidelity_contract_version": 1, "fidelity_requirements": [refined]})
    if struct:
        return body, struct
    new_active = active[:idx] + [refined] + active[idx + 1 :]
    return {**body, ACTIVE_KEY: new_active}, []


def _refine_conflicts(current: dict[str, Any], updated: dict[str, Any]) -> list[FidelityViolation]:
    out: list[FidelityViolation] = []
    for ident_field in ("mode", "post_draft_policy"):
        if updated.get(ident_field) != current.get(ident_field):
            out.append(
                FidelityViolation(
                    "fidelity_refine_requires_replace",
                    ident_field,
                    f"changing {ident_field!r} is an identity change; use replace, not refine (ADR 0024)",
                )
            )
    cur_clauses = {c.get("clause_id"): c for c in current.get("clauses", []) if isinstance(c, dict)}
    upd_clauses = {c.get("clause_id"): c for c in updated.get("clauses", []) if isinstance(c, dict)}
    if set(cur_clauses) != set(upd_clauses):
        out.append(
            FidelityViolation(
                "fidelity_refine_requires_replace",
                "clauses",
                "adding, removing, or re-identifying a clause is an identity change; use replace (ADR 0024)",
            )
        )
        return out
    for cid, cur in cur_clauses.items():
        upd = upd_clauses[cid]
        if upd.get("enforcement") != cur.get("enforcement"):
            out.append(
                FidelityViolation(
                    "fidelity_refine_requires_replace",
                    f"clause[{cid}].enforcement",
                    "enforcement change requires replace (ADR 0024)",
                )
            )
        cur_kind = (
            (cur.get("satisfaction_criterion") or {}).get("evidence_kind")
            if isinstance(cur.get("satisfaction_criterion"), dict)
            else None
        )
        upd_kind = (
            (upd.get("satisfaction_criterion") or {}).get("evidence_kind")
            if isinstance(upd.get("satisfaction_criterion"), dict)
            else None
        )
        if cur_kind != upd_kind:
            out.append(
                FidelityViolation(
                    "fidelity_refine_requires_replace",
                    f"clause[{cid}].satisfaction_criterion",
                    "criterion-meaning change requires replace (ADR 0024)",
                )
            )
    return out


def replace_requirement(
    body: dict[str, Any], requirement_id: str, replacement: dict[str, Any]
) -> tuple[dict[str, Any], list[FidelityViolation]]:
    """Replace an active requirement (or add one if the id is absent) with a freshly minted identity.
    Dependencies are validated on the RAW replacement first, so a dangling dependency is rejected before
    minting (ADR 0024). Old Issues/overrides stay attached to the old identity, handled downstream."""
    violations = validate_active_requirements({"fidelity_contract_version": 1, "fidelity_requirements": [replacement]})
    if violations:
        return body, violations
    minted = mint_identity(replacement)
    active = _active(body)
    idx = _find(active, requirement_id)
    new_active = active + [minted] if idx is None else active[:idx] + [minted] + active[idx + 1 :]
    return {**body, ACTIVE_KEY: new_active, "fidelity_contract_version": 1}, []
