"""Port of ``frontend/src/desk/manuscript/presets.ts`` — export presets as real policy objects.

A preset resolves to one ``ExportPolicy`` that declares, in one place: which emitter runs, page
setup, typography intent, scene-break rules, which metadata is included, how LitRPG panels are
treated, and prose source (raw vs beautified — an EXPLICIT choice, never an invisible transform).
Emitters read these fields; they do not re-decide policy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

EmitterKind = Literal["reader_docx", "shunn_docx", "markdown"]
ProseSource = Literal["raw", "beautified"]
RichBlockPolicy = Literal["styled", "flatten", "passthrough"]
RunningHeader = Literal["none", "reader_pageno", "shunn_surname_title_pageno"]
PreflightMode = Literal["block_on_error", "warn_only"]
LineSpacing = Literal["single", "double", "reader"]


@dataclass(frozen=True)
class ExportTypography:
    body_font: str
    body_size_pt: float
    line_spacing: LineSpacing
    monospace: bool


@dataclass(frozen=True)
class ExportPageSetup:
    margin_inches: float
    running_header: RunningHeader
    #: Distinct title page (Shunn suppresses the running header on page 1).
    title_page: bool


@dataclass(frozen=True)
class ExportPolicy:
    preset: str
    label: str
    description: str
    emitter: EmitterKind
    prose_source: ProseSource
    rich_blocks: RichBlockPolicy

    # --- structure rendering ---
    render_parts: bool
    scene_break_glyph: str

    # --- metadata inclusion ---
    include_series_line: bool
    include_subtitle: bool
    include_author_byline: bool
    # --- generated production pages (book format only) ---
    include_half_title: bool
    include_table_of_contents: bool

    # --- typography + page setup (policy owns styling decisions, not the emitter) ---
    typography: ExportTypography
    page_setup: ExportPageSetup

    # --- gates ---
    submission_safe: bool
    preflight: PreflightMode

    #: The title page. Intrinsic to a whole-book render, but nonsense wrapped around a single
    #: chapter file, so the CLI switches it off when the source has no book structure.
    #: Defaulted, so it must stay last in the field order.
    include_title_page: bool = True


READER_PROOF = ExportPolicy(
    preset="reader_proof",
    label="Reader DOCX",
    description="Styled book format with LitRPG interface panels, epigraphs, and part/chapter openings.",
    emitter="reader_docx",
    prose_source="beautified",
    rich_blocks="styled",
    render_parts=True,
    scene_break_glyph="⁂",
    include_series_line=True,
    include_subtitle=True,
    include_author_byline=True,
    include_half_title=True,
    include_table_of_contents=True,
    typography=ExportTypography(body_font="Georgia", body_size_pt=11, line_spacing="reader", monospace=False),
    page_setup=ExportPageSetup(margin_inches=1, running_header="reader_pageno", title_page=False),
    submission_safe=False,
    preflight="warn_only",
)

SUBMISSION_SHUNN = ExportPolicy(
    preset="submission_shunn",
    label="Shunn DOCX",
    description="Plain manuscript format for agents/editors — rich LitRPG blocks flattened to safe text.",
    emitter="shunn_docx",
    prose_source="beautified",
    rich_blocks="flatten",
    render_parts=True,
    scene_break_glyph="#",
    include_series_line=False,
    include_subtitle=False,
    include_author_byline=True,
    # A Shunn submission's cover page IS its title page; no half-title/TOC.
    include_half_title=False,
    include_table_of_contents=False,
    typography=ExportTypography(body_font="Courier New", body_size_pt=12, line_spacing="double", monospace=True),
    page_setup=ExportPageSetup(margin_inches=1, running_header="shunn_surname_title_pageno", title_page=True),
    submission_safe=True,
    preflight="block_on_error",
)

EDITORIAL_REVIEW = ExportPolicy(
    preset="editorial_review",
    label="Markdown",
    description="Semantic Markdown with YAML front matter — raw prose preserved verbatim for agents.",
    emitter="markdown",
    prose_source="raw",
    rich_blocks="passthrough",
    render_parts=True,
    scene_break_glyph="",  # markdown uses structural comments/headings, not a glyph
    include_series_line=True,
    include_subtitle=True,
    include_author_byline=False,
    include_half_title=False,  # semantic Markdown's YAML + "# title" already is the title page
    include_table_of_contents=False,
    typography=ExportTypography(body_font="", body_size_pt=0, line_spacing="single", monospace=False),
    page_setup=ExportPageSetup(margin_inches=0, running_header="none", title_page=False),
    submission_safe=False,
    preflight="warn_only",
)

_POLICIES: dict[str, ExportPolicy] = {
    "reader_proof": READER_PROOF,
    "submission_shunn": SUBMISSION_SHUNN,
    "editorial_review": EDITORIAL_REVIEW,
}

#: The supported presets, in the order the export UI offers them.
UI_EXPORT_PRESETS: tuple[ExportPolicy, ...] = (READER_PROOF, SUBMISSION_SHUNN, EDITORIAL_REVIEW)

#: Declared-but-unimplemented presets, mirrored from the TS union for parity of the error message.
DECLARED_UNIMPLEMENTED: tuple[str, ...] = ("print_proof", "ebook_source", "canon_bible")


def is_supported_preset(preset: str) -> bool:
    return preset in _POLICIES


def resolve_policy(preset: str) -> ExportPolicy:
    """Resolve a preset to its policy. Raises for declared-but-unimplemented presets."""
    if preset not in _POLICIES:
        raise ValueError(
            f'Export preset "{preset}" is declared but not yet implemented — it is internal-only '
            f"and must not be offered as a working export."
        )
    return _POLICIES[preset]


def with_overrides(policy: ExportPolicy, **kwargs) -> ExportPolicy:
    """A copy of ``policy`` with top-level fields replaced (CLI flags layer on top of a preset)."""
    return replace(policy, **kwargs)
