"""Grammar for the `manuscript_canon_conflict` open question (ADR 0028 Slice 3b / ADR 0029).

A manuscript-vs-locked-canon disagreement can NOT be auto-resolved: it is a real editorial decision a
human must make (shared/claim_precedence.conflict_kind). ADR 0029 records that decision as a structured
open question carried in the ChapterPacket `chapter_contract.open_questions.items[]` — a flat `list[str]`
(see workers/packet/master.py `_normalize_open_questions`). This module is the ONE encoder/decoder for
that string, so the question is round-trippable machine data (both source references, the M# span, and
the conflicting assertions) while still living in the existing `items[]` shape.

Resolution is HUMAN-ONLY. This module never adjudicates, never mutates a resolution, and never
restructures `items[]` beyond appending one encoded string (`append_conflict`).

Encoding: a recognizable marker followed by a single compact JSON object, so the value is greppable by
marker, survives arbitrary Unicode / delimiters in the free-text claims, and reconstructs exactly:

    [manuscript_canon_conflict] {"canon_handle": "C3", "canon_id": "...", ...}

`parse_conflict(format_conflict(c)) == c` for every well-formed conflict; `parse_conflict` returns None
for an ordinary human-authored question (which the author is free to add to the same `items[]`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The ADR 0029 named kind for a manuscript × locked-canon conflict. Kept in lockstep with
#: shared/claim_precedence.conflict_kind(LOCKED_CANON, DERIVED_FROM_MANUSCRIPT) — a unit test pins them
#: together so the vocabulary can never drift between the precedence policy and this grammar.
KIND = "manuscript_canon_conflict"

#: Leading marker that identifies an encoded conflict among free-text open-question items.
MARKER = f"[{KIND}]"


@dataclass(frozen=True)
class ManuscriptCanonConflict:
    """One human-adjudicable manuscript-vs-locked-canon disagreement, with full provenance on both sides.

    - `canon_handle` / `canon_id` / `canon_name` — the C# reference into the LIVE retrieved locked canon.
    - `manuscript_handle` / `scene_id` / `scene_version` / `prose_hash` / `span` — the M# reference into
      the immutable imported-prose snapshot the assertion is traceable to (a [start, end) character span).
    - `canon_claim` / `manuscript_claim` — the two conflicting assertions, verbatim (or snippeted).
    """

    canon_handle: str
    canon_id: str
    canon_name: str | None
    manuscript_handle: str
    scene_id: str
    scene_version: int
    prose_hash: str
    span: tuple[int, int]
    canon_claim: str
    manuscript_claim: str

    def __post_init__(self) -> None:
        span = self.span
        if not (isinstance(span, tuple) and len(span) == 2 and all(isinstance(n, int) for n in span)):
            raise ValueError(f"span must be a (start, end) pair of ints, got {span!r}")
        if not (0 <= span[0] <= span[1]):
            raise ValueError(f"span must satisfy 0 <= start <= end, got {span!r}")
        if not isinstance(self.scene_version, int):
            raise ValueError(f"scene_version must be an int, got {self.scene_version!r}")


def format_conflict(conflict: ManuscriptCanonConflict) -> str:
    """Encode a conflict as the single `open_questions.items[]` string. Deterministic (sorted keys)."""
    payload = {
        "canon_handle": conflict.canon_handle,
        "canon_id": conflict.canon_id,
        "canon_name": conflict.canon_name,
        "manuscript_handle": conflict.manuscript_handle,
        "scene_id": conflict.scene_id,
        "scene_version": conflict.scene_version,
        "prose_hash": conflict.prose_hash,
        "span": [conflict.span[0], conflict.span[1]],
        "canon_claim": conflict.canon_claim,
        "manuscript_claim": conflict.manuscript_claim,
    }
    return f"{MARKER} {json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"


def question_text(item: Any) -> str:
    """The question text of an `open_questions["items"]` entry, in either shape.

    #277 gave items a server-minted id, so an entry is `{"item_id", "text"}` rather than a bare string.
    Both decoders below take the text through here so an encoded conflict stays decodable across that
    change — silently returning False for every dict would have made conflict provenance disappear
    rather than error, which is the failure mode the item-shape migration most needed to avoid.
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        value = item.get("text")
        return value.strip() if isinstance(value, str) else ""
    return ""


def is_conflict_question(item: Any) -> bool:
    """True if `item` is an encoded `manuscript_canon_conflict` question (a cheap marker check)."""
    return question_text(item).startswith(MARKER)


def parse_conflict(item: Any) -> ManuscriptCanonConflict | None:
    """Decode an encoded conflict back to a `ManuscriptCanonConflict`, or None if `item` is not one
    (an ordinary human question, a malformed payload, or the wrong type). Never raises."""
    text = question_text(item)
    if not text.startswith(MARKER):
        return None
    try:
        data = json.loads(text[len(MARKER) :].strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        raw_span = data["span"]
        if not (isinstance(raw_span, (list, tuple)) and len(raw_span) == 2):
            return None
        span = (int(raw_span[0]), int(raw_span[1]))
        canon_name = data.get("canon_name")
        return ManuscriptCanonConflict(
            canon_handle=str(data["canon_handle"]),
            canon_id=str(data["canon_id"]),
            canon_name=None if canon_name is None else str(canon_name),
            manuscript_handle=str(data["manuscript_handle"]),
            scene_id=str(data["scene_id"]),
            scene_version=int(data["scene_version"]),
            prose_hash=str(data["prose_hash"]),
            span=span,
            canon_claim=str(data["canon_claim"]),
            manuscript_claim=str(data["manuscript_claim"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def append_conflict(items: list[str], conflict: ManuscriptCanonConflict) -> list[str]:
    """Return a NEW `items` list with the encoded conflict appended. Append-only: the existing items
    (human questions and any prior encoded conflicts) are preserved in order and never restructured."""
    return [*items, format_conflict(conflict)]
