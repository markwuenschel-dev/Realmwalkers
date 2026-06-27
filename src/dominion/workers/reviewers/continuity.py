"""Continuity: the standalone advisory reviewer (DESIGN §6).

Two-step by design: an LLM EXTRACTS the explicit factual claims the prose makes, then DETERMINISTIC
code compares them to the Oracle's ledger. The decision (is this a contradiction?) is code, never an
LLM. It is advisory — it records flags and never blocks the inbox. Until the ledger has state to
protect (Phase 2 commits it on approval), there is nothing to contradict, so it stays silent.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from dominion.shared.config import settings
from dominion.shared.enums import Severity
from dominion.workers import llm
from dominion.workers.reviewers.base import Flag, parse_json_objects

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_EXTRACT_MAX_TOKENS = 1500
_KNOWLEDGE_MAX_TOKENS = 1200

_SYSTEM = (
    "You extract only EXPLICIT, asserted facts about characters from a scene of prose, for a "
    "continuity check. Report only values the prose states outright. Do not infer, guess, or "
    "normalize. If the scene does not state a value, omit it."
)

_KNOWLEDGE_SYSTEM = (
    "You check a scene against its reader-state contract for knowledge problems. You are given what "
    "the reader and the POV character each know before the scene, what the reader must learn, may only "
    "infer, and must not know, plus the scene's intentional mysteries and reviewer false-positive "
    "traps.\n\n"
    "Flag concrete problems and classify each with a `kind`:\n"
    "- reader_context_gap: the prose assumes the reader knows something they do not.\n"
    "- pov_knowledge_leak: the narration uses knowledge this POV cannot have.\n"
    "- premature_reveal: something forbidden/hidden is revealed too early.\n"
    "- confusing_mystery: an intentional mystery is rendered confusing beyond the contract.\n\n"
    "Do NOT flag intentional mysteries as missing context. Do NOT flag the listed false-positive traps "
    "unless the prose makes the intended mystery confusing beyond the contract. Do NOT treat "
    "author-only/omniscient canon as reader knowledge. If nothing is out of bounds, report nothing."
)


def _extract_prompt(prose: str, watched: dict[str, list[str]]) -> str:
    lines = ["For each character and attribute below, if the SCENE explicitly states a value, report it."]
    for character, attrs in watched.items():
        lines.append(f"- {character}: {', '.join(attrs)}")
    lines.append(
        '\nReturn ONLY a JSON array (no prose, no code fences). Each item: '
        '{"character": str, "attribute": str, "value": str, "context_sentence": str}. '
        "Omit anything the scene does not explicitly state."
    )
    lines.append("\nSCENE:\n" + prose)
    return "\n".join(lines)


def _contract_section(reader_state: dict[str, Any] | None) -> str:
    """Render the reader-state contract for the knowledge check (empty when absent)."""
    if not reader_state:
        return ""
    import json as _json
    known = reader_state.get("known_before_scene") or {}
    learned = reader_state.get("learned_during_scene") or {}
    hidden = reader_state.get("must_remain_hidden") or {}
    pov_perms = reader_state.get("pov_permissions") or {}
    lines = [
        "READER-STATE CONTRACT:",
        f"- Known to reader before scene: {_json.dumps(known.get('reader') or [])}",
        f"- Known to POV before scene: {_json.dumps(known.get('pov') or [])}",
        f"- Reader must learn: {_json.dumps(learned.get('reader_must_learn') or [])}",
        f"- Reader may infer only: {_json.dumps(learned.get('reader_may_infer_only') or [])}",
        f"- Reader must NOT know: {_json.dumps(hidden.get('reader') or [])}",
        f"- POV must NOT know: {_json.dumps((hidden.get('pov') or []) + (pov_perms.get('must_not_know') or []))}",
        f"- Intentional mysteries: {_json.dumps(reader_state.get('intentional_mysteries') or [])}",
        f"- False-positive traps (do not flag): {_json.dumps(reader_state.get('reviewer_false_positive_traps') or [])}",
    ]
    return "\n".join(lines) + "\n\n"


def _knowledge_prompt(prose: str, pov: str, pov_summary: str | None, reader_state: dict[str, Any] | None) -> str:
    knows = f"WHAT {pov} KNOWS SO FAR:\n{pov_summary}\n\n" if pov_summary else ""
    return (
        f"POV character: {pov}\n\n{_contract_section(reader_state)}{knows}SCENE:\n{prose}\n\n"
        'Return ONLY a JSON array (no prose, no code fences). Each item: '
        '{"reference": str, "kind": '
        '"reader_context_gap|pov_knowledge_leak|premature_reveal|confusing_mystery", '
        '"note": str}. Empty array [] if nothing is out of bounds.'
    )


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parse(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


_SENTENCE_BOUNDARY = ".!?\n"


def _locate(prose: str, value: str) -> tuple[list[int] | None, str]:
    """Anchor a flagged value in the prose, deterministically (never an LLM guess).

    Returns ([start, end] char offsets of the value, the enclosing sentence) — so the panel can show
    real context and the inline marker can resolve the exact occurrence. Both default empty/None when
    the value isn't present verbatim (the model may have normalized it)."""
    if not value:
        return None, ""
    idx = prose.find(value)
    if idx < 0:
        return None, ""
    span = [idx, idx + len(value)]
    start = idx
    while start > 0 and prose[start - 1] not in _SENTENCE_BOUNDARY:
        start -= 1
    end = idx + len(value)
    while end < len(prose) and prose[end] not in _SENTENCE_BOUNDARY:
        end += 1
    if end < len(prose):
        end += 1  # keep the terminating punctuation
    return span, prose[start:end].strip()


class ContinuityReviewer:
    name = "continuity"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        # Two independent checks, each gated to spend no tokens when it has nothing to do:
        # the HARD hard-number check (prose vs Oracle ledger) and the ADVISORY POV-knowledge check.
        flags = await self._hard_number_flags(scene_prose, ctx)
        flags.extend(await self._knowledge_flags(scene_prose, ctx))
        return flags

    async def _hard_number_flags(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        # Nothing canonical to contradict yet -> skip (and spend no tokens).
        watched = {char: sorted(stats.keys()) for char, stats in ctx.ledger.items() if stats}
        if not watched:
            return []

        raw, _usage = await llm.complete(
            model=settings.review_model,
            system=_SYSTEM,
            user=_extract_prompt(scene_prose, watched),
            max_tokens=_EXTRACT_MAX_TOKENS,
            budget=ctx.budget,
            expect_cache=False,
        )

        flags: list[Flag] = []
        for claim in _parse(raw):
            character = str(claim.get("character", ""))
            attribute = str(claim.get("attribute", ""))
            prose_value = str(claim.get("value", ""))
            canon = ctx.ledger.get(character, {})
            if attribute in canon and str(canon[attribute]) != prose_value:
                span, sentence = _locate(scene_prose, prose_value)
                # Prefer the LLM's context sentence; fall back to the one we located in the prose.
                context_sentence = str(claim.get("context_sentence", "")).strip() or sentence
                flags.append(Flag(
                    reviewer=self.name,
                    severity=Severity.HARD,
                    note=f"{character} {attribute}: scene says {prose_value!r}, "
                         f"ledger says {str(canon[attribute])!r}",
                    payload={
                        "character": character,
                        "attribute": attribute,
                        "prose_value": prose_value,
                        "ledger_value": str(canon[attribute]),
                        "context_sentence": context_sentence,
                        "span": span,
                    },
                ))
        return flags

    async def _knowledge_flags(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        """POV-knowledge asymmetry (DESIGN §7): advisory-flag narration that references anything
        outside what this POV knows. Silent (and free) when there is no POV summary to measure
        against. Strictly advisory — never HARD, never the continuity panel."""
        # Run when there's either a POV summary or a reader-state contract to measure against.
        if (not ctx.pov_summary and not ctx.reader_state_contract) or not scene_prose.strip():
            return []
        raw, _usage = await llm.complete(
            model=settings.review_model,
            system=_KNOWLEDGE_SYSTEM,
            user=_knowledge_prompt(scene_prose, ctx.pov, ctx.pov_summary, ctx.reader_state_contract),
            max_tokens=_KNOWLEDGE_MAX_TOKENS,
            budget=ctx.budget,
            expect_cache=False,
        )
        flags: list[Flag] = []
        for item in parse_json_objects(raw):
            note = str(item.get("note", "")).strip()
            if not note:
                continue
            reference = str(item.get("reference", "")).strip()
            kind = str(item.get("kind", "")).strip() or "knowledge"
            flags.append(Flag(
                reviewer=self.name,
                severity=Severity.WARN,
                note=note,
                payload={"kind": kind, "reference": reference},
            ))
        return flags


continuity_reviewer = ContinuityReviewer()
