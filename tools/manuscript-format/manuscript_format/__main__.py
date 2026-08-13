"""Command-line entry point: ``python -m manuscript_format INPUT [options]``.

Reads a Markdown or DOCX manuscript and emits any combination of the four artifacts the Writers'
Desk exporter produces. The pipeline is the same one the app runs::

    source ──ingest──▶ Manuscript ──build_spine──▶ ManuscriptSpine ──policy──▶ emitter
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from .ingest import load_source, split_plain_markdown
from .labels import resolve_export_metadata
from .merge import expand_inputs, merge_sources
from .presets import ExportPageSetup, ExportPolicy, ExportTypography, resolve_policy, with_overrides
from .render_markdown import render_markdown
from .render_reader import build_doc_doc, render_reader_doc
from .render_shunn import render_shunn_doc
from .spine import ManuscriptSpine, build_spine, spine_counts, spine_has_prose

TARGETS = ("reader", "shunn", "md", "doc")


def _slug(title: str) -> str:
    """The emitters' own filename rule: JS ``\\w`` is ASCII-only, so do not use Python's ``\\w``."""
    return re.sub(r"^_+|_+$", "", re.sub(r"[^A-Za-z0-9_]+", "_", title)) or "manuscript"


def _apply_typography(policy: ExportPolicy, args: argparse.Namespace) -> ExportPolicy:
    """Layer CLI overrides on top of a preset without mutating the preset itself."""
    typo = policy.typography
    page = policy.page_setup
    if args.body_font or args.body_size or args.line_spacing:
        typo = ExportTypography(
            body_font=args.body_font or typo.body_font,
            body_size_pt=args.body_size or typo.body_size_pt,
            line_spacing=args.line_spacing or typo.line_spacing,
            monospace=typo.monospace,
        )
    if args.margin is not None:
        page = ExportPageSetup(
            margin_inches=args.margin,
            running_header=page.running_header,
            title_page=page.title_page,
        )
    overrides: dict = {"typography": typo, "page_setup": page}
    if args.scene_break is not None:
        overrides["scene_break_glyph"] = args.scene_break
    if args.no_parts:
        overrides["render_parts"] = False
    if args.no_half_title:
        overrides["include_half_title"] = False
    if args.no_toc:
        overrides["include_table_of_contents"] = False
    return with_overrides(policy, **overrides)


def _build_spine(
    args: argparse.Namespace, paths: list[Path]
) -> tuple[ManuscriptSpine, str, str, bool]:
    """Ingest one source or assemble many. Returns ``(spine, title, raw_text, assembled)``."""
    assembled = len(paths) > 1 or any(Path(raw).is_dir() for raw in args.input)

    if assembled:
        manuscript, raw_text = merge_sources(paths, title=args.title, split=args.split)
        title = args.title or manuscript.title
    else:
        in_path = paths[0]
        src = load_source(in_path)
        title = args.title or src.title or in_path.stem
        raw_text = src.raw_text
        manuscript = src.manuscript
        if manuscript is None:
            if args.split == "none":
                from .spine import Manuscript, ManuscriptChapter, ManuscriptScene

                manuscript = Manuscript(
                    title=title,
                    chapters=[
                        ManuscriptChapter(
                            position=0,
                            chapter_no=1,
                            pov="",
                            scenes=[ManuscriptScene(scene_no=1, prose=src.raw_text)],
                        )
                    ],
                )
            else:
                manuscript = split_plain_markdown(
                    src.raw_text, title=title, front_matter=src.front_matter
                )

    # Explicit CLI metadata always wins over whatever the file(s) declared.
    if args.title:
        manuscript.title = args.title
    if args.series is not None:
        manuscript.series = args.series
    if args.book_no is not None:
        manuscript.book_no = args.book_no
    if args.subtitle is not None:
        manuscript.subtitle = args.subtitle

    metadata = resolve_export_metadata(
        manuscript.title or title,
        manuscript.series,
        manuscript.book_no,
        manuscript.subtitle,
        author=args.author,
    )
    return build_spine(manuscript, metadata), metadata.title, raw_text, assembled


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manuscript-format",
        description="Format a Markdown or DOCX manuscript with the Writers' Desk export pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "targets:\n"
            "  reader  styled book DOCX — half-title, title page, Contents, dividers, LitRPG panels\n"
            "  shunn   plain submission DOCX — rich blocks flattened to safe text\n"
            "  md      semantic Markdown — YAML front matter, structural comments, verbatim prose\n"
            "  doc     flat canon-doc DOCX — no book format; markdown rendered straight through\n"
            "\n"
            "examples:\n"
            "  python -m manuscript_format book.md --to reader\n"
            "  python -m manuscript_format book.md --to reader,shunn,md -o out/\n"
            "  python -m manuscript_format notes.md --to doc\n"
            "  python -m manuscript_format old.docx --to reader --title 'The Glass Aqueduct'\n"
            "  python -m manuscript_format front/ chapters/ back/ --to reader --title 'My Novel'\n"
        ),
    )
    p.add_argument(
        "input",
        nargs="+",
        help="one or more source files, or a folder containing manuscript sources",
    )
    p.add_argument("-o", "--out-dir", default=".", help="output directory (default: cwd)")
    p.add_argument(
        "--to",
        default="reader",
        help=f"comma-separated targets from {', '.join(TARGETS)} (default: reader)",
    )
    p.add_argument(
        "--split",
        choices=("auto", "none"),
        default="auto",
        help="how to derive chapters from unstructured input: auto = '# ' headings, "
        "none = one chapter (default: auto). Ignored for semantic input.",
    )

    meta = p.add_argument_group("metadata (overrides whatever the source declares)")
    meta.add_argument("--title")
    meta.add_argument("--author", help="Shunn byline")
    meta.add_argument("--series")
    meta.add_argument("--book-no", type=int)
    meta.add_argument("--subtitle")
    meta.add_argument("--render-subtitle", help="descriptor line on the Reader title page")
    meta.add_argument("--exported-at", help="ISO-8601 stamp for the Markdown front matter")

    typo = p.add_argument_group("typography / layout")
    typo.add_argument("--body-font")
    typo.add_argument("--body-size", type=float, metavar="PT")
    typo.add_argument("--line-spacing", choices=("single", "double", "reader"))
    typo.add_argument("--margin", type=float, metavar="INCHES")
    typo.add_argument("--scene-break", metavar="GLYPH")
    typo.add_argument("--no-parts", action="store_true", help="suppress volume/part dividers")
    typo.add_argument("--no-half-title", action="store_true")
    typo.add_argument("--no-toc", action="store_true")
    typo.add_argument(
        "--book-front-matter",
        action="store_true",
        help="force half-title, title page and Contents even for a single-chapter file",
    )
    typo.add_argument("--draft", action="store_true", help="mark the Markdown front matter draft")

    p.add_argument(
        "--open",
        action="store_true",
        dest="open_result",
        help="open each written file when finished (used by the drag-and-drop shortcuts)",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    targets = [t.strip() for t in args.to.split(",") if t.strip()]
    unknown = [t for t in targets if t not in TARGETS]
    if unknown:
        print(
            f"error: unknown target(s) {', '.join(unknown)}; choose from {', '.join(TARGETS)}",
            file=sys.stderr,
        )
        return 2
    if not targets:
        print("error: --to requires at least one target", file=sys.stderr)
        return 2

    try:
        input_paths = expand_inputs(args.input)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not input_paths:
        print("error: no supported manuscript files were found", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg)

    written: list[Path] = []

    # The flat "doc" target concatenates multiple inputs in reading order. It intentionally does
    # not add book front matter; use reader for a complete assembled manuscript.
    if "doc" in targets:
        if len(input_paths) == 1:
            src = load_source(input_paths[0])
            title = args.title or src.title or input_paths[0].stem
            raw_text = src.raw_text
        else:
            manuscript, raw_text = merge_sources(input_paths, title=args.title, split=args.split)
            title = args.title or manuscript.title
        suffix = ".reference.docx" if len(input_paths) > 1 else ".docx"
        path = out_dir / f"{_slug(title)}{suffix}"
        build_doc_doc(title, raw_text).save(path)
        written.append(path)

    book_targets = [t for t in targets if t != "doc"]
    if book_targets:
        spine, title, _, assembled = _build_spine(args, input_paths)
        counts = spine_counts(spine)
        say(
            f"parsed: {counts.chapters} chapters · {counts.scenes} scenes · "
            f"{counts.words:,} words"
            + (f" · {counts.parts} parts" if counts.parts else "")
            + (f" · {counts.volumes} volumes" if counts.volumes else "")
        )
        if not spine_has_prose(spine):
            print("error: no scene in the source has any prose to render", file=sys.stderr)
            return 1

        # A single chapter with no Parts or Volumes is a chapter file, not a book. Wrapping it in
        # half-title + title page + Contents produces four pages of front matter around one
        # chapter, which is never what anyone wants from formatting one file.
        single_chapter = (
            counts.chapters <= 1 and counts.parts == 0 and counts.volumes == 0
        )
        if single_chapter and not assembled and not args.book_front_matter:
            say("single chapter — skipping half-title, title page and Contents")

        slug = _slug(title)
        for target in book_targets:
            if target == "reader":
                policy = _apply_typography(resolve_policy("reader_proof"), args)
                if single_chapter and not assembled and not args.book_front_matter:
                    policy = with_overrides(
                        policy,
                        include_half_title=False,
                        include_title_page=False,
                        include_table_of_contents=False,
                    )
                path = out_dir / f"{slug}.reader.docx"
                render_reader_doc(spine, policy, args.render_subtitle).save(path)
            elif target == "shunn":
                policy = _apply_typography(resolve_policy("submission_shunn"), args)
                path = out_dir / f"{slug}.shunn.docx"
                render_shunn_doc(spine, policy).save(path)
            else:  # md
                policy = resolve_policy("editorial_review")
                stamp = args.exported_at or datetime.now(UTC).isoformat(timespec="seconds")
                path = out_dir / f"{slug}.md"
                path.write_text(
                    render_markdown(spine, policy, draft=args.draft, exported_at=stamp),
                    encoding="utf-8",
                )
            written.append(path)

    for path in written:
        say(f"wrote {path}  ({path.stat().st_size:,} bytes)")

    if args.open_result:
        for path in written:
            try:
                os.startfile(path)  # noqa: S606 - Windows shell open, the point of the flag
            except (AttributeError, OSError) as exc:  # non-Windows, or no association
                print(f"could not open {path}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
