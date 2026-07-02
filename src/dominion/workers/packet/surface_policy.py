"""Generic SurfaceTermPolicy and collection logic.

Replaces ad-hoc entity_bindings / per-name hidden logic with a single policy model.

A term may be forbidden from reader/drafter surface while remaining fully visible to internal planning
and author canon. Policies drive deterministic projection at the SurfaceContractBuilder stage only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from dominion.workers.packet.scopes import ContractScope

SurfaceReplacementMode = Literal["replace", "omit", "block"]
SurfacePolicySource = Literal["packet", "roster", "canon", "outline", "manual"]


@dataclass(frozen=True)
class SurfaceTermPolicy:
    """Declarative rule for how a canonical term may (or may not) appear on drafter/reader surfaces.

    canonical_term: the internal/system truth name (or identifier).
    forbidden_surface_terms: exact terms that must not leak into forbidden scopes.
    allowed_surface_terms: known-safe labels/descriptions (may overlap surface_label).
    surface_label: primary safe replacement when mode=replace.
    scopes_forbidden: scopes where forbidden_surface_terms are disallowed.
    scopes_allowed: scopes where the canonical term itself is permitted.
    replacement_mode: replace (use surface_label), omit (drop containing units), block (no safe path).
    source: where the policy originated (for audit / debugging).
    until: optional reveal gate (string description, not evaluated here).
    reason: human/audit note.
    """

    canonical_term: str
    forbidden_surface_terms: tuple[str, ...]
    allowed_surface_terms: tuple[str, ...] = ()
    surface_label: str | None = None
    scopes_forbidden: tuple[ContractScope, ...] = (
        ContractScope.DRAFTER_SURFACE,
        ContractScope.READER_KNOWLEDGE,
        ContractScope.POV_KNOWLEDGE,
        ContractScope.MANUSCRIPT_SURFACE,
    )
    scopes_allowed: tuple[ContractScope, ...] = (
        ContractScope.INTERNAL_PLANNING,
        ContractScope.AUTHOR_ONLY_CANON,
        ContractScope.AUDIT,
    )
    replacement_mode: SurfaceReplacementMode = "block"
    source: SurfacePolicySource = "roster"
    until: str | None = None
    reason: str | None = None

    def applies_to_scope(self, scope: ContractScope) -> bool:
        if scope in self.scopes_allowed:
            return False
        return scope in self.scopes_forbidden


def _as_str_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def leading_name(entry: str) -> str:
    """Extract candidate canonical identifier: before first ( , ; — - . Matches validation.py helper."""
    if not entry:
        return ""
    m = re.match(r"^[^(,;—-]+", entry.strip())
    return (m.group(0) if m else entry).strip()


def names_present(text_items: list[str], terms: list[str]) -> list[str]:
    """Exact whole-word (case-insensitive) matches of any of `terms` inside `text_items`."""
    if not text_items or not terms:
        return []
    blob = "\n".join(text_items).lower()
    found: list[str] = []
    for t in terms:
        n = t.strip().lower()
        if n and re.search(rf"\b{re.escape(n)}\b", blob):
            found.append(t)
    return found


def replace_terms(text: str, forbidden: Sequence[str], label: str) -> str:
    """Whole-word case-insensitive replace of each forbidden term with label."""
    out = text
    for term in forbidden:
        t = term.strip()
        if t:
            out = re.sub(rf"\b{re.escape(t)}\b", label, out, flags=re.IGNORECASE)
    return out


def omit_sentences_containing_terms(text: str, forbidden: Sequence[str]) -> str:
    """Best-effort omit: drop sentences that contain any forbidden term (crude split on . ! ?)."""
    if not text or not forbidden:
        return text
    # Simple sentence split; keep structure minimal and deterministic.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept: list[str] = []
    fset = {f.strip().lower() for f in forbidden if f.strip()}
    for s in sentences:
        low = s.lower()
        if any(re.search(rf"\b{re.escape(f)}\b", low) for f in fset):
            continue
        kept.append(s)
    result = " ".join(kept).strip()
    # If everything omitted, return empty rather than fabricate.
    return result if result else ""


def collect_surface_term_policies(body: dict[str, Any]) -> list[SurfaceTermPolicy]:
    """Build generic policies from the internal packet body.

    Sources (in priority / override order inside later upgrade):
      - explicit "surface_terms" array (author-supplied, preferred)
      - characters_forbidden (default to block unless upgraded)
      - forbidden_knowledge, forbidden_reveals, forbidden_ui_concepts (author_only canon)
      - roster_locks, claims with source_strength == "FORBIDDEN"

    Never hardcodes story names. Policies are the single source of truth for projection decisions.
    """
    policies: list[SurfaceTermPolicy] = []
    seen: set[str] = set()

    # 1. Explicit surface_terms from author (the robust production path).
    st = body.get("surface_terms")
    candidates = st if isinstance(st, list) else []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        canon = str(entry.get("canonical_term") or "").strip()
        if not canon:
            continue
        key = canon.lower()
        if key in seen:
            continue
        forbidden = tuple(_as_str_list(entry.get("forbidden_surface_terms")) or [canon])
        allowed = tuple(_as_str_list(entry.get("allowed_surface_terms")))
        label = entry.get("surface_label")
        if isinstance(label, str):
            label = label.strip() or None
        else:
            label = None
        pol = str(entry.get("policy") or "block").strip().lower()
        mode: SurfaceReplacementMode = "replace" if pol == "replace" else ("omit" if pol == "omit" else "block")
        pol_obj = SurfaceTermPolicy(
            canonical_term=canon,
            forbidden_surface_terms=forbidden,
            allowed_surface_terms=allowed,
            surface_label=label,
            replacement_mode=mode,
            source="packet",
            until=entry.get("until"),
            reason=entry.get("reason"),
        )
        policies.append(pol_obj)
        seen.add(key)

    # 2. characters_forbidden -> default "block" (author must provide surface policy to unblock).
    for term in _as_str_list(body.get("characters_forbidden")):
        canon = leading_name(term)
        if not canon or canon.lower() in seen:
            continue
        pol = SurfaceTermPolicy(
            canonical_term=canon,
            forbidden_surface_terms=(canon,),
            allowed_surface_terms=(),
            surface_label=None,
            replacement_mode="block",
            source="roster",
        )
        policies.append(pol)
        seen.add(canon.lower())

    # 3. Other forbidden_* lists (treated as author-only canon by default; surface policy can override).
    for field in ("forbidden_knowledge", "forbidden_reveals", "forbidden_ui_concepts"):
        for term in _as_str_list(body.get(field)):
            canon = leading_name(term) or term
            if not canon or canon.lower() in seen:
                continue
            pol = SurfaceTermPolicy(
                canonical_term=canon,
                forbidden_surface_terms=(canon,),
                allowed_surface_terms=(),
                surface_label=None,
                scopes_forbidden=(
                    ContractScope.DRAFTER_SURFACE,
                    ContractScope.READER_KNOWLEDGE,
                    ContractScope.POV_KNOWLEDGE,
                    ContractScope.MANUSCRIPT_SURFACE,
                ),
                scopes_allowed=(
                    ContractScope.INTERNAL_PLANNING,
                    ContractScope.AUTHOR_ONLY_CANON,
                    ContractScope.AUDIT,
                ),
                replacement_mode="block",
                source="packet",
            )
            policies.append(pol)
            seen.add(canon.lower())

    # 4. Claims with FORBIDDEN strength.
    for claim in body.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if str(claim.get("source_strength") or "").upper() == "FORBIDDEN":
            ctext = str(claim.get("claim") or "").strip()
            if ctext:
                canon = leading_name(ctext) or ctext[:80]
                if canon.lower() not in seen:
                    policies.append(
                        SurfaceTermPolicy(
                            canonical_term=canon,
                            forbidden_surface_terms=(canon,),
                            replacement_mode="block",
                            source="canon",
                        )
                    )
                    seen.add(canon.lower())

    # Upgrade: if explicit surface_terms gave a policy for same canonical, prefer it.
    # (Already done by seen + insertion order; explicit came first.)

    return policies


def validate_policies(policies: Sequence[SurfaceTermPolicy]) -> list[str]:
    """Return human messages for malformed policies (non-fatal; builder can still run)."""
    errs: list[str] = []
    for p in policies:
        if not p.canonical_term.strip():
            errs.append("surface policy missing canonical_term")
        if p.replacement_mode == "replace" and not (p.surface_label or p.allowed_surface_terms):
            errs.append(f"replace policy for {p.canonical_term} lacks surface_label")
    return errs
