"""Port of the Shunn emitter in ``frontend/src/desk/lib/docx.ts`` (``renderShunnDoc`` and helpers).

Plain manuscript format for agents/editors: Courier New, double-spaced, a right-aligned
surname/title/page running header suppressed on the title page, and rich LitRPG blocks flattened to
safe text. The document has NO footer and NO named styles — every property is set on the paragraph.
"""

from __future__ import annotations

import math
import re

from .ooxml import DocxBuilder, Par, R, Run, Spacing, TabStop, inches_to_twips
from .presets import ExportPolicy
from .prose import ProseBlock
from .spine import ManuscriptSpine, SpineChapterNode, SpinePartNode, SpineVolumeNode, spine_counts
from .surfaces import format_interface_shunn_header

SHUNN_FONT = "Courier New"
SHUNN_SIZE = 24
#: docx.ts ``const DOUBLE = { line: 480 }``. ``lineRule`` defaults to "auto", as in docx-js.
DOUBLE_LINE = 480


def _double() -> Spacing:
    """A fresh ``DOUBLE`` spacing. The TS spreads the constant object, so it is never shared."""
    return Spacing(line=DOUBLE_LINE)


def round_words(n: int) -> int:
    """Port of ``roundWords``.

    JavaScript's ``Math.round`` rounds a half toward +Infinity; Python's ``round`` rounds a half to
    even. The half case is therefore spelled out as ``floor(x + 0.5)``.
    """
    if n < 25000:
        return math.floor(n / 100 + 0.5) * 100
    return math.floor(n / 1000 + 0.5) * 1000


def shunn_run(text: str) -> Run:
    return R(text, font=SHUNN_FONT, size=SHUNN_SIZE)


def shunn_header(surname: str, title_upper: str) -> list[Par]:
    """The running header's paragraphs — docx-js ``new Header({ children: [...] })``."""
    return [
        Par(
            alignment="right",
            children=[
                shunn_run(f"{surname} / {title_upper} / "),
                R(page_number=True, font=SHUNN_FONT, size=SHUNN_SIZE),
            ],
        )
    ]


def shunn_body(text: str) -> Par:
    return Par(
        spacing=_double(),
        indent_first_line=inches_to_twips(0.5),
        children=[shunn_run(text)],
    )


def shunn_center(text: str, *, spacing: Spacing | None = None) -> Par:
    """Port of ``shunnCenter``.

    Every TS call site passes ``{ indent: undefined }`` in its ``extra`` object — a no-op, because
    the default already sets no indent at all. The only extra that ever takes effect is ``spacing``.
    """
    return Par(
        alignment="center",
        spacing=_double() if spacing is None else spacing,
        children=[shunn_run(text)],
    )


def shunn_plain_blocks(blocks: list[ProseBlock]) -> list[Par]:
    """Flatten the parsed prose blocks to submission-safe Shunn paragraphs.

    The TS ``switch`` has no ``default:`` case, so an unlisted block kind emits nothing.
    """
    out: list[Par] = []
    for b in blocks:
        if b.kind == "p":
            out.append(shunn_body(re.sub(r"\s+", " ", b.text).strip()))
        elif b.kind == "interface":
            out.append(shunn_center(format_interface_shunn_header(b.spec)))
            out.append(Par(spacing=_double(), children=[shunn_run("")]))
            for ln in b.lines:
                if ln.strip():
                    out.append(shunn_body(ln))
                else:
                    out.append(Par(spacing=_double(), children=[shunn_run("")]))
        elif b.kind == "table":
            for row in [b.head, *b.rows]:
                out.append(shunn_center("| " + " | ".join(row) + " |"))
        elif b.kind in ("code", "stat"):  # the TS `case "code": case "stat":` fallthrough
            for ln in b.lines:
                out.append(shunn_center(ln or " "))
        elif b.kind == "callout":
            if b.title:
                out.append(shunn_body(f"[{b.title}]"))
            for ln in b.lines:
                if ln.strip():
                    out.append(shunn_body(re.sub(r"\s+", " ", ln).strip()))
        elif b.kind == "heading":
            out.append(shunn_center(b.text.upper(), spacing=Spacing(before=120, line=DOUBLE_LINE)))
        elif b.kind == "time":
            # Shunn stays format-plain: a centered label, no rule glyph or colour.
            out.append(shunn_center(b.label.upper()))
        elif b.kind == "ul":
            for it in b.items:
                out.append(shunn_body("- " + re.sub(r"\s+", " ", it).strip()))
        elif b.kind == "ol":
            for i, it in enumerate(b.items):
                out.append(shunn_body(f"{i + 1}. " + re.sub(r"\s+", " ", it).strip()))
        elif b.kind == "hr":
            out.append(shunn_center("#"))
    return out


def shunn_chapter(ch: SpineChapterNode) -> list[Par]:
    """One chapter, plain Shunn format.

    Uses the spine's resolved label (uppercased) — so a Prologue reads "PROLOGUE", never
    "CHAPTER N" (the bug this refactor kills) — and the pre-parsed, flattened blocks.
    """
    scenes = [s for s in ch.scenes if s.has_prose]
    if not scenes:
        return []
    out: list[Par] = [Par(children=[R(break_page=True)])]
    heading = f"{ch.label.upper()}" + (f" — {ch.title.upper()}" if ch.title else "")
    out.append(
        Par(
            alignment="center",
            spacing=Spacing(before=1200, after=240, line=DOUBLE_LINE),
            children=[shunn_run(heading)],
        )
    )
    for si, sc in enumerate(scenes):
        if si > 0:
            out.append(Par(alignment="center", spacing=_double(), children=[shunn_run("#")]))
        for para in shunn_plain_blocks(sc.blocks):
            out.append(para)
    return out


def shunn_divider(label: str) -> list[Par]:
    """A plain grouping divider for Shunn — a centered uppercased label ("PART I — TITLE" /
    "ACT I …" / "VOLUME I …"), no rich styling. Consumes the spine's pre-resolved node label.
    """
    return [
        Par(children=[R(break_page=True)]),
        Par(
            alignment="center",
            spacing=Spacing(before=1200, after=240, line=DOUBLE_LINE),
            children=[shunn_run(label.upper())],
        ),
    ]


def render_shunn_doc(spine: ManuscriptSpine, policy: ExportPolicy) -> DocxBuilder:
    """Shunn DOCX emitter — consumes the ManuscriptSpine.

    Byline + word count come from ExportMetadata and the spine counts; rich LitRPG blocks are
    flattened by :func:`shunn_plain_blocks` (submission-safe).
    """
    title = spine.metadata.title
    author = spine.metadata.author if policy.include_author_byline else None
    byline = (author.strip() if author else "") or "Author"
    # JS `.pop()` on a split with a trailing separator yields "" — falsy, so it falls back to byline.
    surname = re.split(r"\s+", byline)[-1] or byline
    title_upper = title.upper()
    right_tab = inches_to_twips(6.5)
    words = spine_counts(spine).words

    children: list[Par] = [
        Par(
            tab_stops=[TabStop("right", right_tab)],
            children=[shunn_run(byline), shunn_run(f"\tabout {round_words(words):,} words")],
        ),
        Par(
            alignment="center",
            spacing=Spacing(before=2800, line=DOUBLE_LINE),
            children=[shunn_run(title_upper)],
        ),
        Par(alignment="center", spacing=_double(), children=[shunn_run(f"by {byline}")]),
    ]

    def emit_part(part: SpinePartNode) -> None:
        if policy.render_parts:
            children.extend(shunn_divider(part.label))
        for ch in part.chapters:
            children.extend(shunn_chapter(ch))

    for node in spine.nodes:
        if isinstance(node, SpineVolumeNode):
            if policy.render_parts:
                children.extend(shunn_divider(node.label))
            for part in node.parts:
                emit_part(part)
        elif isinstance(node, SpinePartNode):
            emit_part(node)
        else:
            children.extend(shunn_chapter(node))

    builder = DocxBuilder(
        title=title,
        creator="Writers' Desk",
        margin_twips=inches_to_twips(1),
        different_first_page=True,
    )
    # docx-js `headers: { default: shunnHeader(...), first: new Header({ children: [] }) }` —
    # the title page carries no running header.
    builder.set_header(shunn_header(surname, title_upper))
    builder.set_header([], first_page=True)
    builder.add_body(children)
    return builder
