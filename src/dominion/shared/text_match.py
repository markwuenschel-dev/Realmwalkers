"""Shared whole-word name-presence matching for deterministic packet/scene-packet contract checks.

Both `workers/packet/validation.py` (chapter-packet roster consistency) and
`workers/scene_packet/validation.py` (scene-packet absent-character checks) need the same primitive:
does a known name appear, as a whole word, anywhere in some body field? Kept here once so the two
validators can't drift on matching semantics. Deliberately NOT a general NER system — exact,
case-insensitive, whole-word matching only (DESIGN: do not overreach into fuzzy NLP without tests).
"""

from __future__ import annotations

import re
from typing import Any


def as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def collect_strings(value: Any) -> list[str]:
    """Flatten any string content reachable from a body field (str / list / nested dict) into a flat
    list, so an absence scan can look at a field regardless of its shape."""
    out: list[str] = []
    if isinstance(value, str):
        if value.strip():
            out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(collect_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(collect_strings(v))
    return out


def names_present(text_items: list[str], names: list[str]) -> list[str]:
    """Names that appear as a whole word (case-insensitive) anywhere in `text_items`. Whole-word, not
    bare substring, so a short name can't match inside an unrelated word — but still no fuzzy NER."""
    if not text_items or not names:
        return []
    blob = "\n".join(text_items).lower()
    found: list[str] = []
    for name in names:
        n = name.strip().lower()
        if n and re.search(rf"\b{re.escape(n)}\b", blob):
            found.append(name)
    return found


def get_dotted(body: dict[str, Any], dotted: str) -> Any:
    """Walk a dotted path (e.g. "known_before_scene.reader") through nested dicts; None if any hop
    isn't a dict or the key is absent."""
    node: Any = body
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# --- Canonical-entity surface projection (hidden-identity handling) -------------------------------
# A forbidden canonical name (e.g. "Roth") is forbidden from the reader SURFACE, not from the author's
# internal planning. `entity_bindings` on a packet body binds a canonical name to a surface-safe label
# ("the suited Astria figure"); drafter-facing scaffolding (scene_job / required_beats / exit_state /
# forbidden_beats) is projected through these bindings so the drafter is never TOLD the forbidden name,
# while internal fields (claims, author notes) may keep it. Same whole-word, case-insensitive, no-NER
# discipline as `names_present`, so the two never drift.

# Drafter-facing scaffolding fields — present at seed level on a ChapterPacket (body.scene_seeds[].X) and
# at top level on a ScenePacket (body.X). Projecting these scrubs both drafter paths (Beat.beat_text and
# the drafter contract block) of forbidden canonical names.
DRAFTER_TEXT_FIELDS: tuple[str, ...] = ("scene_job", "required_beats", "exit_state", "forbidden_beats")


def binding_replacements(bindings: Any) -> list[tuple[str, str]]:
    """(`term`, `surface_label`) replacement pairs from a body's `entity_bindings`, longest term first so
    a multi-word alias ("Mara Valeria") is replaced before a substring alias ("Mara"). A binding's
    `canonical_name` and every `forbidden_surface_terms` entry map to its `surface_label`. Degenerate
    entries — no label, or a term equal (case-insensitive) to its own label (nothing to hide) — are
    dropped, so they neither project nor suppress a real leak block."""
    if not isinstance(bindings, list):
        return []
    pairs: list[tuple[str, str]] = []
    for b in bindings:
        if not isinstance(b, dict):
            continue
        label = str(b.get("surface_label") or "").strip()
        if not label:
            continue
        terms = [str(b.get("canonical_name") or ""), *(as_str_list(b.get("forbidden_surface_terms")))]
        for term in terms:
            t = term.strip()
            if t and t.lower() != label.lower() and (t, label) not in pairs:
                pairs.append((t, label))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def bound_terms_lower(bindings: Any) -> set[str]:
    """Lower-cased terms that a binding can genuinely project to a surface label — i.e. the forbidden
    canonical names that are SAFE to leave in drafter scaffolding because they'll be rewritten. A forbidden
    name not in this set has no surface label and is a real leak that must still block."""
    return {term.lower() for term, _label in binding_replacements(bindings)}


def project_text(text: str, replacements: list[tuple[str, str]]) -> str:
    """Whole-word, case-insensitive replacement of each `term` with its `surface_label`."""
    for term, label in replacements:
        text = re.sub(rf"\b{re.escape(term)}\b", label, text, flags=re.IGNORECASE)
    return text


def _project_value(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return project_text(value, replacements)
    if isinstance(value, list):
        return [project_text(v, replacements) if isinstance(v, str) else v for v in value]
    return value


def project_drafter_fields(
    mapping: dict[str, Any], replacements: list[tuple[str, str]], fields: tuple[str, ...] = DRAFTER_TEXT_FIELDS
) -> tuple[dict[str, Any], bool]:
    """Return (possibly-new mapping, changed?) with the given drafter-facing fields projected through
    `replacements`. Returns the original object unchanged (and False) when nothing was rewritten, so callers
    can cheaply tell whether to emit a normalization warning."""
    if not replacements or not isinstance(mapping, dict):
        return mapping, False
    out = dict(mapping)
    changed = False
    for field in fields:
        if field in out:
            new = _project_value(out[field], replacements)
            if new != out[field]:
                out[field] = new
                changed = True
    return (out, True) if changed else (mapping, False)
