"""Reviewer protocol. Reviewers ADVISE; they never mutate prose or block the inbox (DESIGN §2, §9)."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dominion.shared.enums import Severity
from dominion.shared.llm_text import strip_fences

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

__all__ = [
    "Flag",
    "advisory_severity",
    "cited_quote",
    "parse_json_objects",
    "quote_is_supported",
    "strip_fences",
]


def parse_json_objects(raw: str) -> list[dict[str, Any]]:
    """Tolerantly parse a model response into a list of JSON objects. Any malformed output -> []
    (advisory reviewers never fail a job on a bad LLM response, DESIGN §6)."""
    try:
        data = json.loads(strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def advisory_severity(value: object) -> Severity:
    """Clamp a model-suggested severity to an advisory level. Reviewers never emit BLOCK — that is
    reserved for the continuity hard-number check (DESIGN §6, §9)."""
    return Severity.WARN if str(value).strip().lower() == "warn" else Severity.INFO


@dataclass
class Flag:
    """An advisory finding. Persisted as a Critique row; HARD numeric ones feed the continuity panel."""

    reviewer: str
    severity: Severity
    note: str
    payload: dict[str, Any] | None = None


# Characters a model routinely "improves" when it quotes prose back: curly quotes for straight ones,
# an em/en dash for a hyphen, a single ellipsis glyph for three dots, non-breaking spaces. None of these
# make a citation false, so they are folded away before comparison rather than counted as a mismatch.
_QUOTE_FOLD = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u00a0": " ",
    "\u2007": " ",
    "\u202f": " ",
    "\u200b": "",
}
# A model eliding the middle of a long passage. Segments are matched in order, not as one literal.
_ELLIPSIS = re.compile(r"(?:\u2026|\.\s*\.\s*\.)")


def _fold(text: str) -> str:
    """Normalize prose for citation comparison: NFKC, typographic look-alikes folded, whitespace
    collapsed, casefolded. Deliberately lossy — the question is whether the model quoted THIS PROSE,
    not whether it reproduced the exact bytes."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans(_QUOTE_FOLD))
    return re.sub(r"\s+", " ", text).strip().casefold()


def cited_quote(flag: Flag) -> str | None:
    """The passage a flag claims to be quoting, or None if it cited nothing.

    None and a citation that is merely blank are the SAME answer here: no claim was made. Many
    legitimate findings quote nothing (a pacing note about the scene as a whole), and this module never
    invents an evidence requirement — it only checks a citation that was actually offered.

    COVERAGE BOUNDARY — read this before assuming a reviewer is guarded. Only the `quote` key is
    treated as a citation, which covers `reviewers/voice.py` and `reviewers/lane.py`. It deliberately
    does NOT cover:
      - `reviewers/continuity.py`, which cites via `prose_value` / `context_sentence` and emits at
        Severity.BLOCK. Its `_locate` already returns span=None when the claimed value is absent, so
        the fabrication is detectable there — but a continuity flag also feeds the repair-verification
        gate (`production_repair._finalize_repair_verification`), and its `prose_value` may be a
        legitimately normalized rendering of a number ("8%" vs "8 percent") that substring matching
        would wrongly reject. Suppressing a real BLOCK finding is a worse failure than the noise this
        module removes, so that reviewer needs its own evidence before it is guarded.
      - `reviewers/state_drift.py`, which cites nothing at all — there is no claim to check.
    """
    if not flag.payload:
        return None
    quote = flag.payload.get("quote")
    if not isinstance(quote, str):
        return None
    quote = quote.strip()
    return quote or None


def quote_is_supported(quote: str, prose: str) -> bool:
    """Does `quote` actually occur in `prose`?

    A reviewer that cites a passage the scene does not contain has fabricated its evidence, and the
    finding built on it was never right — the review equivalent of an out-of-range evidence span.
    Deterministic and local: no model call, no labels, no judgement about whether the finding's POINT
    is correct, only about whether the passage it rests on exists.

    Matching is deliberately forgiving, because a false REJECTION here would suppress a real finding:
    typographic look-alikes are folded, whitespace is collapsed, case is ignored, and a quote elided
    with an ellipsis is satisfied when its segments appear in order. An empty quote is not a claim and
    is treated as supported (`cited_quote` is what decides a claim was made).
    """
    folded_quote = _fold(quote)
    if not folded_quote:
        return True
    folded_prose = _fold(prose)
    segments = [seg for seg in (s.strip() for s in _ELLIPSIS.split(folded_quote)) if seg]
    cursor = 0
    for segment in segments:
        found = folded_prose.find(segment, cursor)
        if found == -1:
            return False
        cursor = found + len(segment)
    return True


@runtime_checkable
class Reviewer(Protocol):
    name: str

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]: ...
