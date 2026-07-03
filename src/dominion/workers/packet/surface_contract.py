"""SurfaceContractBuilder and SurfaceContractValidator.

Raw internal ChapterPacket (AuthorPacketInternal) -> projected safe SurfaceContract.

- build_surface_contract: produces drafter-safe body + policies + violations.
- Projection is deterministic, exact whole-word only. No fuzzy NER, no per-book aliases.
- Scene seeds are projected field-by-field into DRAFTER_SURFACE equivalents.
- When a forbidden term reaches a DRAFTER_SURFACE (or READER/POV) scope with no safe policy we emit
  a REPAIR task (fixable by adjusting surface terms / projection policies — it blocks final export,
  never drafting). Only a structurally unusable body hard-blocks.

The surface body is persisted at ChapterPacket.body["_surface_contract"] as a DERIVED view of the
canonical chapter_master_packet (see packet/master.py) — never authoritative, rebuilt by the writers
(propose / body edit) from the raw top-level seeds. Downstream (ScenePacket derivation, sequence
derivation, any drafter-facing consumer) MUST consume it via master.drafter_view, never the raw seeds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from dominion.workers.packet.scopes import (
    ContractScope,
    get_field_scope,
)
from dominion.workers.packet.surface_policy import (
    SurfaceTermPolicy,
    collect_surface_term_policies,
    names_present,
    omit_sentences_containing_terms,
    replace_terms,
    validate_policies,
)

# ChapterPacketViolation is only referenced at runtime inside helpers to avoid import cycles.
# We resolve it locally when needed.


def _get_violation_cls():
    from dominion.workers.packet.validation import ChapterPacketViolation as CPV

    return CPV


def _mk_warn(path: str, kind: str, detail: str):
    CPV = _get_violation_cls()
    return CPV(kind=kind, field=path, detail=detail, severity="warn")


def _mk_repair(path: str, kind: str, detail: str):
    CPV = _get_violation_cls()
    return CPV(kind=kind, field=path, detail=detail, severity="repair")


def _mk_block(path: str, kind: str, detail: str):
    CPV = _get_violation_cls()
    return CPV(kind=kind, field=path, detail=detail, severity="block")


@dataclass(frozen=True)
class SurfaceContractResult:
    """Result of building a SurfaceContract from internal packet body.

    surface_body: the projected safe view (top-level fields + projected scene_seeds).
    policies: the policies that drove (or could have driven) projection.
    violations: every finding (blockers + warnings).
    """

    surface_body: dict[str, Any]
    policies: list[SurfaceTermPolicy]
    violations: list[Any] = dc_field(default_factory=list)  # list[ChapterPacketViolation] at runtime

    @property
    def blockers(self) -> list[Any]:
        return [v for v in self.violations if getattr(v, "severity", None) == "block"]

    @property
    def repair_tasks(self) -> list[Any]:
        return [v for v in self.violations if getattr(v, "severity", None) == "repair"]

    @property
    def warnings(self) -> list[Any]:
        return [v for v in self.violations if getattr(v, "severity", None) == "warn"]


def _project_text_for_surface(
    *,
    text: str,
    policies: Sequence[SurfaceTermPolicy],
    path: str,
) -> tuple[str, list[Any]]:
    """Apply policies in order. Returns (projected_text, violations).

    Rules (exact, case-insensitive, whole-word):
    - replace only if mode=replace AND surface_label present
    - omit only if mode=omit
    - else block (unprojectable)
    """
    projected = text or ""
    violations: list[Any] = []
    for policy in policies:
        if not policy.applies_to_scope(ContractScope.DRAFTER_SURFACE):
            continue
        hits = names_present([projected], list(policy.forbidden_surface_terms))
        if not hits:
            continue
        if policy.replacement_mode == "replace" and policy.surface_label:
            projected = replace_terms(projected, policy.forbidden_surface_terms, policy.surface_label)
            violations.append(
                _mk_warn(
                    path,
                    "surface_term_replaced",
                    f"replaced {hits!r} -> {policy.surface_label!r} at {path} (policy {policy.canonical_term})",
                )
            )
            continue
        if policy.replacement_mode == "omit":
            projected = omit_sentences_containing_terms(projected, policy.forbidden_surface_terms)
            violations.append(_mk_warn(path, "surface_term_omitted", f"omitted content containing {hits!r} at {path}"))
            continue
        # No safe policy: a repair task (fix the surface_terms / add a replace|omit policy), never a
        # hard block — drafting stays reachable, final export waits on the fix.
        violations.append(
            _mk_repair(
                path,
                "forbidden_surface_term_unprojectable",
                f"forbidden term(s) {hits!r} at {path} have no safe replace/omit policy",
            )
        )
    return projected, violations


def _project_list(items: list[str], policies: Sequence[SurfaceTermPolicy], path: str) -> tuple[list[str], list[Any]]:
    out: list[str] = []
    violations: list[Any] = []
    for i, item in enumerate(items):
        p, vs = _project_text_for_surface(text=item, policies=policies, path=f"{path}[{i}]")
        if p:  # drop fully-omitted entries
            out.append(p)
        violations.extend(vs)
    return out, violations


def project_scene_seed(seed: dict[str, Any], policies: Sequence[SurfaceTermPolicy]) -> tuple[dict[str, Any], list[Any]]:
    """Return a copy of seed with drafter-facing text fields projected + any violations."""
    if not isinstance(seed, dict):
        return seed, []
    projected: dict[str, Any] = dict(seed)
    violations: list[Any] = []
    for fld in ("scene_job", "required_beats", "forbidden_beats", "exit_state"):
        val = seed.get(fld)
        fpath = f"scene_seeds[{seed.get('scene_no', '?')}].{fld}"
        if isinstance(val, str):
            new_val, vs = _project_text_for_surface(text=val, policies=policies, path=fpath)
            projected[fld] = new_val
            violations.extend(vs)
        elif isinstance(val, list):
            new_list, vs = _project_list(val, policies, fpath)
            projected[fld] = new_list
            violations.extend(vs)
    return projected, violations


def build_surface_contract(body: dict[str, Any]) -> SurfaceContractResult:
    """Project internal packet into a drafter-safe SurfaceContract.

    1. Collect policies (explicit surface_terms win; characters_forbidden default to block).
    2. Project top-level drafter-relevant fields (entry/exit etc.) when they are classified DRAFTER_SURFACE.
    3. Project every scene seed's drafter fields.
    4. Preserve original internal structure for audit (we return a new surface_body, not mutate).
    5. Unprojectable leaks into DRAFTER_SURFACE (and similar reader scopes) become repair tasks; only
       a structurally unusable body hard-blocks.
    """
    if not isinstance(body, dict):
        return SurfaceContractResult(
            surface_body={},
            policies=[],
            violations=[_mk_block("", "invalid_body", "chapter packet body is not a JSON object")],
        )

    policies = collect_surface_term_policies(body)
    policy_errs = validate_policies(policies)
    violations: list[Any] = []
    for e in policy_errs:
        violations.append(_mk_warn("surface_terms", "surface_policy_malformed", e))

    # Start surface body from the internal shape; we will overwrite only surface-classified text.
    surface: dict[str, Any] = {k: v for k, v in body.items()}

    # Project top-level fields that become drafter surface (per SURFACE_CONTRACT_FIELD_SCOPES).
    for top_field in ("chapter_job", "one_sentence_spine", "entry_state", "exit_state", "emotional_spine"):
        if top_field in surface:
            scope = get_field_scope(top_field)
            if scope == ContractScope.DRAFTER_SURFACE or top_field in ("entry_state", "exit_state", "chapter_job"):
                # Treat these as surface-visible for the contract handed downstream.
                val = surface[top_field]
                fpath = top_field
                if isinstance(val, str):
                    newv, vs = _project_text_for_surface(text=val, policies=policies, path=fpath)
                    surface[top_field] = newv
                    violations.extend(vs)
                elif isinstance(val, list):
                    newl, vs = _project_list(val, policies, fpath)
                    surface[top_field] = newl
                    violations.extend(vs)

    # Project scene seeds (core requirement).
    seeds = body.get("scene_seeds") or []
    new_seeds: list[Any] = []
    if isinstance(seeds, list):
        for seed in seeds:
            if isinstance(seed, dict):
                ps, vs = project_scene_seed(seed, policies)
                new_seeds.append(ps)
                violations.extend(vs)
            else:
                new_seeds.append(seed)
    surface["scene_seeds"] = new_seeds

    # Carry forward surface_terms (or synthesized) for downstream visibility.
    # Downstream (scene author, drafter) can also read surface_terms if they want to be defensive.
    surface["surface_terms"] = [
        {
            "canonical_term": p.canonical_term,
            "forbidden_surface_terms": list(p.forbidden_surface_terms),
            "surface_label": p.surface_label,
            "allowed_surface_terms": list(p.allowed_surface_terms),
            "policy": p.replacement_mode,
            "until": p.until,
            "reason": p.reason,
        }
        for p in policies
    ]

    # If any repair-level violation from projection, they are already in violations.
    # Hard rule per spec: if a characters_forbidden term would reach DRAFTER_SURFACE and no replace/omit
    # policy rescued it, we already emitted a repair task.

    # Also run a post-projection surface scan (defense in depth).
    surf_viols = validate_surface_contract(surface, policies)
    violations.extend(surf_viols)

    return SurfaceContractResult(surface_body=surface, policies=policies, violations=violations)


def validate_surface_contract(
    surface_body: dict[str, Any],
    policies: Sequence[SurfaceTermPolicy],
) -> list[Any]:
    """Scan only DRAFTER_SURFACE (and applicable READER/POV) fields for any remaining forbidden terms.

    Must not scan internal/audit fields. Returns repair tasks when a term that should be forbidden
    is still present after projection.
    """
    violations: list[Any] = []
    if not isinstance(surface_body, dict):
        return [_mk_block("", "invalid_surface_body", "surface body is not a dict")]

    # Build active forbidden set for drafter scopes.
    active_forbidden: list[tuple[str, SurfaceTermPolicy]] = []
    for p in policies:
        if any(p.applies_to_scope(s) for s in (ContractScope.DRAFTER_SURFACE, ContractScope.READER_KNOWLEDGE)):
            for ft in p.forbidden_surface_terms:
                active_forbidden.append((ft, p))

    def _check(path: str, val: Any) -> None:
        if isinstance(val, str):
            hits = names_present([val], [ft for ft, _ in active_forbidden])
            for h in hits:
                violations.append(
                    _mk_repair(
                        path,
                        "forbidden_surface_leak",
                        f"forbidden surface term {h!r} remains in drafter surface at {path}",
                    )
                )
        elif isinstance(val, list):
            for i, item in enumerate(val):
                _check(f"{path}[{i}]", item)
        elif isinstance(val, dict):
            for k, v in val.items():
                _check(f"{path}.{k}", v)

    # Only walk fields declared as surface in the contract.
    # Primary: scene seeds drafter fields.
    seeds = surface_body.get("scene_seeds") or []
    if isinstance(seeds, list):
        for seed in seeds:
            if not isinstance(seed, dict):
                continue
            sno = seed.get("scene_no", "?")
            for fld in ("scene_job", "required_beats", "forbidden_beats", "exit_state"):
                if fld in seed:
                    _check(f"scene_seeds[scene_no={sno}].{fld}", seed[fld])

    # Top level drafter fields that are part of the handed contract.
    for f in ("chapter_job", "one_sentence_spine", "entry_state", "exit_state"):
        if f in surface_body:
            _check(f, surface_body[f])

    return violations
