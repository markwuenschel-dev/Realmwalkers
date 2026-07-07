"""Parts CRUD + chapter assignment — the Book → Part → Chapter grouping level.

A Part is a durable, optional grouping of chapters (the mid-tier structural spine level between Book and
Chapter). Ordering keys off `part_no`, which is unique within a book; reader-facing labels are derived
from it on the frontend, never stored. Chapters keep their global `chapter_no` — a Part groups, it does
not renumber. Deleting a Part unassigns its chapters (sets `part_id = NULL`) rather than deleting prose.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, Chapter, Part, Volume
from dominion.shared.schemas import (
    ChapterPartAssignIn,
    PartCreateIn,
    PartOut,
    PartUpdateIn,
    PartVolumeAssignIn,
)

router = APIRouter(tags=["parts"])


async def _require_book(session: AsyncSession, book_id: uuid.UUID) -> Book:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book


async def _require_part(session: AsyncSession, part_id: uuid.UUID) -> Part:
    part = await session.get(Part, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="part not found")
    return part


async def _part_no_taken(
    session: AsyncSession, book_id: uuid.UUID, part_no: int, *, exclude: uuid.UUID | None = None
) -> bool:
    """A part_no is unique within a book. `exclude` skips the row being updated so a no-op rename to its
    own number doesn't self-collide."""
    stmt = select(Part.id).where(Part.book_id == book_id, Part.part_no == part_no)
    if exclude is not None:
        stmt = stmt.where(Part.id != exclude)
    return (await session.execute(stmt)).first() is not None


@router.get("/books/{book_id}/parts", response_model=list[PartOut])
async def list_parts(book_id: uuid.UUID, session: SessionDep) -> list[Part]:
    await _require_book(session, book_id)
    rows = (await session.execute(select(Part).where(Part.book_id == book_id).order_by(Part.part_no))).scalars().all()
    return list(rows)


@router.post("/books/{book_id}/parts", response_model=PartOut)
async def create_part(book_id: uuid.UUID, body: PartCreateIn, session: SessionDep) -> Part:
    await _require_book(session, book_id)
    if await _part_no_taken(session, book_id, body.part_no):
        raise HTTPException(status_code=409, detail=f"part_no {body.part_no} already exists in this book")
    part = Part(
        book_id=book_id,
        part_no=body.part_no,
        title=body.title,
        subtitle=body.subtitle,
        kind=body.kind,
    )
    session.add(part)
    await session.commit()
    return part


@router.patch("/parts/{part_id}", response_model=PartOut)
async def update_part(part_id: uuid.UUID, body: PartUpdateIn, session: SessionDep) -> Part:
    part = await _require_part(session, part_id)
    if body.part_no is not None and body.part_no != part.part_no:
        if await _part_no_taken(session, part.book_id, body.part_no, exclude=part.id):
            raise HTTPException(status_code=409, detail=f"part_no {body.part_no} already exists in this book")
        part.part_no = body.part_no
    if body.title is not None:
        part.title = body.title
    if body.subtitle is not None:
        part.subtitle = body.subtitle
    if body.kind is not None:
        part.kind = body.kind
    await session.commit()
    return part


@router.put("/parts/{part_id}/volume", response_model=PartOut)
async def assign_part_volume(part_id: uuid.UUID, body: PartVolumeAssignIn, session: SessionDep) -> Part:
    """Assign a Part to a Volume, or unassign it (`volume_id: null`). The Volume must belong to the same
    book as the Part."""
    part = await _require_part(session, part_id)
    if body.volume_id is None:
        part.volume_id = None
        await session.commit()
        return part
    volume = await session.get(Volume, body.volume_id)
    if volume is None:
        raise HTTPException(status_code=404, detail="volume not found")
    if volume.book_id != part.book_id:
        raise HTTPException(status_code=400, detail="volume and part belong to different books")
    part.volume_id = volume.id
    await session.commit()
    return part


@router.delete("/parts/{part_id}", status_code=204)
async def delete_part(part_id: uuid.UUID, session: SessionDep) -> None:
    """Delete a Part and unassign its chapters (part_id -> NULL). Prose is never touched: a Part is a
    grouping layer, so removing it ungroups the chapters, it does not delete them."""
    part = await _require_part(session, part_id)
    chapters = (await session.execute(select(Chapter).where(Chapter.part_id == part.id))).scalars().all()
    for ch in chapters:
        ch.part_id = None
    await session.delete(part)
    await session.commit()


@router.put("/chapters/{chapter_id}/part", response_model=PartOut | None)
async def assign_chapter_part(chapter_id: uuid.UUID, body: ChapterPartAssignIn, session: SessionDep) -> Part | None:
    """Assign a chapter to a Part, or unassign it (`part_id: null`). The Part must belong to the same
    book as the chapter. Returns the assigned Part, or null when unassigned."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")

    if body.part_id is None:
        chapter.part_id = None
        await session.commit()
        return None

    part = await _require_part(session, body.part_id)
    if part.book_id != chapter.book_id:
        raise HTTPException(status_code=400, detail="part and chapter belong to different books")
    chapter.part_id = part.id
    await session.commit()
    return part
