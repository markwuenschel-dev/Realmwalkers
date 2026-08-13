"""A declarative OOXML layer that mirrors the ``docx`` npm library's builder API.

``frontend/src/desk/lib/docx.ts`` is written against docx-js: it *declares* Paragraph/TextRun/
Table/TableRow/TableCell trees and lets the library emit OOXML. python-docx, by contrast, is an
imperative document-mutation API with a fixed table grid and no public surface for character
spacing, per-cell borders, column spans, paragraph borders, or percentage widths.

Rather than fight that mismatch in every emitter function, this module supplies the same
declarative vocabulary (:class:`Run`, :class:`Par`, :class:`Cell`, :class:`Row`, :class:`Tbl`) and
materializes it straight to OOXML elements. python-docx is used only as the *package*: it owns
[Content_Types].xml, relationships, styles.xml, numbering.xml, headers/footers, and sectPr — all
the fiddly container correctness — while this module owns the body content. That keeps the port of
docx.ts nearly line-for-line with the original.

Units, matching docx-js exactly:

==========================  ==========================================
``Run.size``                half-points (22 → 11pt)
``Run.character_spacing``   twentieths of a point
``Border.size``             eighths of a point (4 → 0.5pt)
spacing / indent / tabs     twips (1440 per inch)
``Tbl.width_pct``           percent; emitted as OOXML fiftieths-of-a-percent
==========================  ==========================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docx import Document as _new_document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Twips

TWIPS_PER_INCH = 1440


def inches_to_twips(inches: float) -> int:
    """docx-js ``convertInchesToTwip``."""
    return round(inches * TWIPS_PER_INCH)


# ── Declarative model ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Border:
    """One edge. ``size`` is in eighths of a point, matching docx-js ``IBorderOptions``."""

    style: str = "single"
    size: int = 4
    color: str = "auto"
    space: int | None = None


NO_BORDER = Border(style="none", size=0, color="auto")


def line(color: str, size: int = 4) -> Border:
    """docx.ts ``line()`` — a single-style edge in the given colour."""
    return Border(style="single", size=size, color=color)


@dataclass
class Spacing:
    before: int | None = None
    after: int | None = None
    line: int | None = None
    line_rule: str | None = None


@dataclass
class Run:
    """A ``TextRun``. Set exactly one of ``text`` / ``page_number`` / ``break_page`` / ``tab``."""

    text: str = ""
    font: str | None = None
    size: int | None = None  # half-points
    bold: bool = False
    italics: bool = False
    color: str | None = None
    all_caps: bool = False
    character_spacing: int | None = None  # twentieths of a point
    underline: bool = False
    page_number: bool = False
    break_page: bool = False


def R(text: str = "", **kwargs) -> Run:  # noqa: N802 - deliberately terse, mirrors `new TextRun`
    return Run(text=text, **kwargs)


@dataclass
class Hyperlink:
    href: str | None = None
    anchor: str | None = None
    children: list[Run] = field(default_factory=list)


@dataclass
class Field:
    instr: str
    cached_text: str = ""
    run: Run = field(default_factory=Run)


@dataclass
class TabStop:
    type: str  # "right" | "center" | "left"
    position: int  # twips
    leader: str | None = None  # "dot" | "hyphen" | "underscore" | "none"


@dataclass
class Par:
    """A ``Paragraph``. ``style`` references a named style; ``heading`` uses a built-in Heading N."""

    children: list[Run | Hyperlink | Field] = field(default_factory=list)
    style: str | None = None
    heading: int | None = None
    alignment: str | None = None  # "left" | "center" | "right" | "both"
    spacing: Spacing | None = None
    indent_first_line: int | None = None
    borders: dict[str, Border] | None = None
    tab_stops: list[TabStop] = field(default_factory=list)
    bullet: bool = False
    bookmark: str | None = None
    drop_cap_lines: int | None = None
    drop_cap_distance: int = 120


@dataclass
class Cell:
    children: list[Par | Tbl] = field(default_factory=list)
    shading: str | None = None
    margins: dict[str, int] | None = None
    borders: dict[str, Border] | None = None
    col_span: int | None = None
    width_pct: float | None = None


@dataclass
class Row:
    cells: list[Cell] = field(default_factory=list)
    header: bool = False
    cant_split: bool = False


@dataclass
class Tbl:
    rows: list[Row] = field(default_factory=list)
    width_pct: float | None = None
    borders: dict[str, Border] | None = None


BlockElement = Par | Tbl


# ── Materialization ──────────────────────────────────────────────────────────

_ALIGN = {"left": "left", "center": "center", "right": "right", "both": "both", "justified": "both"}
_EDGE_ORDER = ("top", "left", "bottom", "right", "insideH", "insideV")
_TBL_EDGE_TAG = {
    "top": "top",
    "left": "left",
    "bottom": "bottom",
    "right": "right",
    "insideH": "insideH",
    "insideV": "insideV",
}


def _el(tag: str, **attrs: str):
    e = OxmlElement(tag)
    for k, v in attrs.items():
        e.set(qn(f"w:{k}"), v)
    return e


def _border_el(tag: str, b: Border):
    e = _el(tag, val=b.style, sz=str(b.size), color=b.color)
    e.set(qn("w:space"), str(b.space if b.space is not None else 0))
    return e


def _pct(value: float) -> str:
    """OOXML ``w:type="pct"`` is in fiftieths of a percent — 100% is 5000."""
    return str(round(value * 50))


def _run_props(r: Run):
    rpr = OxmlElement("w:rPr")
    if r.font:
        rf = OxmlElement("w:rFonts")
        for attr in ("w:ascii", "w:hAnsi", "w:cs"):
            rf.set(qn(attr), r.font)
        rpr.append(rf)
    if r.bold:
        rpr.append(_el("w:b"))
    if r.italics:
        rpr.append(_el("w:i"))
    if r.all_caps:
        rpr.append(_el("w:caps"))
    if r.character_spacing is not None:
        rpr.append(_el("w:spacing", val=str(r.character_spacing)))
    if r.color:
        rpr.append(_el("w:color", val=r.color))
    if r.size is not None:
        rpr.append(_el("w:sz", val=str(r.size)))
        rpr.append(_el("w:szCs", val=str(r.size)))
    if r.underline:
        rpr.append(_el("w:u", val="single"))
    return rpr


def _text_children(text: str) -> list:
    """Split on tabs and newlines so ``\\t`` becomes ``<w:tab/>`` as docx-js does."""
    out = []
    for i, chunk in enumerate(text.split("\t")):
        if i > 0:
            out.append(OxmlElement("w:tab"))
        if chunk:
            t = OxmlElement("w:t")
            t.set(qn("xml:space"), "preserve")
            t.text = chunk
            out.append(t)
    return out


def _render_run(r: Run):
    run_el = OxmlElement("w:r")
    run_el.append(_run_props(r))
    if r.break_page:
        run_el.append(_el("w:br", type="page"))
    elif r.text:
        for child in _text_children(r.text):
            run_el.append(child)
    return run_el


def _render_page_number(r: Run):
    """A ``PAGE`` field. ``w:fldSimple`` is a paragraph-level wrapper around a formatted run."""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), " PAGE ")
    inner = OxmlElement("w:r")
    inner.append(_run_props(r))
    t = OxmlElement("w:t")
    t.text = "1"
    inner.append(t)
    fld.append(inner)
    return fld


def _para_props(p: Par, numbering_id: int | None):
    ppr = OxmlElement("w:pPr")
    if p.style:
        ppr.append(_el("w:pStyle", val=p.style))
    elif p.heading is not None:
        ppr.append(_el("w:pStyle", val=f"Heading{p.heading}"))
    if p.bullet and numbering_id is not None:
        numpr = OxmlElement("w:numPr")
        numpr.append(_el("w:ilvl", val="0"))
        numpr.append(_el("w:numId", val=str(numbering_id)))
        ppr.append(numpr)
    if p.borders:
        pbdr = OxmlElement("w:pBdr")
        for edge in ("top", "left", "bottom", "right"):
            if edge in p.borders:
                pbdr.append(_border_el(f"w:{edge}", p.borders[edge]))
        ppr.append(pbdr)
    if p.tab_stops:
        tabs = OxmlElement("w:tabs")
        for ts in p.tab_stops:
            attrs = {"val": ts.type, "pos": str(ts.position)}
            if ts.leader:
                attrs["leader"] = ts.leader
            tabs.append(_el("w:tab", **attrs))
        ppr.append(tabs)
    if p.drop_cap_lines:
        ppr.append(
            _el(
                "w:framePr",
                dropCap="drop",
                lines=str(p.drop_cap_lines),
                hSpace=str(p.drop_cap_distance),
                w="1200",
                wrap="around",
                vAnchor="text",
                hAnchor="text",
            )
        )
    if p.spacing:
        attrs: dict[str, str] = {}
        if p.spacing.before is not None:
            attrs["before"] = str(p.spacing.before)
        if p.spacing.after is not None:
            attrs["after"] = str(p.spacing.after)
        if p.spacing.line is not None:
            attrs["line"] = str(p.spacing.line)
            attrs["lineRule"] = p.spacing.line_rule or "auto"
        if attrs:
            ppr.append(_el("w:spacing", **attrs))
    if p.indent_first_line is not None:
        ppr.append(_el("w:ind", firstLine=str(p.indent_first_line)))
    if p.alignment:
        ppr.append(_el("w:jc", val=_ALIGN[p.alignment]))
    return ppr


class _Renderer:
    """Holds the package-level context (hyperlink relationships, numbering id) during a render."""

    def __init__(self, part, numbering_id: int | None):
        self.part = part
        self.numbering_id = numbering_id
        self._bookmark_seq = 1

    def paragraph(self, p: Par):
        p_el = OxmlElement("w:p")
        p_el.append(_para_props(p, self.numbering_id))
        bookmark_id = None
        if p.bookmark:
            bookmark_id = str(self._bookmark_seq)
            self._bookmark_seq += 1
            start = _el("w:bookmarkStart", id=bookmark_id, name=p.bookmark)
            p_el.append(start)
        for child in p.children:
            if isinstance(child, Hyperlink):
                p_el.append(self._hyperlink(child))
            elif isinstance(child, Field):
                p_el.append(self._field(child))
            elif child.page_number:
                p_el.append(_render_page_number(child))
            else:
                p_el.append(_render_run(child))
        if bookmark_id is not None:
            p_el.append(_el("w:bookmarkEnd", id=bookmark_id))
        return p_el

    def _hyperlink(self, h: Hyperlink):
        link = OxmlElement("w:hyperlink")
        if h.anchor:
            link.set(qn("w:anchor"), h.anchor)
            link.set(qn("w:history"), "1")
        elif h.href:
            r_id = self.part.relate_to(h.href, RT.HYPERLINK, is_external=True)
            link.set(qn("r:id"), r_id)
        for r in h.children:
            link.append(_render_run(r))
        return link

    @staticmethod
    def _field(f: Field):
        fld = OxmlElement("w:fldSimple")
        fld.set(qn("w:instr"), f" {f.instr.strip()} ")
        inner = OxmlElement("w:r")
        inner.append(_run_props(f.run))
        t = OxmlElement("w:t")
        t.text = f.cached_text or "1"
        inner.append(t)
        fld.append(inner)
        return fld

    def table(self, t: Tbl):
        tbl = OxmlElement("w:tbl")
        tbl.append(self._table_props(t))

        cols = max((sum(c.col_span or 1 for c in row.cells) for row in t.rows), default=1)
        grid = OxmlElement("w:tblGrid")
        usable = inches_to_twips(6.5)
        widths_pct: list[float] = []
        if t.rows:
            for c in t.rows[0].cells:
                span = c.col_span or 1
                total_pct = c.width_pct if c.width_pct is not None else (100 / cols) * span
                widths_pct.extend([total_pct / span] * span)
        if len(widths_pct) != cols or sum(widths_pct) <= 0:
            widths_pct = [100 / cols] * cols
        total = sum(widths_pct)
        for pct in widths_pct:
            grid.append(_el("w:gridCol", w=str(round(usable * pct / total))))
        tbl.append(grid)

        for row in t.rows:
            tbl.append(self._row(row))
        return tbl

    @staticmethod
    def _table_props(t: Tbl):
        tblpr = OxmlElement("w:tblPr")
        if t.width_pct is not None:
            tblpr.append(_el("w:tblW", w=_pct(t.width_pct), type="pct"))
        if t.borders:
            borders = OxmlElement("w:tblBorders")
            for edge in _EDGE_ORDER:
                if edge in t.borders:
                    borders.append(_border_el(f"w:{_TBL_EDGE_TAG[edge]}", t.borders[edge]))
            tblpr.append(borders)
        return tblpr

    def _row(self, row: Row):
        tr = OxmlElement("w:tr")
        if row.header or row.cant_split:
            trpr = OxmlElement("w:trPr")
            if row.header:
                trpr.append(_el("w:tblHeader"))
            if row.cant_split:
                trpr.append(_el("w:cantSplit"))
            tr.append(trpr)
        for c in row.cells:
            tr.append(self._cell(c))
        return tr

    def _cell(self, c: Cell):
        tc = OxmlElement("w:tc")
        tc.append(self._cell_props(c))
        for child in c.children:
            if isinstance(child, Tbl):
                tc.append(self.table(child))
                # OOXML requires a cell to end with a paragraph after a nested table.
                tc.append(self.paragraph(Par(children=[R("")])))
            else:
                tc.append(self.paragraph(child))
        if not c.children:
            tc.append(self.paragraph(Par(children=[R("")])))
        return tc

    @staticmethod
    def _cell_props(c: Cell):
        tcpr = OxmlElement("w:tcPr")
        if c.width_pct is not None:
            tcpr.append(_el("w:tcW", w=_pct(c.width_pct), type="pct"))
        if c.col_span and c.col_span > 1:
            tcpr.append(_el("w:gridSpan", val=str(c.col_span)))
        if c.borders:
            borders = OxmlElement("w:tcBorders")
            for edge in ("top", "left", "bottom", "right"):
                if edge in c.borders:
                    borders.append(_border_el(f"w:{edge}", c.borders[edge]))
            tcpr.append(borders)
        if c.shading:
            tcpr.append(_el("w:shd", val="clear", color="auto", fill=c.shading))
        if c.margins:
            mar = OxmlElement("w:tcMar")
            for edge in ("top", "left", "bottom", "right"):
                if edge in c.margins:
                    mar.append(_el(f"w:{edge}", w=str(c.margins[edge]), type="dxa"))
            tcpr.append(mar)
        return tcpr


# ── Document wrapper ─────────────────────────────────────────────────────────

_BULLET_NUM_ID = 77  # well clear of the default template's 1..9


def _ensure_bullet_numbering(document) -> int | None:
    """Append a bullet ``abstractNum``/``num`` pair so ``Par(bullet=True)`` renders a real glyph.

    python-docx ships a ``numbering.xml`` but no bullet definition this code can rely on, so we add
    our own rather than guessing at a template numId.
    """
    try:
        numbering = document.part.numbering_part.element
    except (KeyError, AttributeError, NotImplementedError):
        return None

    abstract_id = str(_BULLET_NUM_ID)
    abstract = _el("w:abstractNum", abstractNumId=abstract_id)
    abstract.append(_el("w:multiLevelType", val="hybridMultilevel"))
    levels = (("", "Symbol"), ("o", "Courier New"), ("", "Wingdings"))
    for ilvl, (glyph, font) in enumerate(levels):
        lvl = _el("w:lvl", ilvl=str(ilvl))
        lvl.append(_el("w:start", val="1"))
        lvl.append(_el("w:numFmt", val="bullet"))
        lvl.append(_el("w:lvlText", val=glyph))
        lvl.append(_el("w:lvlJc", val="left"))
        ppr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(720 * (ilvl + 1)))
        ind.set(qn("w:hanging"), "360")
        ppr.append(ind)
        lvl.append(ppr)
        rpr = OxmlElement("w:rPr")
        rf = OxmlElement("w:rFonts")
        for attr in ("w:ascii", "w:hAnsi"):
            rf.set(qn(attr), font)
        rf.set(qn("w:hint"), "default")
        rpr.append(rf)
        lvl.append(rpr)
        abstract.append(lvl)

    num = _el("w:num", numId=str(_BULLET_NUM_ID))
    num.append(_el("w:abstractNumId", val=abstract_id))

    # abstractNum elements must precede num elements in numbering.xml.
    existing_nums = numbering.findall(qn("w:num"))
    if existing_nums:
        existing_nums[0].addprevious(abstract)
    else:
        numbering.append(abstract)
    numbering.append(num)
    return _BULLET_NUM_ID


@dataclass
class StyleDef:
    """A named paragraph style — the Python shape of docx-js ``IParagraphStyleOptions``."""

    style_id: str
    name: str
    based_on: str | None = "Normal"
    quick_format: bool = False
    font: str | None = None
    size: int | None = None  # half-points
    bold: bool = False
    italics: bool = False
    color: str | None = None
    character_spacing: int | None = None
    alignment: str | None = None
    line: int | None = None
    line_rule: str | None = None
    indent_first_line: int | None = None


class DocxBuilder:
    """Owns a python-docx package and materializes declarative blocks into it."""

    def __init__(
        self,
        *,
        title: str = "",
        creator: str = "",
        margin_twips: int = TWIPS_PER_INCH,
        different_first_page: bool = False,
    ):
        self.document = _new_document()
        self.document.core_properties.title = title
        if creator:
            self.document.core_properties.author = creator

        settings = self.document.settings.element
        if settings.find(qn("w:updateFields")) is None:
            settings.append(_el("w:updateFields", val="true"))

        body = self.document.element.body
        for child in list(body):
            if child.tag != qn("w:sectPr"):
                body.remove(child)

        section = self.document.sections[0]
        section.top_margin = section.bottom_margin = Twips(margin_twips)
        section.left_margin = section.right_margin = Twips(margin_twips)
        self.section = section
        if different_first_page:
            section.different_first_page_header_footer = True

        self._numbering_id = _ensure_bullet_numbering(self.document)
        self._renderer = _Renderer(self.document.part, self._numbering_id)

    # -- styles ------------------------------------------------------------

    def add_styles(self, defs: list[StyleDef]) -> None:
        for d in defs:
            self._add_style(d)

    def _add_style(self, d: StyleDef) -> None:
        """Append a ``w:style`` element directly, exactly as docx-js does.

        python-docx's ``styles.add_style(name, …)`` raises when a BUILT-IN style already claims the
        display name — and Word ships built-ins called "Book Title", "Title", "Subtitle", "Quote"
        and friends. Reusing the built-in on collision silently binds a *character* style where a
        paragraph style was meant, so the emitter's ``w:pStyle w:val="BookTitle"`` resolves to
        nothing and the paragraph renders unstyled.

        docx-js has no such guard: it writes ``<w:style w:styleId="BookTitle"><w:name
        w:val="Book Title"/>…``. The styleId is the key Word resolves; the name is just a gallery
        label, and a custom paragraph style may share a name with a built-in character style. So we
        build the element ourselves and skip the collision check entirely.
        """
        style = OxmlElement("w:style")
        style.set(qn("w:type"), "paragraph")
        style.set(qn("w:styleId"), d.style_id)

        # CT_Style is a sequence: name → basedOn → qFormat → pPr → rPr. Order is schema-enforced.
        style.append(_el("w:name", val=d.name))
        if d.based_on:
            style.append(_el("w:basedOn", val=d.based_on))
        if d.quick_format:
            style.append(OxmlElement("w:qFormat"))

        ppr = OxmlElement("w:pPr")
        if d.line is not None:
            ppr.append(_el("w:spacing", line=str(d.line), lineRule=d.line_rule or "auto"))
        if d.indent_first_line is not None:
            ppr.append(_el("w:ind", firstLine=str(d.indent_first_line)))
        if d.alignment:
            ppr.append(_el("w:jc", val=_ALIGN[d.alignment]))
        if len(ppr):
            style.append(ppr)

        rpr = OxmlElement("w:rPr")
        if d.font:
            rf = OxmlElement("w:rFonts")
            for attr in ("w:ascii", "w:hAnsi", "w:cs"):
                rf.set(qn(attr), d.font)
            rpr.append(rf)
        if d.bold:
            rpr.append(_el("w:b"))
        if d.italics:
            rpr.append(_el("w:i"))
        if d.character_spacing is not None:
            rpr.append(_el("w:spacing", val=str(d.character_spacing)))
        if d.color:
            rpr.append(_el("w:color", val=d.color))
        if d.size is not None:
            rpr.append(_el("w:sz", val=str(d.size)))
            rpr.append(_el("w:szCs", val=str(d.size)))
        if len(rpr):
            style.append(rpr)

        self.document.styles.element.append(style)

    # -- content -----------------------------------------------------------

    def add_body(self, elements: list[BlockElement]) -> None:
        body = self.document.element.body
        sect_pr = body.find(qn("w:sectPr"))
        for e in elements:
            node = self._renderer.table(e) if isinstance(e, Tbl) else self._renderer.paragraph(e)
            if sect_pr is not None:
                sect_pr.addprevious(node)
            else:
                body.append(node)

    def set_footer(self, paragraphs: list[Par], *, first_page: bool = False) -> None:
        self._fill_hdr_ftr(
            self.section.first_page_footer if first_page else self.section.footer, paragraphs
        )

    def set_header(self, paragraphs: list[Par], *, first_page: bool = False) -> None:
        self._fill_hdr_ftr(
            self.section.first_page_header if first_page else self.section.header, paragraphs
        )

    def _fill_hdr_ftr(self, container, paragraphs: list[Par]) -> None:
        container.is_linked_to_previous = False
        element = container._element  # noqa: SLF001 - python-docx exposes no public accessor
        for child in list(element):
            element.remove(child)
        renderer = _Renderer(container.part, self._numbering_id)
        for p in paragraphs or [Par(children=[R("")])]:
            element.append(renderer.paragraph(p))

    def save(self, path) -> None:
        self.document.save(str(path))
