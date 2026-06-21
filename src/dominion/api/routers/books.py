"""Books + the assembled manuscript (DESIGN §9, §13).

Creating/listing books seeds the planning flow; the manuscript endpoint assembles the approved prose
in reading order (latest approved version of each scene) for a continuous read of the book so far.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, CanonEntity, Chapter, CharacterState, Scene
from dominion.shared.schemas import (
    BookIn,
    BookOut,
    CanonOut,
    CharacterOut,
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
    await session.flush()
    return book


@router.get("/{book_id}/manuscript", response_model=ManuscriptOut)
async def manuscript(book_id: uuid.UUID, session: SessionDep) -> ManuscriptOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapters = (await session.execute(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)
    )).scalars().all()

    out_chapters: list[ManuscriptChapter] = []
    for chapter in chapters:
        # Approved scenes, newest version first so the first row seen per scene_no is the latest.
        scenes = (await session.execute(
            select(Scene)
            .where(Scene.chapter_id == chapter.id, Scene.status == SceneStatus.APPROVED)
            .order_by(Scene.scene_no, Scene.version.desc())
        )).scalars().all()
        latest: dict[int, Scene] = {}
        for sc in scenes:
            latest.setdefault(sc.scene_no, sc)
        if not latest:
            continue
        out_chapters.append(ManuscriptChapter(
            chapter_no=chapter.chapter_no,
            title=chapter.title,
            pov=chapter.pov,
            scenes=[
                ManuscriptScene(scene_no=no, title=latest[no].title, prose=latest[no].prose)
                for no in sorted(latest)
            ],
        ))

    return ManuscriptOut(book_id=book_id, title=book.title, chapters=out_chapters)


@router.get("/{book_id}/characters", response_model=list[CharacterOut])
async def characters(book_id: uuid.UUID, session: SessionDep) -> list[CharacterOut]:
    """Each character's latest hard state from the Oracle ledger (powers entity cards + Ledger)."""
    rows = (await session.execute(
        select(CharacterState)
        .where(CharacterState.book_id == book_id)
        .order_by(CharacterState.character, CharacterState.id.desc())
    )).scalars().all()
    latest: dict[str, CharacterState] = {}
    for cs in rows:                       # newest id per character wins (first seen)
        latest.setdefault(cs.character, cs)
    out: list[CharacterOut] = []
    for name in sorted(latest):
        stats = dict(latest[name].stats_json or {})
        role = stats.pop("role", None)    # role, if recorded, is metadata not a stat row
        out.append(CharacterOut(character=name, role=role, stats=stats))
    return out


@router.get("/{book_id}/canon", response_model=list[CanonOut])
async def canon(
    book_id: uuid.UUID, session: SessionDep, kind: str | None = None
) -> list[CanonEntity]:
    """Canon entities for the book, optionally filtered by kind (location|item|faction|lore|…)."""
    stmt = select(CanonEntity).where(CanonEntity.book_id == book_id)
    if kind:
        stmt = stmt.where(CanonEntity.kind == kind)
    rows = (await session.execute(stmt.order_by(CanonEntity.name))).scalars().all()
    return list(rows)
