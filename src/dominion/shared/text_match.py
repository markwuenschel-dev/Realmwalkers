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
