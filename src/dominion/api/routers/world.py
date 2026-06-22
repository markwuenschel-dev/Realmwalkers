"""World ledger + in-prose entity cards (DESIGN §5, §7).

The Desk's Ledger screen and the hover-cards over names in the prose read here. Hard numbers come
from the Oracle's CharacterState; prose/lore bodies from the canon (CanonEntity). Both are real
story state — nothing here is a fixture.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, CanonEntity, Chapter, CharacterState
from dominion.shared.schemas import CanonEntityOut, CharacterStateOut

router = APIRouter(tags=["world"])


@router.get("/books/{book_id}/characters", response_model=list[CharacterStateOut])
async def list_characters(book_id: uuid.UUID, session: SessionDep) -> list[CharacterStateOut]:
    """Every character the Oracle is tracking, with stats + (if present) the canon body and POV flag."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    states = (await session.execute(
        select(CharacterState)
        .where(CharacterState.book_id == book_id)
        .order_by(CharacterState.character)
    )).scalars().all()
    pov_set = set((await session.execute(
        select(Chapter.pov).where(Chapter.book_id == book_id)
    )).scalars().all())
    canon = (await session.execute(
        select(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.kind == "character")
    )).scalars().all()
    bodies = {(c.name or "").lower(): c.body for c in canon}

    return [
        CharacterStateOut(
            character=st.character,
            stats=st.stats_json or {},
            provisional=st.provisional,
            is_pov=st.character in pov_set,
            body=bodies.get(st.character.lower()),
        )
        for st in states
    ]


@router.get("/books/{book_id}/canon", response_model=list[CanonEntityOut])
async def list_canon(
    book_id: uuid.UUID, session: SessionDep, kind: str | None = None
) -> list[CanonEntity]:
    """The story bible: characters, locations, factions, items, lore. Filter by `kind` if given."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    stmt = select(CanonEntity).where(CanonEntity.book_id == book_id)
    if kind:
        stmt = stmt.where(CanonEntity.kind == kind)
    rows = (await session.execute(stmt.order_by(CanonEntity.kind, CanonEntity.name))).scalars().all()
    return list(rows)
