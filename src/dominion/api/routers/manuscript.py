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
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.db import SessionFactory
from dominion.shared.enums import ChapterStatus, SceneStatus
from dominion.shared.models import Book, Chapter, Scene
from dominion.shared.schemas import (
    ManuscriptImportIn,
    ManuscriptImportReport,
    ManuscriptParseIn,
    ParsedChapterOut,
    ParsedManuscriptOut,
    ParsedSceneOut,
)
from dominion.workers.memory import summaries
from dominion.workers.memory.manuscript_split import split_files

log = structlog.get_logger()
router = APIRouter(prefix="/books", tags=["manuscript"])


async def _fold_imported(scene_id: uuid.UUID) -> None:
    """Best-effort background fold of a directly-approved imported scene into the summaries (own
    session; mirrors chapters._fold_summary). PENDING_REVIEW imports fold later, on inbox approval."""
    try:
        async with SessionFactory() as session:
            await summaries.refresh_on_approval(session, scene_id=scene_id)
    except Exception as exc:  # noqa: BLE001 — the fold is advisory, never part of the import contract
        log.warning("manuscript_import.summary_fold_failed", scene=str(scene_id), error=str(exc))


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

    existing = set(
        (await session.execute(select(Chapter.chapter_no).where(Chapter.book_id == book_id))).scalars().all()
    )

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
    carries overwrite=True. Scenes land as `imported`-sourced, PENDING_REVIEW by default (or APPROVED
    when approve_directly), superseding any prior version at their scene_no. No LLM title call, and no
    summary fold on the review path — folds happen when each scene is approved in the inbox."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    if not body.chapters:
        raise HTTPException(status_code=400, detail="no chapters to import")

    status = SceneStatus.APPROVED if body.approve_directly else SceneStatus.PENDING_REVIEW
    created = updated = imported = 0
    skipped: list[int] = []
    warnings: list[str] = []
    fold_ids: list[uuid.UUID] = []

    for ch in body.chapters:
        existing = (
            await session.execute(
                select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_no == ch.chapter_no)
            )
        ).scalar_one_or_none()
        if existing is not None and not ch.overwrite:
            skipped.append(ch.chapter_no)
            continue
        if existing is None:
            chapter = Chapter(book_id=book_id, chapter_no=ch.chapter_no, pov=ch.pov or "", status=ChapterStatus.PLANNED)
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
            updated += 1

        for sc in ch.scenes:
            prose = sc.prose.strip()
            if not prose:
                warnings.append(f"Chapter {ch.chapter_no} scene {sc.scene_no}: empty prose — skipped.")
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
            if body.approve_directly:
                await session.flush()  # need scene.id for the background fold
                fold_ids.append(scene.id)

    await session.commit()
    for sid in fold_ids:
        background.add_task(_fold_imported, sid)

    return ManuscriptImportReport(
        chapters_created=created,
        chapters_updated=updated,
        scenes_imported=imported,
        skipped_conflicts=skipped,
        warnings=warnings,
    )
