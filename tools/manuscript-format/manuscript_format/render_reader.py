"""Port of the Reader DOCX emitter in ``frontend/src/desk/lib/docx.ts``.

"Many emitters, one parse" — consumes the ``parse_blocks``/``parse_inline`` AST.

* Domain A: manuscript book typography + LitRPG interface panels.
* Domain B: canon docs with professional tables/callouts.

Interface panels use Bahnschrift for labels — a condensed techy sans installed with Windows
10+/Office, so no font embedding is needed (older installs fall back to Franklin Gothic / the
platform sans). Body prose uses the book serif (Georgia) so system/magic/creature descriptions read
as part of the novel, not a terminal dump. Three layouts dispatch off the spec:

* creature       → bestiary card: coloured header band (scan label) + tinted description body
* domain (magic) → tinted body, coloured spine, inline eyebrow label
* role only      → elegant system message: centred label under a hairline rule, serif body
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from .labels import book_number_label, part_kind_word, to_roman
from .ooxml import (
    NO_BORDER,
    Border,
    Cell,
    DocxBuilder,
    Field,
    Hyperlink,
    Par,
    R,
    Row,
    Run,
    Spacing,
    TabStop,
    Tbl,
    inches_to_twips,
    line,
)
from .presets import ExportPolicy
from .prose import (
    Callout,
    CodeBlock,
    DataTable,
    Heading,
    InterfacePanel,
    InterfaceSpec,
    OrderedList,
    Para as ProsePara,
    ProseBlock,
    Rule,
    StatWindow,
    TimeMark,
    UnorderedList,
    parse_blocks,
    parse_inline,
)
from .spine import (
    ManuscriptSpine,
    SpineChapterNode,
    SpinePartNode,
    SpineVolumeNode,
    plan_reader_production,
    spine_chapters,
)
from .styles import STYLE, reader_style_defs
from .surfaces import (
    PALETTE,
    Surface,
    format_interface_header,
    neutral_surface,
    resolve_surface,
    table_surface,
)

#: The three export formats every prose-bearing screen offers.
EXPORT_KINDS = ("md", "docx", "shunn")

TONE_COLOR: dict[str, str] = {
    "note": "1F3864",
    "info": "2E5AAC",
    "good": "2F7D57",
    "warn": "9A6A1F",
    "bad": "A23A52",
}

# Day/date marker divider: the litRPG `time` surface accent for the label, a muted rule flanking it.
TIME_ACCENT = "B45309"
TIME_RULE = "9C9C9C"

CELL_MARGINS = {"top": 60, "bottom": 60, "left": 110, "right": 110}

LABEL_FONT = "Bahnschrift"
BODY_SERIF = "Georgia"

_PANEL_BORDER_KEYS = ("top", "bottom", "right", "left", "insideH", "insideV")


def _empty_par() -> Par:
    """docx-js ``new Paragraph("")`` — a paragraph carrying one empty run."""
    return Par(children=[R("")])


def time_marker_para(label: str) -> Par:
    """A centred rule with the day/date label sitting on it — ``⸻  DAY 3  ⸻``."""
    return Par(
        alignment="center",
        spacing=Spacing(before=260, after=260),
        children=[
            R("⸻  ", color=TIME_RULE, size=22),
            R(
                label,
                bold=True,
                all_caps=True,
                character_spacing=30,
                color=TIME_ACCENT,
                font="Georgia",
                size=20,
            ),
            R("  ⸻", color=TIME_RULE, size=22),
        ],
    )


def inline_runs(text: str, base: dict | None = None) -> list[Run | Hyperlink]:
    """Map the inline AST onto runs, carrying a base font/size/colour into each."""
    b = base or {}
    out: list[Run | Hyperlink] = []
    for tok in parse_inline(text):
        if tok.t == "code":
            out.append(R(tok.s, **{**b, "font": "Consolas"}))
        elif tok.t == "strong":
            out.append(R(tok.s, **b, bold=True))
        elif tok.t == "em":
            out.append(R(tok.s, **b, italics=True))
        elif tok.t == "link":
            out.append(
                Hyperlink(
                    href=tok.href,
                    children=[R(tok.s, **{**b, "color": "0563C1"}, underline=True)],
                )
            )
        else:
            out.append(R(tok.s, **b))
    return out


def panel(rows: list[Row], surface: Surface) -> Tbl:
    """Layout-only panel — callers supply pre-coloured rows."""
    for row in rows:
        row.cant_split = True
    accent = line(surface.accent, surface.left_border_size)
    outer = line(surface.border, 4)
    return Tbl(
        width_pct=100,
        borders={
            "top": outer,
            "bottom": outer,
            "right": outer,
            "left": accent,
            "insideH": NO_BORDER,
            "insideV": NO_BORDER,
        },
        rows=rows,
    )


def single_cell_panel(children: list[Par], surface: Surface) -> Tbl:
    return panel(
        [Row(cells=[Cell(shading=surface.fill, margins=CELL_MARGINS, children=list(children))])],
        surface,
    )


def callout_panel(b: Callout) -> Tbl:
    accent = TONE_COLOR[b.tone]
    surface = neutral_surface()
    children: list[Par] = []
    if b.title:
        children.append(
            Par(
                spacing=Spacing(after=60),
                children=[R(b.title.upper(), bold=True, color=accent, size=18)],
            )
        )
    for ln in b.lines:
        if ln.strip():
            children.append(
                Par(spacing=Spacing(after=40), children=inline_runs(ln, {"color": surface.text}))
            )
    if not children:
        children.append(_empty_par())
    return panel(
        [Row(cells=[Cell(shading=surface.fill, margins=CELL_MARGINS, children=children)])],
        replace(surface, accent=accent),
    )


def mono_panel(lines: list[str], surface: Surface) -> Tbl:
    rows = lines if lines else [""]
    return single_cell_panel(
        [
            Par(
                spacing=Spacing(after=0, line=240, line_rule="auto"),
                children=[R(ln or " ", font="Consolas", size=18, color=surface.text)],
            )
            for ln in rows
        ],
        surface,
    )


def _clean_readout_lines(lines: list[str]) -> list[str]:
    """Remove pre-rendered box borders while preserving the values inside them."""
    cleaned: list[str] = []
    for raw in lines:
        line_text = raw.strip()
        if not line_text:
            cleaned.append("")
            continue
        if line_text[0] in "┌├└┬┴┼─" and all(ch in "┌┐└┘├┤┬┴┼─ " for ch in line_text):
            continue
        if line_text.startswith("│"):
            line_text = line_text[1:]
        if line_text.endswith("│"):
            line_text = line_text[:-1]
        cleaned.append(line_text.strip())
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return cleaned or ["INTERFACE"]


_FIELD_LINE = re.compile(r"^([^:]{1,24}):\s*(.*)$")
_READOUT_ORDER = ("Name", "Level", "Health", "Stamina", "Mana", "Race")
_READOUT_LABEL = {
    "Level": "LVL",
    "Health": "HP",
    "Stamina": "STAMINA",
    "Mana": "MANA",
    "Race": "RACE",
}


def _field_map(lines: list[str]) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for raw in lines:
        if not raw.strip():
            continue
        m = _FIELD_LINE.match(raw.strip())
        if not m:
            return None
        fields[m.group(1).strip()] = m.group(2).strip()
    return fields if fields else None


def readout_panel(lines: list[str]) -> Tbl:
    """Compact steel-blue interface card modelled on the one-line readout reference."""
    surface = resolve_surface(InterfaceSpec(role="system"))
    cleaned = _clean_readout_lines(lines)
    first = cleaned[0].strip()

    # Pure key/value scans become a single compact readout line.
    fields = _field_map(cleaned)
    if fields is not None:
        ordered = [k for k in _READOUT_ORDER if k in fields]
        ordered.extend(k for k in fields if k not in ordered)
        runs: list[Run | Hyperlink] = []
        for i, key in enumerate(ordered):
            if i:
                runs.append(R("     ", font=LABEL_FONT, size=14, color=surface.text))
            value = fields[key]
            if key == "Name":
                runs.append(
                    R(
                        value,
                        font=LABEL_FONT,
                        bold=True,
                        color=surface.text,
                        size=19,
                    )
                )
            else:
                runs.append(
                    R(
                        f"{_READOUT_LABEL.get(key, key.upper())} ",
                        font=LABEL_FONT,
                        bold=True,
                        character_spacing=8,
                        color=surface.label_color,
                        size=14,
                    )
                )
                runs.append(R(value, font=BODY_SERIF, color=surface.text, size=18))
        return single_cell_panel(
            [Par(spacing=Spacing(after=0), children=runs)],
            surface,
        )

    if first.startswith("[") and first.endswith("]"):
        title = first.strip("[] ")
        body = cleaned[1:]
    elif first and first.upper() == first and ":" not in first and len(first) <= 48:
        title = first
        body = cleaned[1:]
    else:
        title = "INTERFACE"
        body = cleaned

    children: list[Par] = [
        Par(
            spacing=Spacing(after=65),
            borders={"bottom": Border(style="single", size=4, color=surface.border, space=4)},
            children=[
                R(
                    title,
                    font=LABEL_FONT,
                    bold=True,
                    character_spacing=10,
                    color=surface.label_color,
                    size=18,
                )
            ],
        )
    ]
    for i, raw in enumerate(body):
        if not raw.strip():
            continue
        m = _FIELD_LINE.match(raw.strip())
        if m:
            children.append(
                Par(
                    spacing=Spacing(after=45, line=260, line_rule="auto"),
                    children=[
                        R(
                            f"{m.group(1).strip().upper()}  ",
                            font=LABEL_FONT,
                            bold=True,
                            character_spacing=8,
                            color=surface.label_color,
                            size=14,
                        ),
                        R(m.group(2).strip(), font=BODY_SERIF, color=surface.text, size=20),
                    ],
                )
            )
        else:
            children.append(
                Par(
                    spacing=Spacing(after=45, line=270, line_rule="auto"),
                    children=[
                        R(
                            raw.strip(),
                            font=LABEL_FONT if i == 0 else BODY_SERIF,
                            bold=i == 0,
                            color=surface.text,
                            size=20,
                        )
                    ],
                )
            )
    return single_cell_panel(children, surface)


def status_sheet_panel(lines: list[str]) -> Tbl:
    """Render a boxed ``stat`` block as the in-story amber character sheet."""
    cleaned = _clean_readout_lines(lines)
    title = "CURRENT STATUS"
    if cleaned and cleaned[0].upper() == cleaned[0] and ":" not in cleaned[0]:
        title = cleaned.pop(0)
    fields = _field_map(cleaned) or {}
    cols = 3
    rows: list[Row] = []
    compact_margins = {"top": 20, "bottom": 20, "left": 110, "right": 110}

    rows.append(
        Row(
            cells=[
                Cell(
                    col_span=cols,
                    shading=SHEET.section,
                    margins=compact_margins,
                    children=[
                        Par(
                            alignment="center",
                            spacing=Spacing(after=0),
                            children=[
                                R(
                                    title.upper(),
                                    font=LABEL_FONT,
                                    bold=True,
                                    character_spacing=34,
                                    color=SHEET.section_text,
                                    size=17,
                                )
                            ],
                        )
                    ],
                )
            ]
        )
    )

    identity = (("Name", fields.get("Name", "????")), ("Race", fields.get("Race", "????")), ("Level", fields.get("Level", "????")))
    rows.append(
        Row(
            cells=[
                Cell(
                    width_pct=100 / cols,
                    shading=SHEET.identity,
                    margins=compact_margins,
                    children=[
                        Par(
                            spacing=Spacing(after=0),
                            children=[
                                R(
                                    f"{key.upper()} ",
                                    font=LABEL_FONT,
                                    bold=True,
                                    character_spacing=14,
                                    color=SHEET.identity_label,
                                    size=15,
                                ),
                                R(value, font=BODY_SERIF, color=SHEET.identity_value, size=21),
                            ],
                        )
                    ],
                )
                for key, value in identity
            ]
        )
    )

    rows.append(
        Row(
            cells=[
                Cell(
                    col_span=cols,
                    shading=SHEET.fill,
                    margins=compact_margins,
                    children=[
                        Par(
                            alignment="center",
                            spacing=Spacing(after=0),
                            children=[
                                R(
                                    "CLASS ",
                                    font=LABEL_FONT,
                                    bold=True,
                                    character_spacing=10,
                                    color=SHEET.label,
                                    size=15,
                                ),
                                R(fields.get("Class", "Unassigned"), font=BODY_SERIF, color=SHEET.ink, size=21),
                            ],
                        )
                    ],
                )
            ]
        )
    )

    rows.append(
        Row(
            cells=[
                Cell(
                    col_span=cols,
                    shading=SHEET.section,
                    margins=compact_margins,
                    children=[
                        Par(
                            alignment="center",
                            spacing=Spacing(after=0),
                            children=[
                                R(
                                    "STATS",
                                    font=LABEL_FONT,
                                    bold=True,
                                    character_spacing=34,
                                    color=SHEET.section_text,
                                    size=17,
                                )
                            ],
                        )
                    ],
                )
            ]
        )
    )
    rows.append(
        Row(
            cells=[
                sheet_cell(f"{key}: {fields.get(key, '????')}", cols, section="STATS", align="center")
                for key in ("Health", "Mana", "Stamina")
            ]
        )
    )

    rows.append(
        Row(
            cells=[
                Cell(
                    col_span=cols,
                    shading=SHEET.section,
                    margins=compact_margins,
                    children=[
                        Par(
                            alignment="center",
                            spacing=Spacing(after=0),
                            children=[
                                R(
                                    "ABILITIES",
                                    font=LABEL_FONT,
                                    bold=True,
                                    character_spacing=34,
                                    color=SHEET.section_text,
                                    size=17,
                                )
                            ],
                        )
                    ],
                )
            ]
        )
    )
    rows.append(
        Row(
            cells=[
                Cell(
                    col_span=cols,
                    shading=SHEET.fill,
                    margins=compact_margins,
                    children=[
                        Par(
                            alignment="center",
                            spacing=Spacing(after=0),
                            children=[
                                R(fields.get("Skills", "None"), font=BODY_SERIF, color=SHEET.ink, size=21)
                            ],
                        )
                    ],
                )
            ]
        )
    )

    for row in rows:
        row.cant_split = True

    g = line(SHEET.grid, 4)
    outer = line(SHEET.border, 4)
    return Tbl(
        width_pct=100,
        borders={
            "top": outer,
            "bottom": outer,
            "left": outer,
            "right": outer,
            "insideH": g,
            "insideV": NO_BORDER,
        },
        rows=rows,
    )

def display_label(spec: InterfaceSpec) -> str:
    """Header text without the mono brackets: ``[ SKILL ] FIRE · TIER III`` → ``SKILL · FIRE · TIER III``."""
    s = format_interface_header(spec)
    s = re.sub(r"\[\s*", "", s)
    s = re.sub(r"\s*\]", " ·", s)
    s = re.sub(r"\s*·\s*", " · ", s)
    s = re.sub(r"\s*·\s*$", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def interface_body(lines: list[str], surface: Surface) -> list[Par]:
    """Interface body prose as book-serif paragraphs (inline bold / em / code preserved)."""
    ps: list[Par] = []
    for ln in lines:
        if ln.strip():
            ps.append(
                Par(
                    spacing=Spacing(after=60, line=288, line_rule="auto"),
                    children=inline_runs(
                        ln, {"font": BODY_SERIF, "size": 21, "color": surface.text}
                    ),
                )
            )
        else:
            ps.append(Par(spacing=Spacing(after=60), children=[R("")]))
    if not ps:
        ps.append(_empty_par())
    return ps


def band_row(label: str, surface: Surface, right_label: str | None = None) -> Row:
    """A single-cell coloured header band carrying a label; optional right-aligned category tag."""
    children: list[Run | Hyperlink] = [
        R(
            label,
            font=LABEL_FONT,
            bold=True,
            all_caps=True,
            character_spacing=24,
            color=surface.header_text,
            size=15,
        )
    ]
    if right_label:
        children.append(R("\t", font=LABEL_FONT, color=surface.header_text))
        children.append(
            R(
                right_label,
                font=LABEL_FONT,
                bold=True,
                all_caps=True,
                character_spacing=20,
                color=surface.header_text,
                size=12,
            )
        )
    return Row(
        cells=[
            Cell(
                shading=surface.header_fill,
                margins=CELL_MARGINS,
                children=[
                    Par(
                        spacing=Spacing(after=0),
                        tab_stops=(
                            [TabStop("right", inches_to_twips(6.3))] if right_label else []
                        ),
                        children=children,
                    )
                ],
            )
        ]
    )


def body_row(lines: list[str], surface: Surface) -> Row:
    return Row(
        cells=[
            Cell(
                shading=surface.fill,
                margins=CELL_MARGINS,
                children=interface_body(lines, surface),
            )
        ]
    )


def magic_panel(b: InterfacePanel, surface: Surface) -> Tbl:
    """Magic/skill block: colour the header band with the domain palette, then the tinted body."""
    s = b.spec
    parts: list[str] = []
    if s.skill:
        parts.append(s.skill)
    if s.domain:
        parts.append(s.domain)
    if s.tier:
        parts.append(f"Tier {s.tier}")
    label = "  ·  ".join(parts) or display_label(s)
    return panel([band_row(label, surface), body_row(b.lines, surface)], surface)


def system_panel(b: InterfacePanel, surface: Surface) -> Tbl:
    """System / role message: a soft readout card with a ruled title line and serif body."""
    return single_cell_panel(
        [
            Par(
                spacing=Spacing(after=70),
                borders={"bottom": Border(style="single", size=4, color=surface.border, space=4)},
                children=[
                    R(
                        display_label(b.spec),
                        font=LABEL_FONT,
                        bold=True,
                        character_spacing=10,
                        color=surface.label_color,
                        size=20,
                    )
                ],
            ),
            *interface_body(b.lines, surface),
        ],
        surface,
    )


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def threat_label(intensity: str | None) -> str | None:
    """Threat readout derived from the block's intensity."""
    return {
        "subtle": "Minor",
        "standard": "Standard",
        "strong": "Severe",
        "apex": "Apex",
    }.get(intensity or "")


def creature_meta_row(s: InterfaceSpec, surface: Surface) -> Row | None:
    """Ruled bestiary sub-field strip — KIND · THREAT · DOMAIN, with a bottom rule under it."""
    fields: list[tuple[str, str, str | None]] = []
    if s.creature:
        fields.append(("Kind", _cap(s.creature), None))
    threat = threat_label(s.intensity)
    if threat:
        fields.append(("Threat", threat, PALETTE.crimson))
    if s.domain:
        fields.append(("Domain", _cap(s.domain), None))
    if not fields:
        return None

    runs: list[Run | Hyperlink] = []
    for i, (k, v, color) in enumerate(fields):
        if i > 0:
            runs.append(R("        ", size=18))
        runs.append(
            R(
                f"{k.upper()}  ",
                font=LABEL_FONT,
                bold=True,
                character_spacing=12,
                color="9A8F7D",
                size=13,
            )
        )
        runs.append(R(v, font=BODY_SERIF, color=color or surface.text, size=19))
    return Row(
        cells=[
            Cell(
                shading=surface.fill,
                margins=CELL_MARGINS,
                borders={"bottom": line(surface.border, 4)},
                children=[Par(spacing=Spacing(after=0), children=runs)],
            )
        ]
    )


def creature_panel(b: InterfacePanel, surface: Surface) -> Tbl:
    """Creature scan: bestiary card — name band, ruled fields, tinted description."""
    name = b.spec.skill if b.spec.skill else display_label(b.spec)
    rows: list[Row] = [band_row(name, surface, "Bestiary")]
    meta = creature_meta_row(b.spec, surface)
    if meta:
        rows.append(meta)
    rows.append(body_row(b.lines, surface))
    return panel(rows, surface)


class GOLD:
    """Fixed gold treatment for the celebratory level-up banner (louder than a system message)."""

    accent = "B8901C"
    band = "1C1608"
    rule = "E5B52A"
    fill = "FFFDF4"
    border = "D8BF6A"
    ink = "3A352F"
    label_gold = "E5B52A"
    sub_gold = "8A7A4E"
    field_label = "B09A55"
    grid = "ECDFAE"
    muted = "9C832F"


GAIN = "1A9D3F"
LOSS = "B4231F"


@dataclass
class Delta:
    """A ``- Label: old -> new`` (or single-value) line parsed for the vitals grid."""

    label: str
    value: str
    delta: str | None = None
    color: str | None = None


DELTA_LINE = re.compile(r"^\s*-\s*(.+?):\s*(.+?)\s*(?:->|→)\s*(.+?)\s*$")


def _js_number(s: str) -> float:
    """JavaScript ``Number(s)`` semantics: empty/whitespace is 0, malformed is NaN."""
    t = s.strip()
    if not t:
        return 0.0
    try:
        return float(t)
    except ValueError:
        return math.nan


def parse_deltas(lines: list[str]) -> tuple[list[str], list[Delta]]:
    body: list[str] = []
    deltas: list[Delta] = []
    for ln in lines:
        m = DELTA_LINE.match(ln)
        if m:
            label, old_v, new_v = m.group(1), m.group(2), m.group(3)
            on = _js_number(re.sub(r"[^0-9.\-]", "", old_v))
            nn = _js_number(re.sub(r"[^0-9.\-]", "", new_v))
            if not math.isnan(on) and not math.isnan(nn):
                color = GAIN if nn >= on else LOSS
            else:
                color = GAIN
            deltas.append(Delta(label=label, value=old_v, delta=f"→ {new_v}", color=color))
        elif ln.strip():
            body.append(ln)
    return body, deltas


def gains_grid(deltas: list[Delta]) -> Tbl:
    """3-up grid of stat cells (label + value → new), gold-ruled."""
    rows: list[Row] = []
    for i in range(0, len(deltas), 3):
        chunk = list(deltas[i : i + 3])
        while len(chunk) < 3:
            chunk.append(Delta(label="", value=""))
        cells: list[Cell] = []
        for d in chunk:
            label_runs: list[Run | Hyperlink] = (
                [
                    R(
                        d.label.upper(),
                        font=LABEL_FONT,
                        bold=True,
                        character_spacing=12,
                        color=GOLD.field_label,
                        size=15,
                    )
                ]
                if d.label
                else [R("")]
            )
            value_runs: list[Run | Hyperlink] = (
                [
                    R(f"{d.value} ", font=BODY_SERIF, color=GOLD.ink, size=22),
                    R(
                        d.delta or "",
                        font=BODY_SERIF,
                        bold=True,
                        color=d.color or GAIN,
                        size=22,
                    ),
                ]
                if d.label
                else [R("")]
            )
            cells.append(
                Cell(
                    width_pct=33.33,
                    shading=GOLD.fill,
                    margins=CELL_MARGINS,
                    children=[
                        Par(spacing=Spacing(after=20), children=label_runs),
                        Par(spacing=Spacing(after=0), children=value_runs),
                    ],
                )
            )
        rows.append(Row(cells=cells))

    grid = line(GOLD.grid, 4)
    return Tbl(
        width_pct=100,
        borders={
            "top": grid,
            "bottom": grid,
            "left": grid,
            "right": grid,
            "insideH": grid,
            "insideV": grid,
        },
        rows=rows,
    )


def levelup_panel(b: InterfacePanel) -> Tbl:
    """Level-up banner: loud dark band (LEVEL UP + from→to), announcement body, vitals grid."""
    s = b.spec
    body, deltas = parse_deltas(b.lines)

    band_runs: list[Run | Hyperlink] = [
        R(
            "Level Up",
            font=LABEL_FONT,
            bold=True,
            all_caps=True,
            character_spacing=60,
            color=GOLD.label_gold,
            size=17,
        )
    ]
    if s.from_ or s.to:
        band_runs.append(R("\t", font=LABEL_FONT))
        if s.from_:
            band_runs.append(
                R(f"{s.from_} → ", font=LABEL_FONT, color=GOLD.sub_gold, size=22)
            )
        if s.to:
            band_runs.append(R(s.to, font=LABEL_FONT, bold=True, color=GOLD.label_gold, size=40))

    band_children: list[Par] = [
        Par(
            spacing=Spacing(after=40 if s.name else 0),
            tab_stops=[TabStop("right", inches_to_twips(6.3))],
            children=band_runs,
        )
    ]
    if s.name:
        band_children.append(
            Par(
                spacing=Spacing(after=0),
                children=[R(s.name, font=BODY_SERIF, italics=True, color="F3EAD0", size=24)],
            )
        )

    band = Row(
        cells=[
            Cell(
                shading=GOLD.band,
                margins=CELL_MARGINS,
                borders={"bottom": line(GOLD.rule, 18)},
                children=band_children,
            )
        ]
    )

    body_children: list[Par | Tbl] = []
    for ln in body:
        body_children.append(
            Par(
                spacing=Spacing(after=80, line=288, line_rule="auto"),
                children=inline_runs(ln, {"font": BODY_SERIF, "size": 21, "color": GOLD.ink}),
            )
        )
    if deltas:
        body_children.append(
            Par(
                spacing=Spacing(before=40, after=80),
                children=[
                    R(
                        "Vitals restored & grown",
                        font=LABEL_FONT,
                        bold=True,
                        all_caps=True,
                        character_spacing=24,
                        color=GOLD.muted,
                        size=15,
                    )
                ],
            )
        )
        body_children.append(gains_grid(deltas))

    body_row_cell = Row(
        cells=[
            Cell(
                shading=GOLD.fill,
                margins=CELL_MARGINS,
                children=body_children if body_children else [_empty_par()],
            )
        ]
    )

    gold_surface = Surface(
        accent=GOLD.accent,
        fill=GOLD.fill,
        header_fill=GOLD.band,
        border=GOLD.border,
        text=GOLD.ink,
        header_text=GOLD.label_gold,
        label_color=GOLD.accent,
        left_border_size=16,
    )
    return panel([band, body_row_cell], gold_surface)


def skill_panel(b: InterfacePanel, surface: Surface) -> Tbl:
    """Skill learned: domain-coded acquisition — band, body, ``via`` footnote."""
    s = b.spec
    name_parts = ["Skill Learned"]
    if s.domain:
        name_parts.append(_cap(s.domain))
    tag = " · ".join([v for v in (s.rank, s.tier) if v]) or None

    rows: list[Row] = [band_row("  ·  ".join(name_parts), surface, tag)]
    body_children: list[Par] = []
    if s.skill:
        body_children.append(
            Par(
                spacing=Spacing(after=40),
                children=[R(s.skill, font=BODY_SERIF, bold=True, color=surface.text, size=22)],
            )
        )
    body_children.extend(interface_body(b.lines, surface))
    if s.via:
        body_children.append(
            Par(
                spacing=Spacing(before=40, after=0),
                children=[
                    R(
                        f"Learned through use — {s.via}.",
                        font=BODY_SERIF,
                        italics=True,
                        color=surface.label_color,
                        size=19,
                    )
                ],
            )
        )
    rows.append(
        Row(cells=[Cell(shading=surface.fill, margins=CELL_MARGINS, children=body_children)])
    )
    return panel(rows, surface)


def interface_panel(b: InterfacePanel) -> Tbl:
    if b.spec.role == "levelup":
        return levelup_panel(b)
    surface = resolve_surface(b.spec)
    if b.spec.role == "skill":
        return skill_panel(b, surface)
    if b.spec.creature:
        return creature_panel(b, surface)
    if b.spec.domain:
        return magic_panel(b, surface)
    return system_panel(b, surface)


def domain_accent(domain: str) -> str:
    return resolve_surface(InterfaceSpec(domain=domain)).accent


class SHEET:
    """Colours for the amber character-sheet layout (``role=sheet``)."""

    identity = "EFEF39"
    identity_label = "C69616"
    identity_value = "1F1A17"
    section = "F1F0D8"
    section_text = "BA7E00"
    grid = "ECE7C7"
    fill = "F7F6F2"
    label = "E2D96B"
    ink = "2F2A26"
    border = "EDE4A8"


SECTION_CELL = re.compile(r"^#\s*(.+)$")  # `| # STATS |`
PIP_CELL = re.compile(r"^~([A-Za-z0-9_]+)\s+(.+)$")  # `~fire Fire +40%`


def sheet_cell(
    text: str,
    cols: int,
    span: int | None = None,
    *,
    section: str | None = None,
    align: str = "left",
) -> Cell:
    body = text.strip()
    pip = PIP_CELL.match(body)
    base_align = "center" if align == "center" else "left"

    if section == "STATS":
        colon = body.find(":")
        if 0 < colon < 18:
            label = body[:colon].strip().upper()
            value = body[colon + 1 :].strip()
            return Cell(
                col_span=span,
                width_pct=None if span else 100 / cols,
                shading=SHEET.fill,
                margins=CELL_MARGINS,
                children=[
                    Par(
                        alignment="center",
                        spacing=Spacing(after=20),
                        children=[
                            R(
                                label,
                                font=LABEL_FONT,
                                bold=True,
                                character_spacing=14,
                                color=SHEET.label,
                                size=14,
                            )
                        ],
                    ),
                    Par(
                        alignment="center",
                        spacing=Spacing(after=0),
                        children=[R(value, font=BODY_SERIF, color=SHEET.ink, size=24)],
                    ),
                ],
            )

    runs: list[Run | Hyperlink] = []
    if pip:
        dom = pip.group(1)
        body = pip.group(2)
        runs.append(R("■ ", color=domain_accent(dom), size=18))

    colon = body.find(":")
    if 0 < colon < 18:
        runs.append(
            R(
                f"{body[:colon].upper()} ",
                font=LABEL_FONT,
                bold=True,
                character_spacing=8,
                color=SHEET.label,
                size=15,
            )
        )
        runs.append(R(body[colon + 1 :].strip(), font=BODY_SERIF, color=SHEET.ink, size=21))
    else:
        runs.append(R(body, font=BODY_SERIF, color=SHEET.ink, size=21))

    return Cell(
        col_span=span,
        width_pct=None if span else 100 / cols,
        shading=SHEET.fill,
        margins=CELL_MARGINS,
        children=[Par(alignment=base_align, spacing=Spacing(after=0), children=runs)],
    )


def character_sheet(b: DataTable) -> Tbl:
    """Amber character sheet: identity band, soft section bars, and colour pips."""
    s = b.spec or InterfaceSpec()
    all_rows = [b.head, *b.rows]
    cols = max([1, *[len(r) for r in all_rows]])
    rows: list[Row] = []
    current_section: str | None = None

    # Identity band: name / age / level from the directive.
    id_fields: list[tuple[str, str]] = []
    if s.name:
        id_fields.append(("Name", s.name))
    if s.age:
        id_fields.append(("Age", s.age))
    if s.level:
        id_fields.append(("Level", s.level))
    if id_fields:
        rows.append(
            Row(
                cells=[
                    Cell(
                        col_span=(cols - len(id_fields) + 1) if i == len(id_fields) - 1 else 1,
                        shading=SHEET.identity,
                        margins=CELL_MARGINS,
                        children=[
                            Par(
                                spacing=Spacing(after=0),
                                children=[
                                    R(
                                        f"{k.upper()} ",
                                        font=LABEL_FONT,
                                        bold=True,
                                        character_spacing=14,
                                        color=SHEET.identity_label,
                                        size=15,
                                    ),
                                    R(v, font=BODY_SERIF, color=SHEET.identity_value, size=21),
                                ],
                            )
                        ],
                    )
                    for i, (k, v) in enumerate(id_fields)
                ]
            )
        )

    for r in all_rows:
        first = (r[0] if r else "").strip()
        section = SECTION_CELL.match(first)
        if section and len([c for c in r if c.strip()]) == 1:
            current_section = section.group(1).strip().upper()
            rows.append(
                Row(
                    cells=[
                        Cell(
                            col_span=cols,
                            shading=SHEET.section,
                            margins=CELL_MARGINS,
                            children=[
                                Par(
                                    alignment="center",
                                    spacing=Spacing(after=0),
                                    children=[
                                        R(
                                            current_section,
                                            font=LABEL_FONT,
                                            bold=True,
                                            character_spacing=34,
                                            color=SHEET.section_text,
                                            size=17,
                                        )
                                    ],
                                )
                            ],
                        )
                    ]
                )
            )
            continue

        cells = list(r)
        nonempty = [c for c in cells if c.strip()]
        while len(cells) < cols:
            cells.append("")

        if current_section == "ABILITIES" and len(nonempty) == 1:
            rows.append(
                Row(
                    cells=[sheet_cell(nonempty[0], cols, span=cols, section=current_section, align="center")]
                )
            )
            continue

        rows.append(
            Row(
                cells=[
                    sheet_cell(
                        c,
                        cols,
                        section=current_section,
                        align="center" if current_section == "STATS" else "left",
                    )
                    for c in cells
                ]
            )
        )

    g = line(SHEET.grid, 4)
    outer = line(SHEET.border, 4)
    return Tbl(
        width_pct=100,
        borders={
            "top": outer,
            "bottom": outer,
            "left": outer,
            "right": outer,
            "insideH": g,
            "insideV": NO_BORDER,
        },
        rows=rows,
    )


_ALIGN_OF = {"center": "center", "right": "right", "left": "left"}


def data_table(b: DataTable) -> Tbl:
    if b.spec is not None and b.spec.role == "sheet":
        return character_sheet(b)
    # A `@style domain=…` directive colour-codes the table; otherwise it stays neutral.
    surface = resolve_surface(b.spec) if (b.spec and b.spec.domain) else table_surface()
    cols = len(b.head)

    def align(i: int) -> str:
        return _ALIGN_OF.get(b.align[i] if i < len(b.align) else "left", "left")

    # Optional domain band spanning all columns.
    band_rows: list[Row] = []
    if b.spec and b.spec.domain:
        s = b.spec
        parts: list[str] = []
        if s.skill:
            parts.append(s.skill)
        parts.append(s.domain)
        if s.tier:
            parts.append(f"Tier {s.tier}")
        band_rows.append(
            Row(
                cells=[
                    Cell(
                        col_span=cols,
                        shading=surface.header_fill,
                        margins=CELL_MARGINS,
                        children=[
                            Par(
                                spacing=Spacing(after=0),
                                tab_stops=[TabStop("right", inches_to_twips(6.3))],
                                children=[
                                    R(
                                        "  ·  ".join(parts),
                                        font=LABEL_FONT,
                                        bold=True,
                                        all_caps=True,
                                        character_spacing=22,
                                        color=surface.header_text,
                                        size=15,
                                    ),
                                    R("\t", font=LABEL_FONT, color=surface.header_text),
                                    R(
                                        "Bestiary" if s.creature else "Stats",
                                        font=LABEL_FONT,
                                        bold=True,
                                        all_caps=True,
                                        character_spacing=20,
                                        color=surface.header_text,
                                        size=12,
                                    ),
                                ],
                            )
                        ],
                    )
                ]
            )
        )

    header = Row(
        header=True,
        cells=[
            Cell(
                shading=surface.header_fill,
                margins=CELL_MARGINS,
                children=[
                    Par(
                        alignment=align(i),
                        children=[
                            R(
                                h,
                                font=LABEL_FONT,
                                bold=True,
                                character_spacing=12,
                                color=surface.header_text,
                            )
                        ],
                    )
                ],
            )
            for i, h in enumerate(b.head)
        ],
    )

    body: list[Row] = []
    for ri, r in enumerate(b.rows):
        cells: list[Cell] = []
        for i, c in enumerate(r):
            fill = PALETTE.paper if ri % 2 == 0 else PALETTE.pale
            if i == 0:
                borders = {
                    "top": line(surface.border),
                    "bottom": line(surface.border),
                    "left": line(surface.accent, 12),
                    "right": line(surface.border),
                }
            else:
                borders = {
                    "top": line(surface.border),
                    "bottom": line(surface.border),
                    "left": line(surface.border),
                    "right": line(surface.border),
                }
            cells.append(
                Cell(
                    shading=fill,
                    margins=CELL_MARGINS,
                    borders=borders,
                    children=[
                        Par(alignment=align(i), children=inline_runs(c, {"color": surface.text}))
                    ],
                )
            )
        body.append(Row(cells=cells))

    edge = line(surface.border)
    return Tbl(
        width_pct=100,
        borders={"top": edge, "bottom": edge, "left": edge, "right": edge, "insideH": edge, "insideV": edge},
        rows=[*band_rows, header, *body],
    )


# When present, book body paragraphs reference the named Body / BodyFirst styles instead of carrying
# inline font/size — the style provides the typography, keeping this file's layout code free of
# one-off run formatting.
BodyStyleIds = tuple[str, str] | None  # (body, first)


_WORKING_DRAFT_RE = re.compile(
    r"^WORKING\s+DRAFT\s*[-—–]\s*PROVISIONAL$", re.IGNORECASE
)


def _is_working_draft_marker(text: str) -> bool:
    return bool(_WORKING_DRAFT_RE.match(text.strip()))


def working_draft_para(text: str = "WORKING DRAFT — PROVISIONAL") -> Par:
    """Reference-doc fallback. Reader books move this marker into the running header."""
    return Par(
        alignment="center",
        spacing=Spacing(before=20, after=20),
        children=[R(text.strip(), font=BODY_SERIF, bold=True, color="B4231F", size=19)],
    )


def working_draft_header(text: str = "WORKING DRAFT — PROVISIONAL") -> list[Par]:
    return [
        Par(
            alignment="center",
            spacing=Spacing(after=0),
            children=[R(text, font=BODY_SERIF, bold=True, color="B4231F", size=17)],
        )
    ]


def chapter_context_para(text: str, *, location: bool = False) -> Par:
    # One point before and after, matching the compact chapter-opening reference.
    return Par(
        alignment="center",
        spacing=Spacing(before=20, after=20),
        children=[
            R(
                text.strip(),
                font=BODY_SERIF,
                bold=not location,
                italics=location,
                color="475569",
                size=20,
            )
        ],
    )


def drop_cap_block(
    text: str,
    body_styles: BodyStyleIds,
    following_paragraphs: list[str] | None = None,
) -> Tbl | Par:
    """Render a stable three-line visual drop cap, even when the opening paragraph is short."""
    if not text:
        return para_for(text, True, False, body_styles)
    index = next((i for i, ch in enumerate(text) if not ch.isspace()), 0)
    prefix = text[:index]
    cap = text[index:index + 1]
    remainder = prefix + text[index + 1:]
    if not cap or not cap.isalpha():
        return para_for(text, True, False, body_styles)
    body_id, first_id = body_styles or (None, None)
    right_children = [Par(style=first_id, children=inline_runs(remainder))]
    for paragraph in following_paragraphs or []:
        right_children.append(Par(style=body_id, children=inline_runs(paragraph)))
    none = NO_BORDER
    return Tbl(
        width_pct=100,
        borders={
            "top": none,
            "bottom": none,
            "left": none,
            "right": none,
            "insideH": none,
            "insideV": none,
        },
        rows=[
            Row(
                cant_split=True,
                cells=[
                    Cell(
                        width_pct=8,
                        margins={"top": 0, "bottom": 0, "left": 0, "right": 80},
                        borders={"top": none, "bottom": none, "left": none, "right": none},
                        children=[
                            Par(
                                alignment="center",
                                spacing=Spacing(before=0, after=0),
                                children=[R(cap, font=BODY_SERIF, bold=True, size=88)],
                            )
                        ],
                    ),
                    Cell(
                        width_pct=92,
                        margins={"top": 0, "bottom": 0, "left": 0, "right": 0},
                        borders={"top": none, "bottom": none, "left": none, "right": none},
                        children=right_children,
                    ),
                ],
            )
        ],
    )


def para_for(
    text: str,
    book: bool,
    indent_first_line: bool = False,
    body_styles: BodyStyleIds = None,
) -> Par:
    """Book paragraphs use a first-line indent as the paragraph cue, with NO extra space between
    paragraphs. The FIRST paragraph of a scene / after a scene break is left un-indented (print
    convention), signalled by ``indent_first_line=False`` from :func:`render_blocks`."""
    if book and body_styles:
        # Named-style path: the Body/BodyFirst style owns font, size, alignment, and the indent cue;
        # runs carry only their own inline emphasis, inheriting the rest from the style.
        body_id, first_id = body_styles
        return Par(style=body_id if indent_first_line else first_id, children=inline_runs(text))
    if book:
        return Par(
            alignment="both",
            spacing=Spacing(line=320, line_rule="auto"),
            indent_first_line=inches_to_twips(0.3) if indent_first_line else None,
            children=inline_runs(text, {"font": "Georgia", "size": 22}),
        )
    return Par(
        spacing=Spacing(after=120, line=276, line_rule="auto"),
        children=inline_runs(text),
    )


def render_blocks(
    blocks: list[ProseBlock],
    book: bool,
    body_styles: BodyStyleIds = None,
    *,
    drop_cap_first: bool = False,
) -> list[Par | Tbl]:
    out: list[Par | Tbl] = []

    def push_table(t: Tbl) -> None:
        out.append(t)
        out.append(_empty_par())

    neutral = neutral_surface()
    seen_book_para = False
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, ProsePara) and _is_working_draft_marker(b.text):
            if not book:
                out.append(working_draft_para())
        elif isinstance(b, Heading):
            out.append(Par(heading=b.level, children=inline_runs(b.text)))
        elif isinstance(b, TimeMark):
            out.append(time_marker_para(b.label))
        elif isinstance(b, UnorderedList):
            for it in b.items:
                out.append(Par(bullet=True, children=inline_runs(it)))
        elif isinstance(b, OrderedList):
            for list_index, it in enumerate(b.items):
                out.append(Par(children=[R(f"{list_index + 1}. "), *inline_runs(it)]))
        elif isinstance(b, Callout):
            push_table(callout_panel(b))
        elif isinstance(b, DataTable):
            push_table(data_table(b))
        elif isinstance(b, CodeBlock):
            push_table(readout_panel(b.lines))
        elif isinstance(b, StatWindow):
            push_table(status_sheet_panel(b.lines))
        elif isinstance(b, InterfacePanel):
            push_table(interface_panel(b))
        elif isinstance(b, Rule):
            out.append(
                Par(
                    spacing=Spacing(before=120, after=120),
                    borders={
                        "bottom": Border(style="single", size=6, color=PALETTE.border, space=1)
                    },
                )
            )
        else:
            if book and drop_cap_first and not seen_book_para:
                following: list[str] = []
                if (
                    len(b.text.strip()) < 120
                    and i + 1 < len(blocks)
                    and isinstance(blocks[i + 1], ProsePara)
                    and not _is_working_draft_marker(blocks[i + 1].text)
                ):
                    following.append(blocks[i + 1].text)
                    i += 1
                out.append(drop_cap_block(b.text, body_styles, following))
            else:
                out.append(para_for(b.text, book, book and seen_book_para, body_styles))
            seen_book_para = True
        i += 1
    return out


def page_footer() -> list[Par]:
    return [Par(alignment="center", children=[R(page_number=True, color="808080", size=18)])]


PAGE_MARGIN_TWIPS = inches_to_twips(1)


def build_doc_doc(title: str, content: str) -> DocxBuilder:
    """Domain B: a canon doc — flowing markdown with professional tables/callouts, no book format."""
    b = DocxBuilder(title=title, creator="Writers' Desk", margin_twips=PAGE_MARGIN_TWIPS)
    b.set_footer(page_footer())
    b.add_body(render_blocks(parse_blocks(content), False))
    return b


READER_BODY_STYLES: BodyStyleIds = (STYLE.body, STYLE.body_first)


class ReaderLayout:
    """The Reader DOCX layout state machine.

    It walks the ``ManuscriptSpine`` and accumulates style-referencing paragraphs — every
    character/alignment decision lives in the named stylesheet (:func:`reader_style_defs`), so this
    class only chooses WHICH style and the contextual spacing. No inline one-off run formatting, so
    the styling can be re-skinned by editing the stylesheet without touching this layout code.
    """

    def __init__(self, policy: ExportPolicy, toc_entries: list[str] | None = None) -> None:
        self.children: list[Par | Tbl] = []
        self.policy = policy
        self.toc_entries = list(toc_entries or [])
        self.toc_bookmarks = [f"rw_chapter_{i + 1}" for i in range(len(self.toc_entries))]
        self._toc_chapter_index = 0

    def _styled(self, style: str, text: str, spacing: Spacing | None = None) -> None:
        self.children.append(Par(style=style, spacing=spacing, children=[R(text)]))

    def _page_break(self) -> None:
        self.children.append(Par(children=[R(break_page=True)]))

    def half_title(self, metadata) -> None:
        """The book title alone on the leading page, ahead of the full title page."""
        self._styled(STYLE.book_title, metadata.title, Spacing(before=3600, after=0))
        self._page_break()

    def table_of_contents(self, entries: list[str]) -> None:
        """Clickable Contents page with chapter-name links and live page-number fields."""
        if not entries:
            return
        self._page_break()
        self._styled(STYLE.chapter_label, "CONTENTS", Spacing(before=480, after=240))
        for label, bookmark in zip(entries, self.toc_bookmarks, strict=False):
            self.children.append(
                Par(
                    spacing=Spacing(after=80),
                    tab_stops=[TabStop("right", inches_to_twips(6.15), leader="dot")],
                    children=[
                        Hyperlink(
                            anchor=bookmark,
                            children=[R(label, font=BODY_SERIF, color="111827", size=21)],
                        ),
                        R("	", font=BODY_SERIF, size=21),
                        Field(
                            f"PAGEREF {bookmark} \\h",
                            cached_text="1",
                            run=R(font=BODY_SERIF, color="111827", size=21),
                        ),
                    ],
                )
            )

    def title_page(self, metadata, render_subtitle: str | None = None) -> None:
        """Full title page with the exact book title and explicit author byline."""
        self._styled(STYLE.book_title, metadata.title, Spacing(before=2400, after=360))
        if self.policy.include_subtitle and metadata.subtitle:
            self._styled(STYLE.book_subtitle, metadata.subtitle, Spacing(after=180))
        if self.policy.include_author_byline and metadata.author:
            self._styled(
                STYLE.author_byline,
                f"Written By: {metadata.author}",
                Spacing(before=240, after=120),
            )
        if render_subtitle:
            self._styled(STYLE.render_descriptor, render_subtitle, Spacing(before=160, after=0))

    def _divider(
        self,
        eyebrow_style: str,
        eyebrow: str,
        title_style: str,
        title: str,
        subtitle: str | None,
    ) -> None:
        self._page_break()
        self._styled(eyebrow_style, eyebrow, Spacing(before=2000, after=200))
        self._styled(title_style, title, Spacing(after=80 if subtitle else 0))
        if subtitle:
            self._styled(STYLE.divider_subtitle, subtitle)

    def volume_divider(self, v: SpineVolumeNode) -> None:
        self._divider(
            STYLE.volume_eyebrow,
            f"VOLUME {to_roman(v.volume_no)}",
            STYLE.volume_title,
            v.title,
            v.subtitle,
        )

    def part_divider(self, p: SpinePartNode) -> None:
        eyebrow = f"{part_kind_word(p.kind).upper()} {to_roman(p.part_no)}"
        self._divider(STYLE.part_eyebrow, eyebrow, STYLE.part_title, p.title, p.subtitle)

    def chapter(self, ch: SpineChapterNode) -> None:
        """One chapter: page break, resolved label, optional title/POV/epigraph, then its scenes.

        Front/back matter renders as a titled section (the label already IS the section name), so
        no duplicate title line and no POV line. Skips a prose-less chapter.
        """
        scenes = [s for s in ch.scenes if s.has_prose]
        if not scenes:
            return
        if self.children:
            self._page_break()
        epigraph = (ch.epigraph or "").strip()
        is_section = ch.kind in ("front_matter", "back_matter")
        show_title = bool(ch.title) and not is_section
        show_pov = not is_section and len(ch.pov.strip()) > 0

        chapter_bookmark = None
        if not is_section and self._toc_chapter_index < len(self.toc_bookmarks):
            chapter_bookmark = self.toc_bookmarks[self._toc_chapter_index]
            self._toc_chapter_index += 1
        self.children.append(
            Par(
                style=STYLE.chapter_label,
                spacing=Spacing(before=360, after=20),
                bookmark=chapter_bookmark,
                children=[R(ch.label.upper())],
            )
        )
        if show_title:
            self._styled(STYLE.chapter_title, ch.title or "", Spacing(before=20, after=20))
        if show_pov:
            self._styled(
                STYLE.pov_line,
                f"POV · {ch.pov}",
                Spacing(before=20, after=20 if not epigraph else 80),
            )
        if epigraph:
            self._styled(STYLE.epigraph, epigraph, Spacing(after=320))

        for si, sc in enumerate(scenes):
            if si > 0:
                self._styled(
                    STYLE.scene_break,
                    self.policy.scene_break_glyph or "⁂",
                    Spacing(before=160, after=160),
                )

            blocks = list(sc.blocks)
            if si == 0:
                if blocks and isinstance(blocks[0], ProsePara) and _is_working_draft_marker(blocks[0].text):
                    blocks.pop(0)

                context_lines: list[str] = []
                while blocks and len(context_lines) < 2:
                    block = blocks[0]
                    if not isinstance(block, Heading) or block.level != 2:
                        break
                    context_lines.append(block.text)
                    blocks.pop(0)
                for i, line_text in enumerate(context_lines):
                    self.children.append(
                        chapter_context_para(line_text, location=i == len(context_lines) - 1 and len(context_lines) > 1)
                    )

            self.children.extend(
                render_blocks(
                    blocks,
                    True,
                    READER_BODY_STYLES,
                    drop_cap_first=(si == 0 and not is_section),
                )
            )


def render_reader_doc(
    spine: ManuscriptSpine, policy: ExportPolicy, render_subtitle: str | None = None
) -> DocxBuilder:
    """Reader DOCX emitter — consumes the ``ManuscriptSpine`` via the :class:`ReaderLayout`.

    Renders Volume dividers → Part dividers → each part's chapters (or ungrouped parts/chapters)
    from the spine's resolved labels + pre-parsed blocks, referencing the named stylesheet built
    from the policy.
    """
    plan = plan_reader_production(spine, policy)
    toc_entries = next((item.entries for item in plan.front if item.type == "toc"), [])
    layout = ReaderLayout(policy, toc_entries)

    # Production sequence: the front matter (half-title, title page, authored front-matter sections
    # and the generated Table of Contents) is planned in canonical publishing order by the pure
    # planner, then the body follows. Back matter needs no special handling — it sorts last by
    # `position` and flows through the body walk.
    for item in plan.front:
        if item.type == "half_title":
            layout.half_title(spine.metadata)
        elif item.type == "title_page":
            layout.title_page(spine.metadata, render_subtitle)
        elif item.type == "toc":
            layout.table_of_contents(item.entries)
        elif item.node is not None:
            layout.chapter(item.node)

    def emit_part(part: SpinePartNode) -> None:
        if policy.render_parts:
            layout.part_divider(part)
        for ch in part.chapters:
            layout.chapter(ch)

    for node in plan.body:
        if isinstance(node, SpineVolumeNode):
            if policy.render_parts:
                layout.volume_divider(node)
            for part in node.parts:
                emit_part(part)
        elif isinstance(node, SpinePartNode):
            emit_part(node)
        else:
            layout.chapter(node)

    m = inches_to_twips(policy.page_setup.margin_inches or 1)
    is_working_draft = any(
        isinstance(block, ProsePara) and _is_working_draft_marker(block.text)
        for chapter in spine_chapters(spine)
        for scene in chapter.scenes
        for block in scene.blocks
    )
    b = DocxBuilder(
        title=spine.metadata.title,
        creator=spine.metadata.author or "Writers' Desk",
        margin_twips=m,
        different_first_page=True,
    )
    b.add_styles(reader_style_defs(policy))
    b.set_footer(page_footer())
    b.set_footer([Par(children=[R("")])], first_page=True)
    if is_working_draft:
        b.set_header(working_draft_header())
    else:
        b.set_header([Par(children=[R("")])])
    b.set_header([Par(children=[R("")])], first_page=True)
    b.add_body(layout.children)
    return b
