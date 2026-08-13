"""Port of ``frontend/src/desk/manuscript/docxStyles.ts`` — named Word styles for the Reader DOCX.

Derived from the ``ExportPolicy``'s typography intent. This replaces the emitter's inline one-off
run formatting with a stylesheet the emitter references by id — so the reader can be re-skinned by
editing THESE definitions (or the policy that feeds them) without touching the layout code, and a
Word user sees real named styles instead of hard-formatted paragraphs.

Sizes are half-points (docx convention).
"""

from __future__ import annotations

from .ooxml import StyleDef, inches_to_twips
from .presets import ExportPolicy


class STYLE:
    """Stable style ids the emitter references."""

    book_title = "RWBookTitle"
    series_line = "RWSeriesLine"
    book_subtitle = "RWBookSubtitle"
    render_descriptor = "RWRenderDescriptor"
    author_byline = "RWAuthorByline"
    volume_eyebrow = "RWVolumeEyebrow"
    volume_title = "RWVolumeTitle"
    part_eyebrow = "RWPartEyebrow"
    part_title = "RWPartTitle"
    divider_subtitle = "RWDividerSubtitle"
    chapter_label = "RWChapterLabel"
    chapter_title = "RWChapterTitle"
    pov_line = "RWPovLine"
    epigraph = "RWEpigraph"
    scene_break = "RWSceneBreak"
    body = "RWBody"
    body_first = "RWBodyFirst"


GRAY = "808080"
DIM = "606060"


def body_line(spacing: str) -> int:
    return 480 if spacing == "double" else 240 if spacing == "single" else 320  # "reader" default


def reader_style_defs(policy: ExportPolicy) -> list[StyleDef]:
    """Build the Reader DOCX stylesheet from a policy.

    Body font/size/line-spacing come from ``policy.typography``; the display sizes (titles,
    dividers) are the reader's typographic scale, expressed once here rather than sprinkled
    through the emitter.
    """
    font = policy.typography.body_font or "Georgia"
    body_size = int((policy.typography.body_size_pt or 11) * 2)
    line = body_line(policy.typography.line_spacing)

    def eyebrow(style_id: str, name: str) -> StyleDef:
        return StyleDef(
            style_id=style_id,
            name=name,
            font=font,
            size=24,
            color=GRAY,
            character_spacing=40,
            alignment="center",
        )

    return [
        # Prose body: first-line indent is the paragraph cue (classic print novel). BodyFirst (the
        # first paragraph of a scene / after a break) drops the indent per print convention.
        StyleDef(
            style_id=STYLE.body,
            name="RW Body",
            quick_format=True,
            font=font,
            size=body_size,
            alignment="both",
            line=line,
            line_rule="auto",
            indent_first_line=inches_to_twips(0.3),
        ),
        StyleDef(
            style_id=STYLE.body_first,
            name="RW Body First",
            based_on=STYLE.body,
            indent_first_line=0,
        ),
        StyleDef(style_id=STYLE.book_title, name="RW Book Title", font=font, bold=True, size=56, alignment="center"),
        StyleDef(style_id=STYLE.series_line, name="RW Series Line", font=font, size=20, color=GRAY, alignment="center"),
        StyleDef(
            style_id=STYLE.book_subtitle,
            name="RW Book Subtitle",
            font=font,
            italics=True,
            size=26,
            alignment="center",
        ),
        StyleDef(
            style_id=STYLE.render_descriptor,
            name="RW Render Descriptor",
            font=font,
            italics=True,
            size=24,
            color=GRAY,
            alignment="center",
        ),
        StyleDef(
            style_id=STYLE.author_byline,
            name="RW Author Byline",
            font=font,
            size=22,
            color=DIM,
            alignment="center",
        ),
        eyebrow(STYLE.volume_eyebrow, "RW Volume Eyebrow"),
        StyleDef(style_id=STYLE.volume_title, name="RW Volume Title", font=font, bold=True, size=48, alignment="center"),
        eyebrow(STYLE.part_eyebrow, "RW Part Eyebrow"),
        StyleDef(style_id=STYLE.part_title, name="RW Part Title", font=font, bold=True, size=40, alignment="center"),
        StyleDef(
            style_id=STYLE.divider_subtitle,
            name="RW Divider Subtitle",
            font=font,
            italics=True,
            size=24,
            color=GRAY,
            alignment="center",
        ),
        StyleDef(
            style_id=STYLE.chapter_label, name="RW Chapter Label", font=font, bold=True, size=28, alignment="center"
        ),
        StyleDef(
            style_id=STYLE.chapter_title, name="RW Chapter Title", font=font, italics=True, size=26, alignment="center"
        ),
        StyleDef(style_id=STYLE.pov_line, name="RW POV Line", font=font, size=18, color=GRAY, alignment="center"),
        StyleDef(
            style_id=STYLE.epigraph,
            name="RW Epigraph",
            font=font,
            italics=True,
            size=22,
            color=DIM,
            alignment="center",
        ),
        StyleDef(style_id=STYLE.scene_break, name="RW Scene Break", font=font, size=24, color=GRAY, alignment="center"),
    ]
