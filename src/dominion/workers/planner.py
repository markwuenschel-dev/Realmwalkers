"""Gate-1 beat proposal — ONE bounded plan-call, then gone (DESIGN §4, §8).

The only LLM that touches the *plan*. It reads a chapter outline (+ POV, omniscient summary, and
beat-scoped canon) and proposes per-scene beats for the human to edit/approve before any scene is
drafted. It is not a resident planner: one call per run, no standing process, no control over what
runs next. Parsing is tolerant — a malformed model response yields no beats rather than an error,
exactly like the continuity reviewer (DESIGN §6).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting

# Headroom for a full beat list: a detailed outline can yield a dozen+ beats, each with prose plus
# knowledge_injections. Too small and the array is truncated mid-element (the parser salvages what
# completed, but the tail beats are lost) — so keep this generous.
_PLAN_MAX_TOKENS = 8000

_SYSTEM = (
    "You are a novelist's planning assistant. From a chapter outline you propose a sequence of "
    "per-scene beats for the author to edit and approve. A beat is a short plan, NOT prose: what "
    "happens, who is present, and any declared state changes. Stay within the outline; invent no "
    "named people, places, or lore the outline and canon do not support. Propose only as many beats "
    "as the outline genuinely needs — never pad to reach a count."
)

_TITLE_SYSTEM = (
    "You are a novelist's assistant. Given a chapter outline and its POV character, propose ONE "
    "short, evocative chapter title: 2-6 words, title case, no quotation marks, no chapter number, "
    "no trailing punctuation. It must fit the outline and invent nothing it doesn't support. Reply "
    "with the title text ONLY — nothing else."
)


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _plan_prompt(*, outline: str, pov: str, omniscient_summary: str | None, canon: list[str], max_beats: int) -> str:
    parts: list[str] = [f"POV (the narrating character for this chapter): {pov}"]
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    if canon:
        parts.append("Relevant canon (treat as true):\n" + "\n".join(f"- {c}" for c in canon))
    parts.append("CHAPTER OUTLINE:\n" + outline)
    parts.append(
        f"\nPropose scene beats that cover this outline, in order — only as many as the outline "
        f"genuinely needs. A short outline may need just a few scenes; do NOT pad to reach a number. "
        f"Propose at most {max_beats}. "
        "Return ONLY a JSON array (no prose, no code fences). Each item:\n"
        '{"scene_no": int (1-based, sequential), "beat_text": str (1-3 sentences of plan), '
        '"characters_present": [str], "tags": [str] (any of "combat", "sensory", '
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


def _salvage_objects(s: str) -> list[Any]:
    """Pull as many *complete* JSON objects as possible out of an array body — even one truncated
    mid-element because the model hit the token cap. Decoding object-by-object (rather than trusting
    the last `]`, which may sit inside an inner list) means a cut-off response still yields every
    beat that finished, instead of parsing to nothing."""
    start = s.find("[")
    if start < 0:
        return []
    dec = json.JSONDecoder()
    out: list[Any] = []
    i, n = start + 1, len(s)
    while i < n:
        while i < n and s[i] in " \t\r\n,":  # skip whitespace/commas between elements
            i += 1
        if i >= n or s[i] == "]":
            break
        try:
            obj, i = dec.raw_decode(s, i)
        except (json.JSONDecodeError, ValueError):
            break  # the truncated tail starts here — keep everything decoded so far
        out.append(obj)
    return out


def _extract_array(raw: str) -> list[Any]:
    """Pull the beat array out of a model response, tolerating common deviations from "ONLY a JSON
    array": code fences, a prose preamble/suffix, a wrapper object like {"beats": [...]}, or a
    response truncated mid-array by the token cap."""
    s = _strip_fences(raw)
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):  # model wrapped it, e.g. {"beats": [...]} / {"scenes": [...]}
        for value in data.values():
            if isinstance(value, list):
                return value
    return _salvage_objects(s)  # whole-string parse failed → recover complete objects (truncation-safe)


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
    max_beats: int = 24,
    budget: TokenBudget | None = None,
) -> list[dict[str, Any]]:
    """One bounded plan-call -> a list of normalized beat dicts (possibly empty). Never raises on a
    malformed response; the author reviews/edits whatever comes back at gate 1."""
    if not outline.strip():
        return []
    b = budget or TokenBudget(max_tokens=settings.scene_token_budget)
    user = _plan_prompt(
        outline=outline,
        pov=pov,
        omniscient_summary=omniscient_summary,
        canon=canon or [],
        max_beats=max_beats,
    )

    async def _attempt(model: str, max_tokens: int) -> tuple[list[dict[str, Any]], Any]:
        raw, usage = await llm.complete(
            model=model,
            system=_SYSTEM,
            user=user,
            max_tokens=max_tokens,
            budget=b,
            expect_cache=False,
        )
        return _parse_beats(raw), usage

    try:
        beats, _model, _esc = await asyncio.wait_for(
            attempt_with_escalation(
                setting_key="draft_model",
                primary_model=settings.draft_model,
                primary_max_tokens=_PLAN_MAX_TOKENS,
                attempt_fn=_attempt,
                is_success=lambda bts: len(bts) > 0,
                policy=policy_for_setting("draft_model"),
            ),
            timeout=settings.plan_time_budget_s,
        )
    except TimeoutError:
        raise TimeoutError(f"beat proposal exceeded {settings.plan_time_budget_s}s — try again") from None
    return beats


def _clean_title(raw: str) -> str | None:
    """First line of the model's reply, stripped of fences/quotes and any 'Chapter N:'/'Title:'
    prefix it sometimes adds. Returns None for an empty or implausibly long result (treated as a
    non-answer), so a bad title is simply absent rather than garbage shown to the author."""
    s = _strip_fences(raw).strip()
    line = s.splitlines()[0].strip() if s else ""
    line = line.strip("\"'").strip()
    line = re.sub(r"^(chapter\s+\w+\s*[:\-—.]\s*|title\s*[:\-—]\s*)", "", line, flags=re.IGNORECASE)
    line = line.strip().rstrip(".")
    return line if 0 < len(line) <= 80 else None


async def propose_chapter_title(
    *,
    outline: str,
    pov: str,
    omniscient_summary: str | None = None,
    budget: TokenBudget | None = None,
) -> str | None:
    """Best-effort: one tiny bounded call -> a short chapter title (or None). NEVER raises — title
    generation is optional polish that runs alongside beat proposal, so any failure (timeout, parse,
    budget, API error) just yields no title rather than failing the run."""
    if not outline.strip():
        return None
    parts = [f"POV: {pov}"]
    if omniscient_summary:
        parts.append(f"Story so far:\n{omniscient_summary}")
    parts.append("CHAPTER OUTLINE:\n" + outline)
    parts.append("\nPropose the chapter title.")
    try:
        raw, _usage = await asyncio.wait_for(
            llm.complete(
                model=settings.draft_model,
                system=_TITLE_SYSTEM,
                user="\n\n".join(parts),
                max_tokens=32,
                budget=budget or TokenBudget(max_tokens=settings.scene_token_budget),
                expect_cache=False,
            ),
            timeout=settings.plan_time_budget_s,
        )
    except Exception:  # noqa: BLE001 — optional; a failed title must not fail the run
        return None
    return _clean_title(raw)
