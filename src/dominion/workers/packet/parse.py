"""Tolerant extraction of a single JSON object from a model response (contract-first drafting).

The Packet Author and Packet QA are asked for ONE JSON object. Models still wrap it in code fences
or a prose preamble, so we extract leniently here. NOTE the phase-wide rule: lenient *parsing* is
fine, but the orchestration treats a `None` result as fail-closed (packet -> blocked) — a malformed
packet must never silently degrade into partial drafting constraints.
"""

from __future__ import annotations

import json
from typing import Any

from dominion.shared.llm_text import strip_fences


def extract_object(raw: str) -> dict[str, Any] | None:
    """Pull a single JSON object out of a model response, tolerating code fences and a prose
    preamble/suffix. Returns None when no object can be recovered (caller must fail closed)."""
    s = strip_fences(raw)
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: decode the first complete object starting at the first '{'.
    start = s.find("{")
    if start < 0:
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(s, start)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def str_list(value: Any) -> list[str]:
    """Coerce a model field into a clean list[str]; anything non-list becomes []."""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
