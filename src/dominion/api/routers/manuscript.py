"""Manuscript ingest — parse dropped files into a chapter/scene preview, then import them.

`/parse` runs the best-effort splitter and reports the detected structure plus which chapter numbers
already exist in the book (read-only). `/import` writes the confirmed structure: it upserts chapters
and lands scenes in the review inbox (PENDING_REVIEW by default), refusing to overwrite an existing
chapter unless told to. Boundary editing between the two lives in the frontend.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import func, select

from dominion.api.deps import SessionDep
from dominion.shared.chapter_order import chapter_position
from dominion.shared.db import SessionFactory
from dominion.shared.enums import ChapterStatus, SceneStatus
from dominion.shared.models import Book, Chapter, Scene
from dominion.shared.schemas import (
    ManuscriptImportIn,
    ManuscriptImportReport,
    ManuscriptParseIn,
    ManuscriptScaffoldReport,
    ParsedChapterOut,
    ParsedManuscriptOut,
    ParsedSceneOut,
)
from dominion.workers import planner
from dominion.workers.memory.manuscript_split import split_files

log = structlog.get_logger()
router = APIRouter(prefix="/books", tags=["manuscript"])


async def _auto_title_chapter(chapter_id: uuid.UUID) -> None:
    """Best-effort background title for an untitled imported chapter. Imported chapters have no
    outline, so the title generator is fed the chapter's own prose (an excerpt). Never raises —
    propose_chapter_title is bounded and returns None on any trouble, and we skip an existing title."""
    try:
        async with SessionFactory() as session:
            chapter = await session.get(Chapter, chapter_id)
            if chapter is None or (chapter.title or "").strip():
                return
            proses = (
                (
                    await session.execute(
                        select(Scene.prose)
                        .where(Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED)
                        .order_by(Scene.scene_no)
                    )
                )
                .scalars()
                .all()
            )
            outline = "\n\n".join(p for p in proses if p)[:2000]
            title = await planner.propose_chapter_title(outline=outline, pov=chapter.pov or "")
            if title:
                chapter.title = title
                await session.commit()
    except Exception as exc:  # noqa: BLE001 — auto-title is opt-in polish, never part of the contract
        log.warning("manuscript_import.auto_title_failed", chapter=str(chapter_id), error=str(exc))


@router.post("/{book_id}/manuscript/parse", response_model=ParsedManuscriptOut)
async def parse_manuscript(book_id: uuid.UUID, body: ManuscriptParseIn, session: SessionDep) -> ParsedManuscriptOut:
    """Split the dropped files into a chapter/scene preview and flag chapter numbers that already
    exist in this book. Pure read — no DB writes."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    if not body.files:
        raise HTTPException(status_code=400, detail="no files provided")

    parsed = split_files([(f.filename, f.text) for f in body.files])

    existing = {
        n
        for n in (await session.execute(select(Chapter.chapter_no).where(Chapter.book_id == book_id))).scalars().all()
        if n is not None  # numberless kinds (prologue/…) carry no chapter_no and can't be a collision
    }

    return ParsedManuscriptOut(
        warnings=parsed.warnings,
        existing_chapter_nos=sorted(existing),
        chapters=[
            ParsedChapterOut(
                chapter_no=c.chapter_no,
                title=c.title,
                detected=c.detected,
                conflict=c.chapter_no in existing,
                warnings=c.warnings,
                scenes=[ParsedSceneOut(scene_no=s.scene_no, prose=s.prose, word_count=s.word_count) for s in c.scenes],
            )
            for c in parsed.chapters
        ],
    )


@router.post("/{book_id}/manuscript/import", response_model=ManuscriptImportReport)
async def import_manuscript(
    book_id: uuid.UUID, body: ManuscriptImportIn, session: SessionDep, background: BackgroundTasks
) -> ManuscriptImportReport:
    """Import the confirmed chapter/scene structure. Chapters upsert by (book_id, chapter_no) — a
    chapter number that already exists is refused (reported in skipped_conflicts) unless that chapter
    carries overwrite=True. Scenes land as `imported`-sourced and PENDING_REVIEW — imports are never
    approved directly (ADR 0028; the guard below rejects approve_directly), superseding any prior
    version at their scene_no. No LLM title call, and no summary fold on the review path — folds happen
    when each scene is approved in the inbox."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    if not body.chapters:
        raise HTTPException(status_code=400, detail="no chapters to import")
    # ADR 0028 (Slice 2, soft-enforce): imported prose must not become canonical without a contract.
    # The skip-review rail is retired for imports — they land in review; adoption (Slice 3) becomes the
    # path to a contract. Existing contractless-approved imports get an explicit operator escape later.
    if body.approve_directly:
        raise HTTPException(
            status_code=422,
            detail="imported scenes cannot be approved directly; they land in review and become canonical "
            "only through an approved contract (ADR 0028)",
        )

    status = SceneStatus.PENDING_REVIEW  # imports always enter the review inbox (approve_directly rejected above)
    created = updated = imported = 0
    skipped: list[int] = []
    warnings: list[str] = []
    auto_title_ids: set[uuid.UUID] = set()

    # Reading order is computed from kind + number (shared/chapter_order.py), never a raw number. `seq`
    # gives numberless sections (a second prologue, an interlude) a stable per-book tiebreak, based off
    # the current chapter count so a later import doesn't tie with an earlier one.
    seq_base = (
        await session.execute(select(func.count()).select_from(Chapter).where(Chapter.book_id == book_id))
    ).scalar_one()

    for idx, ch in enumerate(body.chapters):
        kind = ch.kind or "chapter"
        # A numberless kind (prologue/interlude/epilogue/front-/back-matter) carries no number, so it can
        # NEVER collide — it's always additive. Only a plain numbered chapter looks up an existing row.
        number = ch.chapter_no if kind == "chapter" else None
        existing = (
            (
                await session.execute(
                    select(Chapter).where(
                        Chapter.book_id == book_id, Chapter.kind == "chapter", Chapter.chapter_no == number
                    )
                )
            ).scalar_one_or_none()
            if number is not None
            else None
        )
        if existing is not None and not ch.overwrite:
            assert number is not None  # `existing` is only ever set for a numbered chapter
            skipped.append(number)
            continue
        position = chapter_position(kind, number, seq=seq_base + idx)
        if existing is None:
            chapter = Chapter(
                book_id=book_id,
                chapter_no=number,
                pov=ch.pov or "",
                kind=kind,
                status=ChapterStatus.PLANNED,
                position=position,
            )
            if ch.title:
                chapter.title = ch.title
            session.add(chapter)
            await session.flush()
            created += 1
        else:
            chapter = existing
            if ch.pov.strip():  # never clobber an existing chapter's POV with a blank
                chapter.pov = ch.pov
            if ch.title:
                chapter.title = ch.title
            chapter.kind = kind  # kind drives label + reading-order band; the user's explicit pick
            chapter.position = position
            updated += 1

        if body.auto_title and not (chapter.title or "").strip():
            auto_title_ids.add(chapter.id)

        for sc in ch.scenes:
            prose = sc.prose.strip()
            if not prose:
                label = f"Chapter {number}" if number is not None else kind.replace("_", " ")
                warnings.append(f"{label} scene {sc.scene_no}: empty prose — skipped.")
                continue
            prior = (
                await session.execute(
                    select(Scene)
                    .where(Scene.chapter_id == chapter.id, Scene.scene_no == sc.scene_no)
                    .order_by(Scene.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            scene = Scene(
                chapter_id=chapter.id,
                scene_no=sc.scene_no,
                version=(prior.version + 1) if prior else 1,
                parent_scene_id=prior.id if prior else None,
                status=status,
                prose=prose,
                prose_source="imported",
            )
            if prior is not None:
                prior.status = SceneStatus.SUPERSEDED
            session.add(scene)
            imported += 1

    await session.commit()
    for cid in auto_title_ids:
        background.add_task(_auto_title_chapter, cid)

    return ManuscriptImportReport(
        chapters_created=created,
        chapters_updated=updated,
        scenes_imported=imported,
        skipped_conflicts=skipped,
        warnings=warnings,
    )


# The standard AUTHORED production skeleton, in canonical reading order. The generated pages (half-title,
# title page, table of contents) are NOT here — the Reader export builds those from metadata + the chapter
# list, so they're never chapters. Body chapters (1..N) come from the writing/import flow, not the skeleton.
_SCAFFOLD_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("front_matter", "copyright"),
    ("front_matter", "dedication"),
    ("front_matter", "preface"),
    ("prologue", None),
    ("epilogue", None),
    ("back_matter", "afterword"),
    ("back_matter", "acknowledgments"),
    ("back_matter", "appendix"),
    ("back_matter", "glossary"),
    ("back_matter", "author_bio"),
)


@router.post("/{book_id}/manuscript/scaffold", response_model=ManuscriptScaffoldReport)
async def scaffold_production(book_id: uuid.UUID, session: SessionDep) -> ManuscriptScaffoldReport:
    """Create the standard production skeleton — front/back-matter + prologue/epilogue slots, as empty
    chapters ready to fill — in canonical reading order. Idempotent: a section that already exists (same
    kind + section_type) is skipped, so re-running never duplicates. Each slot's `position` is derived
    from the shared reading-order helper (kind + section_type), so the skeleton is correctly ordered the
    moment it's created; an empty slot stays out of exports until it has prose."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    existing = (await session.execute(select(Chapter).where(Chapter.book_id == book_id))).scalars().all()

    def _present(kind: str, section_type: str | None) -> bool:
        return any(c.kind == kind and (c.section_type or None) == section_type for c in existing)

    created: list[str] = []
    skipped: list[str] = []
    for kind, section_type in _SCAFFOLD_SECTIONS:
        label = (section_type or kind).replace("_", " ").title()
        if _present(kind, section_type):
            skipped.append(label)
            continue
        # position is derived on insert from kind + section_type (Chapter._chapter_default_position).
        session.add(
            Chapter(
                book_id=book_id,
                chapter_no=None,
                pov="",
                kind=kind,
                section_type=section_type,
                status=ChapterStatus.PLANNED,
            )
        )
        created.append(label)

    await session.commit()
    return ManuscriptScaffoldReport(created=created, skipped=skipped)
