"""Deterministic stat-window rendering — the model emits values, code draws the box.

The drafter emits a fenced ```stat``` block (label/value lines only, no borders); this pure function
computes the column widths and draws a perfectly aligned Unicode box. Asking an LLM to do monospace
column math (padding every line to equal width) produces ragged borders; deterministic code does not.

Pure text in, text out: no I/O, no state, no randomness, no clock. Prose outside ```stat``` blocks is
left byte-for-byte unchanged, and malformed/empty markers are left exactly as written — never raises.
"""

from __future__ import annotations

import re

# A fence whose info string is exactly `stat` (case-insensitive, trimmed); closing fence is a bare
# run of backticks. We scan line-by-line so everything outside a block is preserved byte-for-byte.
_OPEN = re.compile(r"^[ \t]*`{3,}[ \t]*stat[ \t]*$", re.IGNORECASE)
_CLOSE = re.compile(r"^[ \t]*`{3,}[ \t]*$")

_GAP = "  "  # ≥2 spaces between the label column and the value column


def render_stat_blocks(text: str) -> str:
    """Replace every ```stat``` block with an aligned box-drawing window; leave all else unchanged."""
    lines = text.split("\n")  # split/join on "\n" round-trips any content exactly
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _OPEN.match(lines[i].rstrip("\r")):
            j = i + 1
            while j < len(lines) and not _CLOSE.match(lines[j].rstrip("\r")):
                j += 1
            if j < len(lines):  # found a closing fence: lines i..j are the block
                box = _render_block(lines[i + 1 : j])
                if box is None:
                    out.extend(lines[i : j + 1])  # nothing parseable — leave exactly as written
                else:
                    out.append(box)
                i = j + 1
                continue
            # unterminated fence — treat the opener as ordinary prose and carry on
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _render_block(content: list[str]) -> str | None:
    """Render one block's value lines into a box, or None if it has no header/row worth drawing."""
    # Classify each non-empty line: ("hdr", text), ("row", label, value), or ("div",) for a rule.
    elements: list[tuple[str, str, str]] = []
    for raw in content:
        line = raw.strip()
        if not line:
            continue
        if set(line) == {"-"}:  # a line of only dashes -> a divider rule
            elements.append(("div", "", ""))
        elif ": " in line:  # Label: Value (split on the first ": ") -> a row
            label, value = line.split(": ", 1)
            elements.append(("row", label.strip(), value.strip()))
        else:  # no "Label: Value" -> a centered header line
            elements.append(("hdr", line, ""))

    rows = [(label, value) for kind, label, value in elements if kind == "row"]
    headers = [text for kind, text, _ in elements if kind == "hdr"]
    if not rows and not headers:  # only dividers / blanks -> nothing to draw
        return None

    label_w = max((len(label) for label, _ in rows), default=0)
    value_w = max((len(value) for _, value in rows), default=0)
    row_w = label_w + len(_GAP) + value_w if rows else 0
    content_w = max(row_w, max((len(text) for text in headers), default=0))

    # Build the body: each item is a content_w-wide string, or None for a divider rule. A header
    # gets a rule below it; consecutive/edge rules are collapsed so borders never double up.
    body: list[str | None] = []
    for kind, a, b in elements:
        if kind == "hdr":
            body.append(a.center(content_w))
            body.append(None)
        elif kind == "row":
            body.append((a.ljust(label_w) + _GAP + b).ljust(content_w))
        else:
            body.append(None)

    cleaned: list[str | None] = []
    for item in body:
        if item is None and (not cleaned or cleaned[-1] is None):
            continue
        cleaned.append(item)
    while cleaned and cleaned[-1] is None:
        cleaned.pop()

    inner = content_w + 2  # one space of padding on each side
    lines = ["┌" + "─" * inner + "┐"]
    for item in cleaned:
        lines.append("├" + "─" * inner + "┤" if item is None else f"│ {item} │")
    lines.append("└" + "─" * inner + "┘")
    return "\n".join(lines)
