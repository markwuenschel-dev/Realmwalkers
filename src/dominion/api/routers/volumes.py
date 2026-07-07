"""Volumes CRUD — the top grouping tier (Book → Volume → Part → Chapter).

A Volume groups Parts exactly as a Part groups Chapters. Ordering keys off `volume_no` (unique within a
book); labels are derived on the frontend. Deleting a Volume unassigns its Parts (sets `volume_id = NULL`)
rather than deleting them — the same non-cascading behavior as deleting a Part.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, Part, Volume
from dominion.shared.schemas import VolumeCreateIn, VolumeOut, VolumeUpdateIn

router = APIRouter(tags=["volumes"])


async def _require_book(session: AsyncSession, book_id: uuid.UUID) -> Book:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book


async def _require_volume(session: AsyncSession, volume_id: uuid.UUID) -> Volume:
    volume = await session.get(Volume, volume_id)
    if volume is None:
        raise HTTPException(status_code=404, detail="volume not found")
    return volume


async def _volume_no_taken(
    session: AsyncSession, book_id: uuid.UUID, volume_no: int, *, exclude: uuid.UUID | None = None
) -> bool:
    stmt = select(Volume.id).where(Volume.book_id == book_id, Volume.volume_no == volume_no)
    if exclude is not None:
        stmt = stmt.where(Volume.id != exclude)
    return (await session.execute(stmt)).first() is not None


@router.get("/books/{book_id}/volumes", response_model=list[VolumeOut])
async def list_volumes(book_id: uuid.UUID, session: SessionDep) -> list[Volume]:
    await _require_book(session, book_id)
    rows = (
        (await session.execute(select(Volume).where(Volume.book_id == book_id).order_by(Volume.volume_no)))
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/books/{book_id}/volumes", response_model=VolumeOut)
async def create_volume(book_id: uuid.UUID, body: VolumeCreateIn, session: SessionDep) -> Volume:
    await _require_book(session, book_id)
    if await _volume_no_taken(session, book_id, body.volume_no):
        raise HTTPException(status_code=409, detail=f"volume_no {body.volume_no} already exists in this book")
    volume = Volume(book_id=book_id, volume_no=body.volume_no, title=body.title, subtitle=body.subtitle)
    session.add(volume)
    await session.commit()
    return volume


@router.patch("/volumes/{volume_id}", response_model=VolumeOut)
async def update_volume(volume_id: uuid.UUID, body: VolumeUpdateIn, session: SessionDep) -> Volume:
    volume = await _require_volume(session, volume_id)
    if body.volume_no is not None and body.volume_no != volume.volume_no:
        if await _volume_no_taken(session, volume.book_id, body.volume_no, exclude=volume.id):
            raise HTTPException(status_code=409, detail=f"volume_no {body.volume_no} already exists in this book")
        volume.volume_no = body.volume_no
    if body.title is not None:
        volume.title = body.title
    if body.subtitle is not None:
        volume.subtitle = body.subtitle
    await session.commit()
    return volume


@router.delete("/volumes/{volume_id}", status_code=204)
async def delete_volume(volume_id: uuid.UUID, session: SessionDep) -> None:
    """Delete a Volume and unassign its Parts (volume_id -> NULL). Parts (and their chapters/prose) are
    never touched — removing the Volume ungroups the Parts, it does not delete them."""
    volume = await _require_volume(session, volume_id)
    parts = (await session.execute(select(Part).where(Part.volume_id == volume.id))).scalars().all()
    for p in parts:
        p.volume_id = None
    await session.delete(volume)
    await session.commit()
