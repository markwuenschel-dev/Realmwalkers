"""Seed prior canon: import drafted manuscript scenes as APPROVED, build memory (DESIGN §14, Phase 2).

The drafter writes the NEXT scene against prior state — the per-POV + omniscient rolling summaries,
beat-scoped canon, the in-chapter tail. Against an empty database it starts cold: no summary to
narrow what a POV knows, no canon to retrieve. This importer loads already-written scene files
(`book1/manuscript/scenes/*.md`) as `approved` Scene rows so that state exists, folds them forward
into the rolling summaries, and (re)builds the canon RAG index from `series/canon` — so one command
makes the system continuation-ready.

Idempotent: re-running upserts the same (chapter, scene) seed rows rather than duplicating them, and
the canon index is rebuilt in place. Voice specs are a separate concern (see legacy/set_voice.py); hard stats
are declared via beats going forward — this importer never invents `CharacterState` from prose.

    uv run python -m dominion.workers.memory.seed --book "Dominion Realm"
    uv run python -m dominion.workers.memory.seed --book "Dominion Realm" --no-summaries  # no API key

`--no-summaries` skips the LLM fold (the only step needing ANTHROPIC_API_KEY); a later run without it
folds the already-imported scenes into the summaries. The whole import is one transaction: if the
summary fold fails (e.g. no key), nothing persists — re-run with `--no-summaries`.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared import agent_ops
from dominion.shared.chapter_lock import acquire_chapter_workflow_lock
from dominion.shared.chapter_order import chapter_position
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import ChapterStatus, SceneStatus
from dominion.shared.models import Book, Chapter, Scene
from dominion.workers.memory import canon_rag, summaries

# Imported manuscript prose is human-authored; mark it distinctly from 'agent' / 'agent+human_edit'.
_SEED_SOURCE = "human"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# The template's trailing editorial block ("## Scene-local notes ...") is not prose — drop it.
_NOTES_RE = re.compile(r"^#{1,6}\s+scene-local notes\b.*\Z", re.IGNORECASE | re.MULTILINE | re.DOTALL)
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_DIGITS_RE = re.compile(r"\d+")


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Split a `--- ... ---` YAML-ish header off the body. Only scalar `key: value` lines are read
    (the fields we use are scalars); list/nested lines are ignored. No PyYAML dependency."""
    text = raw.replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        kv = _KV_RE.match(line)
        if kv:
            meta[kv.group(1).strip().lower()] = kv.group(2).strip().strip("'\"")
    return meta, match.group(2)


def _extract_prose(body: str) -> str:
    """Strip the non-prose scaffolding the scene template wraps around the text: HTML writer-notes
    comments, the trailing 'Scene-local notes' block, and the leading run of title/brief/rule lines.
    Internal `---` scene breaks (which appear after prose has begun) are preserved."""
    body = _COMMENT_RE.sub("", body)
    body = _NOTES_RE.sub("", body)
    lines = body.splitlines()
    start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        is_rule = len(s) >= 3 and set(s) <= {"-"}
        if not s or s.startswith("#") or s.startswith(">") or is_rule:
            start = i + 1
            continue
        break
    return "\n".join(lines[start:]).strip()


def _normalize_pov(raw: str | None) -> str:
    """Drop a parenthetical alias and collapse whitespace: 'Marcus (Marc)' -> 'Marcus'. Used
    verbatim as the chapter POV key, so it must match the voice/profile names you use elsewhere."""
    if not raw:
        return "Unknown"
    pov = re.sub(r"\s+", " ", _PAREN_RE.sub("", raw)).strip()
    return pov or "Unknown"


def _scene_no(meta: dict[str, str], path: Path, fallback: int) -> int:
    """Explicit `scene:`/`scene_no:` frontmatter wins; else the first digits of `scene_id`/filename
    (SCENE-001 -> 1); else the file's position in the run."""
    for key in ("scene", "scene_no"):
        if key in meta and meta[key].strip().isdigit():
            return int(meta[key])
    digits = _DIGITS_RE.search(meta.get("scene_id") or path.stem)
    return int(digits.group()) if digits else fallback


def _chapter_no(meta: dict[str, str], default: int) -> int:
    """Explicit `chapter:` frontmatter, else the default (the manuscript carries no chapter key yet)."""
    raw = meta.get("chapter", "")
    return int(raw) if raw.strip().isdigit() else default


@dataclass
class SeedReport:
    imported: list[str] = field(default_factory=list)  # newly created seed scenes
    updated: list[str] = field(default_factory=list)  # existing seed scenes refreshed in place
    skipped: list[str] = field(default_factory=list)  # files with no extractable prose
    warnings: list[str] = field(default_factory=list)  # e.g. a file's POV != the existing chapter's
    summaries_built: int = 0
    canon_chunks: int = 0


async def seed_manuscript(
    session: AsyncSession,
    *,
    book_title: str,
    scenes_dir: str | Path,
    canon_dir: str | Path | None = None,
    build_summaries: bool = True,
    default_chapter: int = 1,
) -> SeedReport:
    """Import every `*.md` scene under scenes_dir (skipping `_`-prefixed template/draft files) as an
    approved Scene, fold the imported scenes forward into the rolling summaries, and optionally
    rebuild the canon index. Caller owns the commit (so this stays unit-testable on any session)."""
    report = SeedReport()
    book = await _get_or_create_book(session, book_title)

    # (chapter_no, scene_no, Scene) so the summary fold runs in narrative order regardless of glob order.
    seeded: list[tuple[int, int, Scene]] = []
    for path in sorted(Path(scenes_dir).glob("*.md")):
        if path.stem.startswith("_"):
            continue
        meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        prose = _extract_prose(body)
        if not prose:
            report.skipped.append(f"{path.name} (no prose)")
            continue

        chapter_no = _chapter_no(meta, default_chapter)
        scene_no = _scene_no(meta, path, fallback=len(seeded) + 1)
        pov = _normalize_pov(meta.get("pov"))
        title = meta.get("title") or path.stem

        chapter = await _get_or_create_chapter(session, book_id=book.id, chapter_no=chapter_no, pov=pov)
        if chapter.pov != pov:
            # We never clobber an existing chapter's POV, but a mismatch silently mis-keys the per-POV
            # summary (it folds under chapter.pov). Surface it so the author can align file or chapter.
            report.warnings.append(
                f"ch{chapter_no}: existing chapter POV {chapter.pov!r} != {path.name}'s {pov!r}; "
                f"kept {chapter.pov!r} — the per-POV summary keys on it. Align the chapter or the file."
            )
        # #283 C3. Seeding lands scenes at APPROVED — a real authority write, and it used to take no
        # lock at all, so it could interleave with a concurrent approval or revision on the same chapter
        # and it shifts scene-packet staleness downstream through the approved-scene hash. The advisory
        # lock is transaction-scoped, so taking it here serializes this CLI against every other chapter
        # mutation and releases on the seed transaction's commit. Re-taking it per scene is harmless:
        # the same transaction already holds it.
        #
        # The APPROVED status itself is NOT gated on a contract, and that is deliberate: this command
        # imports prose the author already wrote, so the human running it IS the authority. What was
        # missing was serialization and visibility, not permission.
        await acquire_chapter_workflow_lock(session, chapter.id)
        scene, created = await _upsert_seed_scene(session, chapter_id=chapter.id, scene_no=scene_no, prose=prose)
        seeded.append((chapter_no, scene_no, scene))
        (report.imported if created else report.updated).append(f"ch{chapter_no}.s{scene_no} {title!r} ({chapter.pov})")

    await session.flush()

    if build_summaries:
        for _ch, _sc, scene in sorted(seeded, key=lambda t: (t[0], t[1])):
            await summaries.refresh_on_approval(session, scene_id=scene.id)
            report.summaries_built += 1

    if canon_dir is not None:
        report.canon_chunks = await canon_rag.ingest_path(session, book_id=book.id, root=canon_dir)

    return report


async def _get_or_create_book(session: AsyncSession, title: str) -> Book:
    book = (await session.execute(select(Book).where(Book.title == title))).scalar_one_or_none()
    if book is None:
        book = Book(title=title)
        session.add(book)
        await session.flush()
    return book


async def _get_or_create_chapter(session: AsyncSession, *, book_id: object, chapter_no: int, pov: str) -> Chapter:
    chapter = (
        await session.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_no == chapter_no))
    ).scalar_one_or_none()
    if chapter is None:
        chapter = Chapter(
            book_id=book_id,
            chapter_no=chapter_no,
            pov=pov,
            status=ChapterStatus.DONE,
            position=chapter_position("chapter", chapter_no),
        )
        session.add(chapter)
        await session.flush()
    return chapter


async def _upsert_seed_scene(
    session: AsyncSession, *, chapter_id: object, scene_no: int, prose: str
) -> tuple[Scene, bool]:
    """Find the existing seed row for (chapter, scene) and refresh its prose, or insert one. Keyed on
    prose_source='human' so a re-import never clobbers an agent-drafted version of the same scene."""
    existing = (
        await session.execute(
            select(Scene)
            .where(
                Scene.chapter_id == chapter_id,
                Scene.scene_no == scene_no,
                Scene.prose_source == _SEED_SOURCE,
            )
            .order_by(Scene.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.prose = prose
        existing.status = SceneStatus.APPROVED
        await session.flush()
        return existing, False
    scene = Scene(
        chapter_id=chapter_id,
        scene_no=scene_no,
        version=1,
        status=SceneStatus.APPROVED,
        prose=prose,
        prose_source=_SEED_SOURCE,
    )
    session.add(scene)
    await session.flush()
    return scene, True


async def _run(args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        # The summary fold routes through the review_model role, whose Settings-persisted policy
        # (model tier, backend "llm" vs "agent_cli", fallbacks) lives in the DB. The web app and
        # worker load it on startup; this standalone CLI must too, or the fold silently ignores
        # the user's Settings and runs the env-default model over the HTTP API.
        await agent_ops.apply_model_overrides(session)
        report = await seed_manuscript(
            session,
            book_title=args.book,
            scenes_dir=args.scenes_dir,
            canon_dir=None if args.no_canon else args.canon_dir,
            build_summaries=not args.no_summaries,
        )
        await session.commit()

    print(
        f"seeded '{args.book}': {len(report.imported)} imported, {len(report.updated)} updated, "
        f"{len(report.skipped)} skipped"
    )
    for line in (*report.imported, *report.updated):
        print(f"  + {line}")
    for line in report.skipped:
        print(f"  - {line}")
    for line in report.warnings:
        print(f"  ! {line}")
    if not args.no_summaries:
        print(f"summaries folded: {report.summaries_built} scene(s)")
    if not args.no_canon:
        print(f"canon indexed: {report.canon_chunks} passage(s) from {args.canon_dir!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import drafted manuscript scenes as approved prior state + build memory (Phase 2)."
    )
    parser.add_argument("--book", required=True)
    parser.add_argument("--scenes-dir", default=settings.scenes_dir)
    parser.add_argument("--canon-dir", default=settings.canon_dir)
    parser.add_argument("--no-canon", action="store_true", help="skip rebuilding the canon RAG index")
    parser.add_argument(
        "--no-summaries",
        action="store_true",
        help="skip the LLM summary fold (the only step needing ANTHROPIC_API_KEY)",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
