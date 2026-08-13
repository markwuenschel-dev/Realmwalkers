"""Port of the Markdown emitter + filename helpers in ``frontend/src/desk/lib/docx.ts``.

Semantic Markdown: YAML front matter, structural HTML comments carrying the chapter/scene identity,
and verbatim ``prose_raw`` — the safe, semantic source, never the beautified form.

Regex parity note: JavaScript's ``\\w`` is ASCII-only while Python's is Unicode-aware, so the
filename slug spells the class out as ``[A-Za-z0-9_]``.
"""

from __future__ import annotations

import re

from .presets import ExportPolicy
from .spine import ManuscriptSpine, SpineChapterNode, SpinePartNode, SpineVolumeNode


def docx_filename(title: str) -> str:
    return (re.sub(r"^_+|_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", title)) or "document") + ".docx"


def markdown_filename(title: str) -> str:
    return (re.sub(r"^_+|_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", title)) or "manuscript") + ".md"


def manifest_filename(title: str) -> str:
    return (re.sub(r"^_+|_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", title)) or "manuscript") + ".manifest.json"


def _yaml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _chapter_comment(ch: SpineChapterNode) -> str:
    title = f" title={_yaml_quote(ch.title)}" if ch.title else ""
    kind = f" kind={ch.kind}" if ch.kind != "chapter" else ""
    section = f" section_type={ch.section_type}" if ch.section_type else ""
    number = f" number={ch.chapter_no}" if ch.chapter_no is not None else ""  # numberless kinds omit it
    return f"<!-- chapter{number}{kind}{section}{title} pov={_yaml_quote(ch.pov)} -->"


def _markdown_chapter(lines: list[str], ch: SpineChapterNode) -> None:
    """Emit one chapter to the Markdown line buffer, using the resolved label + RAW prose (never the
    beautified form — Markdown preserves the safe, verbatim semantic source).
    """
    scenes = [s for s in ch.scenes if s.has_prose]
    if not scenes:
        return
    # A section chapter's label already IS its title (Glossary, Map…); a normal/prologue label is a
    # bare "Chapter N"/"Prologue" to which the chapter title is appended.
    is_section = ch.kind == "front_matter" or ch.kind == "back_matter"
    heading = ch.label if (is_section or not ch.title) else f"{ch.label} — {ch.title}"
    lines.extend([f"# {heading}", _chapter_comment(ch), ""])
    for si, sc in enumerate(scenes):
        lines.extend([f"<!-- scene index={si + 1} scene_no={sc.scene_no} -->", ""])
        lines.append(sc.prose_raw)
        lines.append("")


def render_markdown(
    spine: ManuscriptSpine,
    policy: ExportPolicy,
    *,
    draft: bool,
    exported_at: str,
) -> str:
    """Semantic Markdown emitter — consumes the ManuscriptSpine.

    Front matter is metadata-driven (no hard-coded series/book/litrpg flags); Part headings group
    their chapters; prose is preserved verbatim (``prose_raw``). ``exported_at`` is injected
    (deterministic for tests) rather than stamped inline.

    ``policy`` is accepted and deliberately unused — the TS signature ignores it too (``_policy``).
    """
    metadata = spine.metadata
    lines: list[str] = [
        "---",
        "schema: dominion-manuscript/v1",
        f"title: {_yaml_quote(metadata.title)}",
    ]
    # Metadata-driven — a standalone/new book with no series identity simply omits these lines.
    if metadata.series:
        lines.append(f"series: {_yaml_quote(metadata.series)}")
    if metadata.book_number is not None:
        lines.append(f"book: {metadata.book_number}")
    if metadata.subtitle:
        lines.append(f"subtitle: {_yaml_quote(metadata.subtitle)}")
    lines.extend(
        [
            f"exported_at: {_yaml_quote(exported_at)}",
            "source: writers-desk",
            "format: semantic-markdown",
            # JS interpolates a boolean lowercase; Python's str(True) would not.
            f"draft: {'true' if draft else 'false'}",
            "---",
            "",
            f"# {metadata.title}",
            "",
        ]
    )

    def emit_part(part: SpinePartNode) -> None:
        subtitle = f" subtitle={_yaml_quote(part.subtitle)}" if part.subtitle else ""
        lines.extend(
            [
                f"# {part.label}",
                f"<!-- part number={part.part_no} kind={part.kind}{subtitle} -->",
                "",
            ]
        )
        for ch in part.chapters:
            _markdown_chapter(lines, ch)

    for node in spine.nodes:
        if isinstance(node, SpineVolumeNode):
            subtitle = f" subtitle={_yaml_quote(node.subtitle)}" if node.subtitle else ""
            lines.extend(
                [
                    f"# {node.label}",
                    f"<!-- volume number={node.volume_no}{subtitle} -->",
                    "",
                ]
            )
            for part in node.parts:
                emit_part(part)
        elif isinstance(node, SpinePartNode):
            emit_part(node)
        else:
            _markdown_chapter(lines, node)

    return "\n".join(lines)
