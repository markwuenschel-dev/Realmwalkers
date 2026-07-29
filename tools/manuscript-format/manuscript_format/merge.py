"""Multi-file manuscript assembly.

The formatter's normal input is one Markdown/DOCX file. This module expands folders and multiple
selected files, ingests each with the existing parser, then combines their structural manuscripts
before rendering. Authored front matter is moved into the Reader front-matter plan, body chapters
retain source order, and authored back matter is emitted after the body.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from .ingest import SourceDoc, load_source, split_plain_markdown
from .labels import section_rank
from .spine import Manuscript, ManuscriptChapter, ManuscriptPart, ManuscriptVolume

SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".docx"})
_GENERATED_SUFFIXES = (".reader.docx", ".shunn.docx", ".reference.docx")
_GENERATED_DIRS = frozenset({"out", "output", "outputs", "export", "exports", "render", "renders", "rendered", "build"})
_DERIVED_DOCX_MARKERS = ("working_draft", "formatted", "fixed_format", "updated_format")
_NATURAL_PART = re.compile(r"(\d+)")


def natural_path_key(path: Path) -> tuple:
    """Human filename ordering: chapter_2 sorts before chapter_10."""
    parts: list[tuple[int, object]] = []
    for token in _NATURAL_PART.split(str(path).lower()):
        parts.append((0, int(token)) if token.isdigit() else (1, token))
    return tuple(parts)


def _eligible(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and not name.startswith(".")
        and not name.endswith(_GENERATED_SUFFIXES)
    )


def expand_inputs(values: list[str | Path]) -> list[Path]:
    """Expand files/folders deterministically while preferring editable source over exports.

    Folder scans ignore common generated-output directories. If editable Markdown exists, derived
    working/formatted DOCX exports discovered in the same scan are excluded so the book does not
    silently duplicate chapters or round-trip interface tables. Explicitly selected DOCX files are
    still accepted and their known panel structures are recovered by :mod:`ingest`.
    """
    found: list[tuple[Path, bool]] = []  # (path, came_from_folder_scan)
    for raw in values:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            for candidate in path.rglob("*"):
                relative_parts = candidate.relative_to(path).parts
                if any(part.startswith(".") for part in relative_parts):
                    continue
                if any(part.lower() in _GENERATED_DIRS for part in relative_parts[:-1]):
                    continue
                if _eligible(candidate):
                    found.append((candidate, True))
        elif _eligible(path):
            found.append((path, False))
        else:
            raise FileNotFoundError(f"no supported manuscript source: {path}")

    unique: dict[str, tuple[Path, bool]] = {}
    for path, scanned in found:
        key = os.path.normcase(str(path))
        unique[key] = (path, unique.get(key, (path, False))[1] or scanned)

    values_found = list(unique.values())
    scanned_has_markdown = any(
        scanned and path.suffix.lower() in {".md", ".markdown", ".txt"}
        for path, scanned in values_found
    )
    filtered: list[Path] = []
    for path, scanned in values_found:
        normalized = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
        if (
            scanned
            and scanned_has_markdown
            and path.suffix.lower() == ".docx"
            and any(marker in normalized for marker in _DERIVED_DOCX_MARKERS)
        ):
            continue
        filtered.append(path)

    return sorted(filtered, key=natural_path_key)


def default_merge_title(paths: list[Path]) -> str:
    if not paths:
        return "Untitled"
    common = Path(os.path.commonpath([str(p) for p in paths]))
    if common.is_file() or common.suffix:
        common = common.parent
    return common.name or paths[0].stem


def _source_manuscript(path: Path, split: str) -> tuple[SourceDoc, Manuscript]:
    src = load_source(path)
    manuscript = src.manuscript
    if manuscript is None:
        if split == "none":
            from .spine import ManuscriptScene

            manuscript = Manuscript(
                title=src.title or path.stem,
                chapters=[
                    ManuscriptChapter(
                        position=0,
                        chapter_no=1,
                        scenes=[ManuscriptScene(scene_no=1, prose=src.raw_text)],
                    )
                ],
            )
        else:
            manuscript = split_plain_markdown(
                src.raw_text,
                title=src.title or path.stem,
                front_matter=src.front_matter,
            )
    return src, manuscript


def _copy_groups(
    target: Manuscript,
    source: Manuscript,
    source_index: int,
) -> dict[str, str]:
    """Copy volume/part definitions and return old part-id -> new part-id mapping."""
    volume_ids: dict[str, str] = {}
    for volume in source.volumes:
        new_id = f"source{source_index}:{volume.id}"
        volume_ids[volume.id] = new_id
        target.volumes.append(replace(deepcopy(volume), id=new_id))

    part_ids: dict[str, str] = {}
    for part in source.parts:
        new_id = f"source{source_index}:{part.id}"
        part_ids[part.id] = new_id
        target.parts.append(
            replace(
                deepcopy(part),
                id=new_id,
                volume_id=volume_ids.get(part.volume_id, part.volume_id),
            )
        )
    return part_ids


def _ordered_chapters(chapters: list[ManuscriptChapter]) -> list[ManuscriptChapter]:
    """Canonical bands: authored front matter, body in source order, authored back matter."""
    front = [c for c in chapters if c.kind == "front_matter"]
    body = [c for c in chapters if c.kind not in ("front_matter", "back_matter")]
    back = [c for c in chapters if c.kind == "back_matter"]
    front.sort(key=lambda c: (section_rank(c.section_type), c.position or 0))
    back.sort(key=lambda c: (section_rank(c.section_type), c.position or 0))
    return [*front, *body, *back]


def merge_sources(
    paths: list[Path],
    *,
    title: str | None = None,
    split: str = "auto",
) -> tuple[Manuscript, str]:
    """Ingest and combine sources. Returns ``(manuscript, concatenated_raw_markdown)``."""
    if not paths:
        raise ValueError("no manuscript sources were supplied")

    merged = Manuscript(title=title or default_merge_title(paths))
    raw_parts: list[str] = []
    collected: list[ManuscriptChapter] = []
    first_structured_title: str | None = None

    for source_index, path in enumerate(paths, start=1):
        src, manuscript = _source_manuscript(path, split)
        raw_parts.append(src.raw_text.strip())

        if src.structured and manuscript.title and manuscript.title != "Untitled":
            first_structured_title = first_structured_title or manuscript.title
        merged.series = merged.series or manuscript.series
        merged.book_no = merged.book_no if merged.book_no is not None else manuscript.book_no
        merged.subtitle = merged.subtitle or manuscript.subtitle

        part_ids = _copy_groups(merged, manuscript, source_index)
        for chapter in manuscript.chapters:
            copied = deepcopy(chapter)
            copied.part_id = part_ids.get(copied.part_id, copied.part_id)
            copied.position = len(collected)
            collected.append(copied)

    if title is None and first_structured_title:
        merged.title = first_structured_title

    merged.chapters = _ordered_chapters(collected)
    for position, chapter in enumerate(merged.chapters):
        chapter.position = position

    return merged, "\n\n".join(part for part in raw_parts if part)
