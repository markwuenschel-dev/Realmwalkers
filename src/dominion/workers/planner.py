"""Gate-1 beat proposal — ONE bounded plan-call, then gone (DESIGN §4, §8).

The only LLM that touches the *plan*. It reads a chapter outline (+ POV, omniscient summary, and
beat-scoped canon) and proposes per-scene beats for the human to edit/approve before any scene is
drafted. It is not a resident planner: one call per run, no standing process, no control over what
runs next. Parsing is tolerant — a malformed model response yields no beats rather than an error,
exactly like the continuity reviewer (DESIGN §6).
"""
from __future__ import annotations

import json
from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget

_PLAN_MAX_TOKENS = 3000

_SYSTEM = (
    "You are a novelist's planning assistant. From a chapter outline you propose a sequence of "
    "per-scene beats for the author to edit and approve. A beat is a short plan, NOT prose: what "
    "happens, who is present, and any declared state changes. Stay within the outline; invent no "
    "named people, places, or lore the outline and canon do not support."
)


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _plan_prompt(
    *, outline: str, pov: str, omniscient_summary: str | None, canon: list[str], max_beats: int
) -> str:
    parts: list[str] = [f"POV (the narrating character for this chapter): {pov}"]
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    if canon:
        parts.append("Relevant canon (treat as true):\n" + "\n".join(f"- {c}" for c in canon))
    parts.append("CHAPTER OUTLINE:\n" + outline)
    parts.append(
        f"\nPropose up to {max_beats} scene beats covering this outline, in order. "
        "Return ONLY a JSON array (no prose, no code fences). Each item:\n"
        '{"scene_no": int (1-based, sequential), "beat_text": str (1-3 sentences of plan), '
        '"characters_present": [str], "tags": [str] (any of "combat", "physical_description", '
        '"dialogue"; else []), "expected_state_changes": object|null (declared stat/inventory '
        'deltas, e.g. {"' + pov + '": {"level": "+1"}}), "knowledge_injections": [str] '
        "(facts this POV learns or already knows in the scene)}."
    )
    return "\n".join(parts)


def _coerce_beat(item: dict[str, Any], fallback_no: int) -> dict[str, Any] | None:
    """Normalize one model-proposed beat into the shape Beat rows expect. Drop unusable items."""
    raw_no = item.get("scene_no", fallback_no)
    try:
        scene_no = int(raw_no)
    except (TypeError, ValueError):
        scene_no = fallback_no
    beat_text = item.get("beat_text")
    if not isinstance(beat_text, str) or not beat_text.strip():
        return None

    def _str_list(value: Any) -> list[str]:
        return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []

    esc = item.get("expected_state_changes")
    return {
        "scene_no": scene_no,
        "beat_text": beat_text.strip(),
        "characters_present": _str_list(item.get("characters_present")),
        "tags": _str_list(item.get("tags")),
        "expected_state_changes": esc if isinstance(esc, dict) else None,
        "knowledge_injections": _str_list(item.get("knowledge_injections")),
    }


def _extract_array(raw: str) -> list[Any]:
    """Pull the beat array out of a model response, tolerating common deviations from "ONLY a JSON
    array": code fences, a prose preamble/suffix, or a wrapper object like {"beats": [...]}."""
    s = _strip_fences(raw)
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        start, end = s.find("["), s.rfind("]")  # salvage an array embedded in prose
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(s[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):  # model wrapped it, e.g. {"beats": [...]} / {"scenes": [...]}
        for value in data.values():
            if isinstance(value, list):
                return value
    return []


def _parse_beats(raw: str) -> list[dict[str, Any]]:
    data = _extract_array(raw)
    beats: list[dict[str, Any]] = []
    for i, item in enumerate(data, start=1):
        if isinstance(item, dict):
            coerced = _coerce_beat(item, fallback_no=i)
            if coerced is not None:
                beats.append(coerced)
    return beats


async def propose_beats(
    *,
    outline: str,
    pov: str,
    omniscient_summary: str | None = None,
    canon: list[str] | None = None,
    max_beats: int = 12,
    budget: TokenBudget | None = None,
) -> list[dict[str, Any]]:
    """One bounded plan-call -> a list of normalized beat dicts (possibly empty). Never raises on a
    malformed response; the author reviews/edits whatever comes back at gate 1."""
    if not outline.strip():
        return []
    raw, _usage = await llm.complete(
        model=settings.draft_model,
        system=_SYSTEM,
        user=_plan_prompt(
            outline=outline, pov=pov, omniscient_summary=omniscient_summary,
            canon=canon or [], max_beats=max_beats,
        ),
        max_tokens=_PLAN_MAX_TOKENS,
        budget=budget or TokenBudget(max_tokens=settings.scene_token_budget),
    )
    return _parse_beats(raw)
