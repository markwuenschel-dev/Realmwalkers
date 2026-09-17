"""Place a model's quote back onto the author's raw chapter snapshot — or say plainly that it can't.

A read-through note cites 1-3 quotes. The Desk highlights them in the raw text by offset, and it never
searches for them itself (a first-occurrence search would silently pick the wrong copy of a repeated
passage). So this module is the ONE place a quote becomes a location, and it is honest about the three
outcomes:

- **located** — exactly one placement exists; its segments carry UTF-16 offsets into the raw text.
- **ambiguous** — several placements exist; up to ``MAX_CANDIDATES`` are returned with the TRUE count,
  and none is chosen.
- **unlocated** — no placement (or a blank quote).

Matching runs over a *projection* of the text, folded the way ``reviewers/base.py:_fold`` folds a
citation (NFKC, typographic quotes/dashes/spaces, whitespace collapse, casefold) plus markdown emphasis
markers ``*``/``_`` ignored, with a per-character map back to the raw text. The upload itself is never
normalized: every returned ``Segment.text`` is the exact raw substring at its offsets.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from array import array
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dominion.workers.memory.manuscript_split import is_scene_break

__all__ = [
    "AMBIGUOUS",
    "LOCATED",
    "MAX_CANDIDATES",
    "UNLOCATED",
    "Anchor",
    "Segment",
    "locate",
    "scene_spans",
    "utf16_offset",
]

MAX_CANDIDATES = 5

LOCATED = "located"
AMBIGUOUS = "ambiguous"
UNLOCATED = "unlocated"

# A superset of `reviewers/base.py:_QUOTE_FOLD` (:56-75): the characters a model "improves" when it
# quotes prose back. Applied AFTER NFKC (which already turns most exotic spaces into " " and "…" into
# "..."), so only look-alikes NFKC leaves alone need listing. `None` deletes the character.
_FOLD_TABLE: dict[int, str | None] = {
    # single quotes / apostrophes
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    0x02BC: "'",
    # double quotes
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x201F: '"',
    # dashes, hyphens, minus
    0x2010: "-",
    0x2011: "-",
    0x2012: "-",
    0x2013: "-",
    0x2014: "-",
    0x2015: "-",
    0x2212: "-",
    0x2E3A: "-",
    0x2E3B: "-",
    # non-breaking / fixed-width spaces (whitespace collapse turns them into one " ")
    0x00A0: " ",
    0x2007: " ",
    0x202F: " ",
    # zero-width and invisible characters
    0x200B: None,
    0x200C: None,
    0x200D: None,
    0x2060: None,
    0xFEFF: None,
    0x00AD: None,
}
_EMPHASIS = frozenset("*_")
# Identical to `reviewers/base.py:_ELLIPSIS` (:77), applied to the FOLDED quote (NFKC has already turned
# "…" into "..."), so the two matchers split a quote the same way.
_ELLIPSIS = re.compile(r"(?:\N{HORIZONTAL ELLIPSIS}|\.\s*\.\s*\.)")
# The manuscript splitter's line model (`manuscript_split._parse_raw`): \r\n, \r and \n end a line.
_NEWLINE = re.compile(r"\r\n|\r|\n")
_ASTRAL = re.compile("[\U00010000-\U0010ffff]")
# U+0300: no character below this has a non-zero canonical combining class.
_FIRST_COMBINING = "\N{COMBINING GRAVE ACCENT}"


@dataclass(frozen=True)
class Segment:
    """One contiguous piece of a placed quote. ``start``/``end`` are UTF-16 code units into the RAW chapter
    text (what JavaScript's ``String.slice`` uses); ``text`` is the raw substring there, unchanged."""

    start: int
    end: int
    text: str

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True)
class Anchor:
    """Where a quoted passage lives in one chapter snapshot. Shape = ``schemas.ReadThroughAnchorOut``."""

    chapter_id: str | None
    state: str  # LOCATED | AMBIGUOUS | UNLOCATED
    text_quoted: str  # the quote as the model returned it
    segments: tuple[Segment, ...]  # located only
    candidates: tuple[tuple[Segment, ...], ...]  # ambiguous only, at most MAX_CANDIDATES
    candidate_count: int  # ambiguous: the true number of placements; 0 otherwise

    def to_json(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "state": self.state,
            "text_quoted": self.text_quoted,
            "segments": [segment.to_json() for segment in self.segments],
            "candidates": [[segment.to_json() for segment in placement] for placement in self.candidates],
            "candidate_count": self.candidate_count,
        }


def utf16_offset(text: str, index: int) -> int:
    """The UTF-16 code-unit offset of python index ``index`` in ``text``. Every astral code point (one
    python character) before ``index`` is two UTF-16 units (a surrogate pair)."""
    if index < 0 or index > len(text):
        raise ValueError(f"index {index} out of range for text of length {len(text)}")
    return index + len(_ASTRAL.findall(text, 0, index))


def scene_spans(text: str) -> list[tuple[int, int]]:
    """Python-index ``[start, end)`` spans of the scenes in ``text``.

    Scenes are split at lines where ``manuscript_split.is_scene_break`` is true — the same rule manuscript
    import uses — and a break line belongs to no scene. Whitespace-only spans are omitted. Text with no
    break is one scene.
    """
    spans: list[tuple[int, int]] = []
    scene_start = 0

    def close(end: int) -> None:
        if end > scene_start and text[scene_start:end].strip():
            spans.append((scene_start, end))

    line_start = 0
    for newline in _NEWLINE.finditer(text):
        if is_scene_break(text[line_start : newline.start()]):
            close(line_start)
            scene_start = newline.end()
        line_start = newline.end()
    if is_scene_break(text[line_start:]):
        close(line_start)
        scene_start = len(text)
    close(len(text))
    return spans


@dataclass(frozen=True)
class _Projection:
    """The folded text of one scene. Projected character ``j`` came from the raw normalization unit
    ``[starts[j], ends[j])`` — a starter plus its combining marks — so an expansion ("ﬁ" -> "fi",
    "ß" -> "ss") maps every produced character back to the one raw character it came from."""

    text: str
    starts: array[int]
    ends: array[int]


def _project(text: str, start: int, end: int) -> _Projection:
    chars: list[str] = []
    starts: array[int] = array("q")
    ends: array[int] = array("q")
    previous_was_space = True  # also drops leading whitespace
    i = start
    while i < end:
        j = i + 1
        # A unit is a starter plus its combining marks, so NFKC can compose "e" + U+0301 into "é".
        while j < end and text[j] >= _FIRST_COMBINING and unicodedata.combining(text[j]):
            j += 1
        unit = text[i:j]
        if j == i + 1 and unit < "\x80":
            folded = unit.lower()  # ASCII: NFKC is the identity and casefold is lower()
        else:
            folded = unicodedata.normalize("NFKC", unit).translate(_FOLD_TABLE).casefold()
        for ch in folded:
            if ch in _EMPHASIS:
                continue
            if ch.isspace():
                if previous_was_space:
                    continue
                ch = " "
                previous_was_space = True
            else:
                previous_was_space = False
            chars.append(ch)
            starts.append(i)
            ends.append(j)
        i = j
    return _Projection("".join(chars), starts, ends)


@lru_cache(maxsize=8)
def _scene_projections(text: str) -> tuple[_Projection, ...]:
    """Cached per chapter text: a chapter's several quotes are located against one projection build."""
    return tuple(_project(text, start, end) for start, end in scene_spans(text))


def _quote_segments(quote: str) -> list[str]:
    """The quote folded exactly as the text is, then split on ellipsis into ordered non-blank segments."""
    folded = _project(quote, 0, len(quote)).text
    return [part for part in (piece.strip() for piece in _ELLIPSIS.split(folded)) if part]


def _placements(projection: _Projection, segments: list[str]) -> Iterator[list[tuple[int, int]]]:
    """Every placement of ``segments`` in one scene, in document order, as projected ``[start, end)`` spans.

    One placement per occurrence of the first segment: each following segment takes its earliest match
    after the previous one ends. As the first occurrence moves right every later cursor moves right too,
    so a segment's last match is reused until the cursor passes it — each segment's search region is
    scanned about once, which keeps the whole enumeration O(len(scene) * len(segments)).
    """
    haystack = projection.text
    first, rest = segments[0], segments[1:]
    last_found = [-1] * len(rest)
    position = haystack.find(first)
    while position != -1:
        spans = [(position, position + len(first))]
        cursor = position + len(first)
        for k, segment in enumerate(rest):
            found = last_found[k]
            if found < cursor:
                found = haystack.find(segment, cursor)
                if found == -1:
                    return  # a later first occurrence only pushes the cursor further right
                last_found[k] = found
            spans.append((found, found + len(segment)))
            cursor = found + len(segment)
        yield spans
        position = haystack.find(first, position + 1)


def _trimmable(ch: str) -> bool:
    return ch.isspace() or ch in _EMPHASIS


def _raw_span(text: str, projection: _Projection, start: int, end: int) -> tuple[int, int]:
    """Map a projected ``[start, end)`` back to raw python indices, trimmed of edge whitespace/emphasis."""
    raw_start = projection.starts[start]
    raw_end = projection.ends[end - 1]
    trimmed_start, trimmed_end = raw_start, raw_end
    while trimmed_start < trimmed_end and _trimmable(text[trimmed_start]):
        trimmed_start += 1
    while trimmed_end > trimmed_start and _trimmable(text[trimmed_end - 1]):
        trimmed_end -= 1
    if trimmed_start == trimmed_end:  # a unit made only of trimmable characters: keep it whole
        return raw_start, raw_end
    return trimmed_start, trimmed_end


class _Utf16Index:
    """``utf16_offset`` for many indices into one text: astral positions found once, then bisected."""

    def __init__(self, text: str) -> None:
        self._astral = [match.start() for match in _ASTRAL.finditer(text)]

    def __call__(self, index: int) -> int:
        return index + bisect.bisect_left(self._astral, index)


def locate(quote: str, chapter_text: str, *, chapter_id: str | None) -> Anchor:
    """Place ``quote`` in ``chapter_text``: located (one placement), ambiguous (several — none chosen) or
    unlocated (none, or a blank quote). A quote elided with ``…``/``...`` places only when every segment
    matches in order, without overlap, inside ONE scene."""
    segments = _quote_segments(quote)
    if not segments:
        return Anchor(chapter_id, UNLOCATED, quote, (), (), 0)

    seen: set[tuple[tuple[int, int], ...]] = set()
    kept: list[tuple[tuple[int, int], ...]] = []
    count = 0
    for projection in _scene_projections(chapter_text):
        for spans in _placements(projection, segments):
            raw = tuple(_raw_span(chapter_text, projection, start, end) for start, end in spans)
            if raw in seen:  # two projected placements inside one expanded raw character
                continue
            seen.add(raw)
            count += 1
            if len(kept) < MAX_CANDIDATES:
                kept.append(raw)

    if count == 0:
        return Anchor(chapter_id, UNLOCATED, quote, (), (), 0)

    to_utf16 = _Utf16Index(chapter_text)
    placements = tuple(
        tuple(Segment(to_utf16(start), to_utf16(end), chapter_text[start:end]) for start, end in raw) for raw in kept
    )
    if count == 1:
        return Anchor(chapter_id, LOCATED, quote, placements[0], (), 0)
    return Anchor(chapter_id, AMBIGUOUS, quote, (), placements, count)
