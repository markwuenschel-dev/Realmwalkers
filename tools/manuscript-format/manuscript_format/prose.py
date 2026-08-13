"""Port of ``frontend/src/desk/prose.ts`` — the block + inline Markdown parser.

``parse_blocks()`` segments prose/markdown into renderable blocks so the emitters draw tables,
lists, callouts, and monospace stat windows instead of flattening everything into paragraphs.
Pure: text in, blocks out, no I/O.

Regex parity note: JavaScript's ``\\w`` and ``\\d`` are ASCII-only, Python's are Unicode-aware.
Every ported pattern therefore spells those classes out explicitly (``JS_W`` / ``[0-9]``) so the
Python parser accepts exactly what the TypeScript one accepts — no more, no less.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar, Literal

# JavaScript's ASCII-only \w. Used wherever the TS source relies on \w's ASCII semantics.
JS_W = r"[A-Za-z0-9_]"

Align = Literal["left", "center", "right"]
Tone = Literal["note", "info", "good", "warn", "bad"]

UI_ROLES: frozenset[str] = frozenset(
    {
        "system",
        "warning",
        "combat",
        "damage",
        "healing",
        "defense",
        "resource",
        "progression",
        "xp",
        "crafting",
        "insight",
        "corruption",
        "name",
        "vow",
        "item",
        "levelup",
        "skill",
        "sheet",
    }
)

MAGIC_DOMAINS: frozenset[str] = frozenset(
    {
        "fire",
        "water",
        "air",
        "earth",
        "light",
        "shadow",
        "life",
        "death",
        "runic",
        "blood",
        "spirit",
        "mind",
        "force",
        "chaos",
        "celestial",
        "void",
        "planar",
        "time",
        "entropy",
        "eldritch",
        "aether",
    }
)

CREATURE_KINDS: frozenset[str] = frozenset(
    {
        "mortal",
        "beast",
        "monster",
        "demon",
        "archdemon",
        "angel",
        "archangel",
        "undead",
        "dragon",
        "construct",
        "spirit",
        "fae",
        "celestial",
        "voidborn",
        "eldritch",
        "xyloryn",
        "nhal",
    }
)

INTENSITIES: frozenset[str] = frozenset({"subtle", "standard", "strong", "apex"})


@dataclass
class InterfaceSpec:
    """Parsed ``@interface`` / ``@style`` directive attributes."""

    role: str | None = None
    domain: str | None = None
    creature: str | None = None
    # Free-form race/species label. Unlike ``creature``, this is not restricted to the built-in
    # taxonomy, so manuscript scans can colour-code newly introduced peoples automatically.
    race: str | None = None
    intensity: str | None = None
    skill: str | None = None
    tier: str | None = None
    # Level-up banner + skill-learned + character sheet identity.
    name: str | None = None
    from_: str | None = None  # level-up: prior level (`from` is a Python keyword)
    to: str | None = None  # level-up: new level
    rank: str | None = None  # skill-learned: proficiency rank ("Novice")
    via: str | None = None  # skill-learned: how it was earned (footnote)
    age: str | None = None  # sheet identity
    level: str | None = None  # sheet identity


# ── Block types ──────────────────────────────────────────────────────────────


@dataclass
class Para:
    kind: ClassVar[str] = "p"
    text: str
    n: int


@dataclass
class Heading:
    kind: ClassVar[str] = "heading"
    level: int
    text: str


@dataclass
class TimeMark:
    """In-prose day/date marker → centered rule + label."""

    kind: ClassVar[str] = "time"
    label: str


@dataclass
class UnorderedList:
    kind: ClassVar[str] = "ul"
    items: list[str]


@dataclass
class OrderedList:
    kind: ClassVar[str] = "ol"
    items: list[str]


@dataclass
class Callout:
    kind: ClassVar[str] = "callout"
    tone: Tone
    title: str | None
    lines: list[str]


@dataclass
class Rule:
    kind: ClassVar[str] = "hr"


@dataclass
class StatWindow:
    """Pre-rendered box-drawing window (the backend's ```stat``` fences arrive already rendered)."""

    kind: ClassVar[str] = "stat"
    lines: list[str]
    spec: InterfaceSpec | None = None


@dataclass
class CodeBlock:
    kind: ClassVar[str] = "code"
    lines: list[str]
    lang: str


@dataclass
class InterfacePanel:
    kind: ClassVar[str] = "interface"
    spec: InterfaceSpec
    lines: list[str]


@dataclass
class DataTable:
    kind: ClassVar[str] = "table"
    head: list[str]
    rows: list[list[str]]
    align: list[Align]
    spec: InterfaceSpec | None = None


ProseBlock = (
    Para
    | Heading
    | TimeMark
    | UnorderedList
    | OrderedList
    | Callout
    | Rule
    | StatWindow
    | CodeBlock
    | InterfacePanel
    | DataTable
)


# ── Structural line detection (shared with beautify.py, exactly as in prose.ts) ───────────────

BOX = re.compile(r"^\s*[┌│├└]")  # first non-space char of a rendered stat-window line
FENCE = re.compile(r"^\s*```")
FENCE_CLOSE = re.compile(r"^\s*```\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
HR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")  # ---, ***, ___, - - -
UL = re.compile(r"^\s*[-*+]\s+(.*)$")
OL = re.compile(r"^\s*[0-9]+[.)]\s+(.*)$")
BQ = re.compile(r"^\s*>\s?(.*)$")


# ── In-prose day/date markers ────────────────────────────────────────────────
# A standalone time marker (a novel's "Day 47" / "March 3rd" section divider) is lifted out of body
# prose and rendered as a centered rule + label. Every heuristic form must be the WHOLE (short) line
# so ordinary prose is never captured; the explicit @day/@date/@time tag is the escape hatch.

TIME_TAG = re.compile(r"^@(day|date|time)\b\s*(.*)$", re.IGNORECASE)

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_WEEKDAYS = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
_DASH_SUFFIX = r"(?:\s*[—–-]\s*\S.*)?"  # optional " — time-of-day"; dash only (a comma keeps prose)
DAY_COUNTER = re.compile(rf"^Day\s+[0-9]+{_DASH_SUFFIX}$", re.IGNORECASE)
CALENDAR = re.compile(
    "^(?:"
    rf"(?:{_MONTHS})\s+[0-9]{{1,2}}(?:st|nd|rd|th)?(?:,?\s+[0-9]{{1,4}})?"  # March 3rd, 1998
    rf"|(?:{_WEEKDAYS}){_DASH_SUFFIX}"  # Monday — Dusk
    r"|(?:the\s+)?[0-9]{1,2}(?:st|nd|rd|th)\s+of\s+[A-Za-z][A-Za-z0-9_'’-]*"  # the 4th of Emberfall
    ")$",
    re.IGNORECASE,
)


def time_marker(line: str) -> str | None:
    """The reader-facing label if ``line`` is a standalone day/date marker, else ``None``."""
    t = line.strip()
    tag = TIME_TAG.match(t)
    if tag:
        body = tag.group(2).strip()
        if not body:
            return None
        # "@day 3" → "Day 3"; "@date …" / "@time …" keep their body verbatim.
        if tag.group(1).lower() == "day" and re.match(r"^[0-9]", body):
            return f"Day {body}"
        return body
    if len(t) > 48:  # markers are terse; a length cap guards the heuristic forms
        return None
    return t if (DAY_COUNTER.match(t) or CALENDAR.match(t)) else None


# GitHub admonitions + the Realmwalkers status tags → a callout tone.
ADMON: dict[str, Tone] = {
    "note": "note",
    "tip": "good",
    "important": "info",
    "info": "info",
    "warning": "warn",
    "caution": "bad",
    "danger": "bad",
    "lock": "note",
    "working": "info",
    "open": "warn",
    "override": "bad",
    "decision": "note",
    "halt": "bad",
    "fail": "bad",
    "pass": "good",
}


def _split_cells(line: str) -> list[str]:
    """A GFM table cell row: outer pipes stripped, split on the rest. ``\\|`` is NOT unescaped."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_delimiter(line: str) -> bool:
    """A pipe table's header/body separator: every cell is dashes with optional alignment colons."""
    if "|" not in line:
        return False
    cells = _split_cells(line)
    return len(cells) > 0 and all(re.fullmatch(r":?-+:?", c) for c in cells)


def _align_of(cell: str) -> Align:
    left = cell.startswith(":")
    right = cell.endswith(":")
    if left and right:
        return "center"
    return "right" if right else "left"


def _make_callout(inner: list[str]) -> Callout:
    tone: Tone = "note"
    title: str | None = None
    lines = list(inner)
    first = (lines[0] if lines else "").strip()

    m = re.match(rf"^\[!({JS_W}+)\]\s*(.*)$", first)  # GitHub admonition: > [!WARNING]
    if m:
        tone = ADMON.get(m.group(1).lower(), "note")
        title = m.group(1)[0].upper() + m.group(1)[1:].lower()
        lines = [m.group(2), *lines[1:]] if m.group(2) else lines[1:]
    else:
        m = re.match(r"^\[(LOCK|WORKING|OPEN|OVERRIDE)\]", first, re.IGNORECASE)
        if m:
            tone = ADMON.get(m.group(1).lower(), "note")
            title = m.group(1).upper()

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return Callout(tone=tone, title=title, lines=lines)


def _collect(lines: list[str], i: int, pattern: re.Pattern[str]) -> tuple[list[str], int]:
    items: list[str] = []
    while i < len(lines) and pattern.match(lines[i]):
        items.append(pattern.sub(r"\1", lines[i], count=1))
        i += 1
    return items, i


INTERFACE_DIRECTIVE = re.compile(r"^@interface\s+(.+)$")
# Color-codes the immediately-following stat window / pipe table by domain (same attr grammar).
STYLE_DIRECTIVE = re.compile(r"^@style\s+(.+)$")

_ATTR = re.compile(rf'({JS_W}+)=(?:"([^"]*)"|(\S+))')

_ENUM_FIELDS: dict[str, frozenset[str]] = {
    "role": UI_ROLES,
    "domain": MAGIC_DOMAINS,
    "creature": CREATURE_KINDS,
    "intensity": INTENSITIES,
}
_FREE_FIELDS = ("skill", "tier", "name", "from", "to", "rank", "via", "age", "level", "race", "species")


def parse_interface_spec(raw: str) -> InterfaceSpec:
    """Parse ``@interface role=insight creature=archdemon skill="Shadow Step" …`` into a spec.

    Values may be bare (``role=skill``) or double-quoted to include spaces (``name="Wren Calloway"``).
    An out-of-enum value for a typed field is dropped (left ``None``), matching ``asEnum``.
    """
    spec = InterfaceSpec()
    for m in _ATTR.finditer(raw):
        key = m.group(1)
        value = m.group(2) if m.group(2) is not None else m.group(3)
        if key in _ENUM_FIELDS:
            setattr(spec, key, value if value in _ENUM_FIELDS[key] else None)
        elif key in _FREE_FIELDS:
            attr = "from_" if key == "from" else "race" if key == "species" else key
            setattr(spec, attr, value)
    return spec


def parse_blocks(text: str) -> list[ProseBlock]:
    """Segment prose/markdown into renderable blocks. Pure; mirrors ``parseBlocks`` line for line."""
    out: list[ProseBlock] = []
    lines = text.split("\n")
    p = 0  # paragraph index — drop-cap parity with seg()
    i = 0
    # A `@style domain=… tier=… skill=…` directive line color-codes the stat window or pipe table
    # that immediately follows it (survives exactly one block, then clears).
    pending_style: InterfaceSpec | None = None

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        style_directive = STYLE_DIRECTIVE.match(line.strip())
        if style_directive:
            pending_style = parse_interface_spec(style_directive.group(1))
            i += 1
            continue
        # Consume any pending style for THIS block only; unrelated blocks drop it.
        style = pending_style
        pending_style = None

        # fenced code block — collect to the closing fence (or EOF if unterminated)
        if FENCE.match(line):
            lang = line.strip().lstrip("`").strip()
            start = i + 1
            j = start
            while j < len(lines) and not FENCE_CLOSE.match(lines[j]):
                j += 1
            inner = lines[start:j]
            iface = INTERFACE_DIRECTIVE.match((inner[0] if inner else "").strip())
            if iface:
                out.append(
                    InterfacePanel(spec=parse_interface_spec(iface.group(1)), lines=inner[1:])
                )
            elif lang.lower() == "stat":
                out.append(StatWindow(lines=inner, spec=style))
            else:
                out.append(CodeBlock(lines=inner, lang=lang))
            i = j + 1 if j < len(lines) else j  # step past the closing fence
            continue

        h = HEADING.match(line)
        if h:
            out.append(Heading(level=len(h.group(1)), text=h.group(2).strip()))
            i += 1
            continue

        if HR.match(line):
            out.append(Rule())
            i += 1
            continue

        # stat window — a contiguous run of box-drawing lines
        if BOX.match(line):
            start = i
            while i < len(lines) and BOX.match(lines[i]):
                i += 1
            out.append(StatWindow(lines=lines[start:i], spec=style))
            continue

        # blockquote → callout box
        if BQ.match(line):
            inner, i = _collect(lines, i, BQ)
            out.append(_make_callout(inner))
            continue

        # pipe table — a header row immediately followed by a delimiter row
        if "|" in line and i + 1 < len(lines) and _is_delimiter(lines[i + 1]):
            head = _split_cells(line)
            align = [_align_of(c) for c in _split_cells(lines[i + 1])]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_cells(lines[i]))
                i += 1
            out.append(DataTable(head=head, rows=rows, align=align, spec=style))
            continue

        # lists (one level; nested sublists are flattened in v1)
        if UL.match(line):
            items, i = _collect(lines, i, UL)
            out.append(UnorderedList(items=items))
            continue
        if OL.match(line):
            items, i = _collect(lines, i, OL)
            out.append(OrderedList(items=items))
            continue

        # standalone day/date marker → its own centered-rule block (checked last, so any structural
        # line above wins and prose is only diverted on a whole-line match)
        marker = time_marker(line)
        if marker:
            out.append(TimeMark(label=marker))
            i += 1
            continue

        # ordinary paragraph — one per non-blank line, matching seg()
        out.append(Para(text=line.strip(), n=p))
        p += 1
        i += 1

    return out


# ── Inline formatting ────────────────────────────────────────────────────────
# A flat (non-nesting) inline pass over a single line: `code`, **strong**, *em*, and [text](href).


@dataclass
class Inline:
    t: Literal["text", "code", "strong", "em", "link"]
    s: str
    href: str = field(default="")


_RE_CODE = re.compile(r"^`([^`]+)`")
_RE_STRONG_STAR = re.compile(r"^\*\*([^*]+)\*\*")
_RE_STRONG_UNDER = re.compile(r"^__([^_]+)__")
_RE_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)")
_RE_EM_STAR = re.compile(r"^\*([^*\s](?:[^*]*[^*\s])?)\*")
_RE_EM_UNDER = re.compile(rf"^_([^_\s](?:[^_]*[^_\s])?)_(?!{JS_W})")
_RE_WORD_CHAR = re.compile(JS_W)


def parse_inline(text: str) -> list[Inline]:
    """Flat inline pass over one line. Mirrors ``parseInline`` including its match precedence."""
    out: list[Inline] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            out.append(Inline("text", "".join(buf)))
            buf.clear()

    i = 0
    n = len(text)
    while i < n:
        rest = text[i:]

        m = _RE_CODE.match(rest)
        if m:
            flush()
            out.append(Inline("code", m.group(1)))
            i += len(m.group(0))
            continue

        m = _RE_STRONG_STAR.match(rest) or _RE_STRONG_UNDER.match(rest)
        if m:
            flush()
            out.append(Inline("strong", m.group(1)))
            i += len(m.group(0))
            continue

        m = _RE_LINK.match(rest)
        if m:
            flush()
            out.append(Inline("link", m.group(1), href=m.group(2)))
            i += len(m.group(0))
            continue

        m = _RE_EM_STAR.match(rest)
        if m:
            flush()
            out.append(Inline("em", m.group(1)))
            i += len(m.group(0))
            continue

        # underscore emphasis only at word boundaries — avoids snake_case false positives
        if i == 0 or not _RE_WORD_CHAR.match(text[i - 1]):
            m = _RE_EM_UNDER.match(rest)
            if m:
                flush()
                out.append(Inline("em", m.group(1)))
                i += len(m.group(0))
                continue

        buf.append(text[i])
        i += 1

    flush()
    return out


def word_count(prose: str | None) -> int:
    """Port of ``lib/format.ts`` ``wordCount``."""
    if not prose:
        return 0
    return len(re.findall(r"\S+", prose.strip()))
