"""Port of ``frontend/src/desk/lib/beautify.ts`` — the manuscript-prose pre-parse.

Runs on scene prose BEFORE ``parse_blocks`` so hand-pasted text reads like a novel: hard-wrapped
paragraphs are re-flowed, punctuation is typeset, and stray markdown escapes are stripped. Pure and
non-destructive.

Structural blocks pass through byte-for-byte, using the SAME detection ``parse_blocks`` uses:
fenced ```/@interface, box-drawing stat windows, tables, lists, headings, horizontal rules, and
blockquote callouts are line-significant and must never be unwrapped or re-punctuated.
"""

from __future__ import annotations

import re

from .prose import BOX, BQ, FENCE, FENCE_CLOSE, HEADING, HR, OL, UL, time_marker

ESCAPABLE = frozenset("\\`*_{}[]()#+-.!&<>|~\"'/:;=?@^$%")


def _strip_escapes(s: str) -> str:
    r"""Drop markdown escape backslashes before ASCII punctuation (``pass\!`` → ``pass!``)."""

    def repl(m: re.Match[str]) -> str:
        c = m.group(1)
        return c if c in ESCAPABLE else m.group(0)

    return re.sub(r"\\(.)", repl, s)


# JS `\p{L}` / `\p{N}` have no Python `re` equivalent; `[^\W_]` is Unicode letters+digits, and
# `[^\W\d_]` is Unicode letters alone — the exact two classes the TS source uses.
_LETTER_OR_DIGIT = r"[^\W_]"
_LETTER = r"[^\W\d_]"


def _typeset(s: str) -> str:
    """Straight quotes → curly, ``--``/``---`` → em dash, ``...`` → ellipsis."""
    s = re.sub(r"-{2,3}", "—", s)
    s = s.replace("...", "…")
    s = re.sub(r"(^|[\s([{<—])\"", r"\1“", s)
    s = s.replace('"', "”")
    s = re.sub(rf"({_LETTER_OR_DIGIT})'({_LETTER})", r"\1’\2", s)  # don't → don’t
    s = re.sub(r"(^|[\s([{<—])'", r"\1‘", s)
    return s.replace("'", "’")


def _outside_code(s: str, fn) -> str:
    """Apply text transforms only OUTSIDE inline ``code`` spans (backtick-delimited)."""
    parts = re.split(r"(`[^`]*`)", s)
    return "".join(
        seg if (seg.startswith("`") and seg.endswith("`") and len(seg) >= 2) else fn(seg)
        for seg in parts
    )


def _clean_prose(s: str) -> str:
    return _outside_code(s, lambda seg: _typeset(_strip_escapes(seg)))


def _is_delimiter_row(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line)) and "-" in line


def _is_structural_run(run: list[str]) -> bool:
    """A blank-line-delimited run that must be preserved verbatim."""
    first = run[0]
    if (
        BOX.match(first)
        or HEADING.match(first)
        or HR.match(first)
        or UL.match(first)
        or OL.match(first)
        or BQ.match(first)
    ):
        return True
    # pipe table = header row immediately followed by a delimiter row (matches parse_blocks)
    return len(run) >= 2 and "|" in first and _is_delimiter_row(run[1])


def _reflow_run(run: list[str]) -> list[str]:
    """Re-flow + typeset one paragraph run.

    A standalone day/date marker line stays on its own line — a marker butted against body text,
    with no blank line between, still reads as a divider.
    """
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(_clean_prose(re.sub(r"\s+", " ", " ".join(buf)).strip()))
            buf.clear()

    for ln in run:
        if time_marker(ln):
            flush()
            out.append(_clean_prose(ln.strip()))  # `Day 3 -- Dusk` → `Day 3 — Dusk`
        else:
            buf.append(ln)
    flush()
    return out


def beautify(text: str) -> str:
    """Re-flow + typeset prose, passing structural blocks through untouched."""
    lines = re.sub(r"\r\n?", "\n", text).split("\n")
    segments: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue

        # Fenced block (```/@interface/code): verbatim, may span internal blank lines.
        if FENCE.match(lines[i]):
            start = i
            j = i + 1
            while j < len(lines) and not FENCE_CLOSE.match(lines[j]):
                j += 1
            closed = j < len(lines)
            segments.append("\n".join(lines[start : j + 1 if closed else j]))
            i = j + 1 if closed else j
            continue

        # A run of consecutive non-blank lines.
        start = i
        while i < len(lines) and lines[i].strip():
            i += 1
        run = lines[start:i]

        if _is_structural_run(run):
            segments.append("\n".join(run))  # stat window / list / table / heading / rule / callout
            continue

        segments.extend(_reflow_run(run))

    return "\n\n".join(segments)
