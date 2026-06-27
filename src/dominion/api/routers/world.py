"""World ledger + in-prose entity cards (DESIGN §5, §7).

The Desk's Ledger screen and the hover-cards over names in the prose read here. Hard numbers come
from the Oracle's CharacterState; prose/lore bodies from the canon (CanonEntity). Both are real
story state — nothing here is a fixture.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, CanonEntity, Chapter, CharacterState, KnowledgeFact
from dominion.shared.schemas import (
    CanonEntityIn,
    CanonEntityOut,
    CanonEntityUpdateIn,
    CanonIngestOut,
    CharacterStateIn,
    CharacterStateOut,
    KnowledgeFactOut,
)
from dominion.workers.memory import canon_rag
from dominion.workers.memory.embedding import embed

router = APIRouter(tags=["world"])

# …/src/dominion/api/routers/world.py -> repo root (mirrors docs.py); shared canon docs live under series/.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


async def _require_book(book_id: uuid.UUID, session: SessionDep) -> Book:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book


async def _character_out(
    book_id: uuid.UUID, state: CharacterState, session: SessionDep
) -> CharacterStateOut:
    """One CharacterState -> DTO, resolving POV flag (from chapters) + canon body (kind='character')."""
    is_pov = (await session.execute(
        select(Chapter.id)
        .where(Chapter.book_id == book_id, Chapter.pov == state.character)
        .limit(1)
    )).first() is not None
    body = (await session.execute(
        select(CanonEntity.body).where(
            CanonEntity.book_id == book_id,
            CanonEntity.kind == "character",
            func.lower(CanonEntity.name) == state.character.lower(),
        ).limit(1)
    )).scalar_one_or_none()
    return CharacterStateOut(
        character=state.character,
        stats=state.stats_json or {},
        provisional=state.provisional,
        is_pov=is_pov,
        body=body,
    )


@router.get("/books/{book_id}/characters", response_model=list[CharacterStateOut])
async def list_characters(book_id: uuid.UUID, session: SessionDep) -> list[CharacterStateOut]:
    """Every character the Oracle is tracking, with stats + (if present) the canon body and POV flag."""
    await _require_book(book_id, session)

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


@router.put("/books/{book_id}/characters/{character}", response_model=CharacterStateOut)
async def upsert_character(
    book_id: uuid.UUID, character: str, body: CharacterStateIn, session: SessionDep
) -> CharacterStateOut:
    """Seed or replace a character's Oracle stats (absolute values) + optional canon description.

    This is how you establish a baseline BEFORE writing — the continuity reviewer checks prose against
    these hard numbers, and the drafter/RAG see the description. Stats are set wholesale (not deltas);
    beat-declared deltas still advance them on approval as usual (workers/memory/ledger.py)."""
    await _require_book(book_id, session)
    name = character.strip()
    if not name:
        raise HTTPException(status_code=400, detail="character name required")

    row = (await session.execute(
        select(CharacterState).where(
            CharacterState.book_id == book_id, CharacterState.character == name
        )
    )).scalar_one_or_none()
    if row is None:
        row = CharacterState(book_id=book_id, character=name, stats_json={})
        session.add(row)
    row.stats_json = body.stats or {}
    row.provisional = False

    # An optional description lives as a kind='character' canon entity (what list_characters reads).
    if body.body is not None:
        canon = (await session.execute(
            select(CanonEntity).where(
                CanonEntity.book_id == book_id,
                CanonEntity.kind == "character",
                func.lower(CanonEntity.name) == name.lower(),
            ).limit(1)
        )).scalar_one_or_none()
        text = body.body.strip() or None
        if canon is None:
            canon = CanonEntity(book_id=book_id, kind="character", name=name)
            session.add(canon)
        canon.body = text
        canon.embedding = embed(text) if text else None

    await session.commit()
    return await _character_out(book_id, row, session)


@router.delete("/books/{book_id}/characters/{character}")
async def delete_character(
    book_id: uuid.UUID, character: str, session: SessionDep
) -> dict[str, str]:
    """Drop a character's tracked stat row (the kind='character' canon description is left in place)."""
    row = (await session.execute(
        select(CharacterState).where(
            CharacterState.book_id == book_id, CharacterState.character == character
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="character not found")
    await session.delete(row)
    await session.commit()
    return {"deleted": character}


@router.get("/books/{book_id}/knowledge", response_model=list[KnowledgeFactOut])
async def list_knowledge(book_id: uuid.UUID, session: SessionDep) -> list[KnowledgeFact]:
    """The knowledge ledger: discrete story facts + who knows them when (scene-packet knowledge layer).
    Populated from approved scenes' ScenePacket reveals."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    rows = (await session.execute(
        select(KnowledgeFact)
        .where(KnowledgeFact.book_id == book_id)
        .order_by(KnowledgeFact.created_at)
    )).scalars().all()
    return list(rows)


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


@router.post("/books/{book_id}/canon", response_model=CanonEntityOut)
async def create_canon(
    book_id: uuid.UUID, body: CanonEntityIn, session: SessionDep
) -> CanonEntity:
    """Add a canon entity (location/faction/item/lore/character/…). Embedded on write so it's
    immediately retrievable by the drafter/planner RAG (DESIGN §7)."""
    await _require_book(book_id, session)
    text = (body.body or "").strip() or None
    entity = CanonEntity(
        book_id=book_id,
        kind=(body.kind or "").strip() or None,
        name=(body.name or "").strip() or None,
        body=text,
        embedding=embed(text) if text else None,
    )
    session.add(entity)
    await session.commit()
    return entity


@router.put("/canon/{canon_id}", response_model=CanonEntityOut)
async def update_canon(
    canon_id: uuid.UUID, body: CanonEntityUpdateIn, session: SessionDep
) -> CanonEntity:
    """Edit a canon entity. Only provided fields change; a body change re-embeds it for retrieval."""
    entity = await session.get(CanonEntity, canon_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="canon entity not found")
    data = body.model_dump(exclude_unset=True)
    if "kind" in data:
        entity.kind = (data["kind"] or "").strip() or None
    if "name" in data:
        entity.name = (data["name"] or "").strip() or None
    if "body" in data:
        text = (data["body"] or "").strip() or None
        entity.body = text
        entity.embedding = embed(text) if text else None
    await session.commit()
    return entity


@router.delete("/canon/{canon_id}")
async def delete_canon(canon_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    entity = await session.get(CanonEntity, canon_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="canon entity not found")
    await session.delete(entity)
    await session.commit()
    return {"deleted": str(canon_id)}


@router.post("/books/{book_id}/canon/ingest", response_model=CanonIngestOut)
async def ingest_canon(book_id: uuid.UUID, session: SessionDep) -> CanonIngestOut:
    """Rebuild the retrieval index from the on-disk canon docs (series/canon) — the bridge from the
    read-only Canon tab into the RAG the drafter/planner actually query. Replaces the kind='passage'
    rows; hand-authored entities (character/location/…) are untouched."""
    await _require_book(book_id, session)
    n = await canon_rag.ingest_path(session, book_id=book_id, root=_PROJECT_ROOT / "series" / "canon")
    await session.commit()
    return CanonIngestOut(indexed=n)
