"""Push style documents from the working tree into `style_documents`.

    python -m dominion.tools.push_style --dry-run          # show what would change
    python -m dominion.tools.push_style                     # push series/style/*.md
    python -m dominion.tools.push_style --sql               # emit SQL instead of connecting

WHY THIS EXISTS. `series/` is gitignored on purpose — creative content does not go to GitHub or the
deploy box. Deploy is a `git pull`, so the style guides can never arrive that way, and any loader that
reads them from disk is silently inert in production. The content has to travel by a different road,
and Postgres is the road that already exists: the canon RAG index lives there.

`--sql` is the mode that matters for deploying. The box's database is private to the instance and not
reachable from a laptop, so the normal flow is to emit SQL locally and pipe it over ssh into psql on the
box. The *file* never lands on the server filesystem — only its content, into the database, which is
where canon already is. That keeps the local-only rule intact rather than punching a hole in it.

Idempotent by slug: pushing twice is a no-op on unchanged content and an update on changed content.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.db import SessionFactory
from dominion.shared.models import StyleDocument
from dominion.workers.context.style_source import slug_for

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = "series/style"


def _documents(root: str) -> list[tuple[str, str, str]]:
    """(slug, relative source path, content) for every markdown file under `root`."""
    base = Path(root)
    if not base.is_absolute():
        base = _PROJECT_ROOT / base
    if not base.is_dir():
        return []
    out: list[tuple[str, str, str]] = []
    for path in sorted(base.rglob("*.md")):
        rel = path.relative_to(_PROJECT_ROOT).as_posix()
        out.append((slug_for(rel), rel, path.read_text(encoding="utf-8")))
    return out


def _sql_literal(value: str) -> str:
    """Dollar-quoted so markdown — full of quotes, backslashes and apostrophes — survives verbatim."""
    tag = "$style$"
    n = 0
    while tag in value:
        n += 1
        tag = f"$style{n}$"
    return f"{tag}{value}{tag}"


def emit_sql(docs: list[tuple[str, str, str]]) -> str:
    lines = ["BEGIN;"]
    for slug, src, content in docs:
        lines.append(
            "INSERT INTO style_documents (slug, content, source_path, updated_at) VALUES "
            f"({_sql_literal(slug)}, {_sql_literal(content)}, {_sql_literal(src)}, now()) "
            "ON CONFLICT (slug) DO UPDATE SET content = EXCLUDED.content, "
            "source_path = EXCLUDED.source_path, updated_at = now();"
        )
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


async def push(session: AsyncSession, docs: list[tuple[str, str, str]], *, dry_run: bool) -> list[str]:
    """Upsert each document. Returns a human-readable line per document."""
    report: list[str] = []
    for slug, src, content in docs:
        row = (await session.execute(select(StyleDocument).where(StyleDocument.slug == slug))).scalar_one_or_none()
        if row is None:
            verb = "would add" if dry_run else "added"
            if not dry_run:
                session.add(StyleDocument(slug=slug, content=content, source_path=src))
        elif row.content != content:
            verb = "would update" if dry_run else "updated"
            if not dry_run:
                row.content = content
                row.source_path = src
        else:
            verb = "unchanged"
        report.append(f"  {verb:<13} {slug:<34} {len(content):>7,} chars  ({src})")
    return report


async def _run(args: argparse.Namespace) -> None:
    docs = _documents(args.root)
    if not docs:
        print(f"no markdown found under {args.root!r} — nothing to push")
        return

    if args.sql:
        # Bytes, not print(). Windows text-mode stdout translates every newline into CRLF, and those
        # carriage returns ride inside the dollar-quoted literal straight into the database. Every
        # structured reader downstream anchors on "\n", so the document loads and then matches nothing:
        # no error, no warning, just guidance that silently stops applying. That is precisely how the
        # first push shipped 34,689 characters of drift patterns that scoped to zero patterns.
        sys.stdout.buffer.write(emit_sql(docs).encode("utf-8"))
        return

    async with SessionFactory() as session:
        report = await push(session, docs, dry_run=args.dry_run)
        if not args.dry_run:
            await session.commit()
    print(f"{'DRY RUN — ' if args.dry_run else ''}{len(docs)} document(s) from {args.root!r}:")
    print("\n".join(report))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push style documents into Postgres so the deployed app can read them."
    )
    parser.add_argument("--root", default=DEFAULT_ROOT, help=f"directory to push (default {DEFAULT_ROOT})")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument(
        "--sql",
        action="store_true",
        help="emit idempotent upsert SQL on stdout instead of connecting — pipe this to psql on the box",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
