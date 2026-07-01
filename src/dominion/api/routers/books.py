"""Books + the assembled manuscript (DESIGN §9, §13).

Creating/listing books seeds the planning flow; the manuscript endpoint assembles the approved prose
in reading order (latest approved version of each scene) for a continuous read of the book so far.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.api.scene_delete import hard_delete_scene
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, Scene
from dominion.shared.schemas import (
    BookIn,
    BookOut,
    ClearDraftScenesOut,
    ManuscriptChapter,
    ManuscriptOut,
    ManuscriptScene,
)

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=list[BookOut])
async def list_books(session: SessionDep) -> list[Book]:
    rows = (await session.execute(select(Book).order_by(Book.created_at))).scalars().all()
    return list(rows)


@router.post("", response_model=BookOut)
async def create_book(body: BookIn, session: SessionDep) -> Book:
    book = Book(title=body.title, premise=body.premise)
    session.add(book)
    await session.commit()
    return book


@router.get("/{book_id}/manuscript", response_model=ManuscriptOut)
async def manuscript(book_id: uuid.UUID, session: SessionDep) -> ManuscriptOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapters = (
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )

    # Every approved scene in the book in ONE query (was one query per chapter — an N+1 on the widest
    # table). Ordered so the first row seen per (chapter_id, scene_no) is the latest version.
    scene_rows = (
        (
            await session.execute(
                select(Scene)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(Chapter.book_id == book_id, Scene.status == SceneStatus.APPROVED)
                .order_by(Scene.chapter_id, Scene.scene_no, Scene.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_by_chapter: dict[uuid.UUID, dict[int, Scene]] = {}
    for sc in scene_rows:
        latest_by_chapter.setdefault(sc.chapter_id, {}).setdefault(sc.scene_no, sc)

    out_chapters: list[ManuscriptChapter] = []
    for chapter in chapters:
        latest = latest_by_chapter.get(chapter.id, {})
        if not latest:
            continue
        out_chapters.append(
            ManuscriptChapter(
                chapter_no=chapter.chapter_no,
                title=chapter.title,
                pov=chapter.pov,
                kind=chapter.kind,
                epigraph=chapter.epigraph,
                scenes=[ManuscriptScene(scene_no=no, prose=latest[no].prose) for no in sorted(latest)],
            )
        )

    return ManuscriptOut(book_id=book_id, title=book.title, chapters=out_chapters)


@router.post("/{book_id}/scenes/clear-draft", response_model=ClearDraftScenesOut)
async def clear_draft_scenes(
    book_id: uuid.UUID,
    session: SessionDep,
    chapter_id: uuid.UUID | None = None,
) -> ClearDraftScenesOut:
    """Delete all non-approved scenes for a book (optional chapter scope). Approved prose is kept."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapter_ids = list((await session.execute(select(Chapter.id).where(Chapter.book_id == book_id))).scalars().all())
    if chapter_id is not None:
        if chapter_id not in chapter_ids:
            raise HTTPException(status_code=404, detail="chapter not found in book")
        chapter_ids = [chapter_id]

    rows = (
        (
            await session.execute(
                select(Scene.id).where(
                    Scene.chapter_id.in_(chapter_ids),
                    Scene.status != SceneStatus.APPROVED,
                )
            )
        )
        .scalars()
        .all()
    )
    scene_ids = list(rows)
    total_jobs = 0
    for sid in scene_ids:
        _, jobs_purged = await hard_delete_scene(session, sid)
        total_jobs += jobs_purged
    await session.commit()
    return ClearDraftScenesOut(purged=len(scene_ids), jobs_purged=total_jobs)
