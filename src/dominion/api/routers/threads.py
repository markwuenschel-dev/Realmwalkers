"""Author-curated plot/relationship threads (Ledger 'Threads', DESIGN §3 proposed).

Read + basic write only — threads are authored/curated, never auto-derived. New persistent concept:
the table is created by `scripts/init_db.py` (create_all); rerun it after pulling this.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Thread
from dominion.shared.schemas import ThreadIn, ThreadOut, ThreadUpdateIn

router = APIRouter(tags=["threads"])


@router.get("/books/{book_id}/threads", response_model=list[ThreadOut])
async def list_threads(book_id: uuid.UUID, session: SessionDep) -> list[Thread]:
    rows = (await session.execute(
        select(Thread).where(Thread.book_id == book_id).order_by(Thread.created_at)
    )).scalars().all()
    return list(rows)


@router.post("/books/{book_id}/threads", response_model=ThreadOut)
async def create_thread(book_id: uuid.UUID, body: ThreadIn, session: SessionDep) -> Thread:
    thread = Thread(
        book_id=book_id, name=body.name, kind=body.kind,
        state=body.state, note=body.note, beats=body.beats,
    )
    session.add(thread)
    await session.flush()
    return thread


@router.put("/threads/{thread_id}", response_model=ThreadOut)
async def update_thread(thread_id: uuid.UUID, body: ThreadUpdateIn, session: SessionDep) -> Thread:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(thread, key, value)
    await session.flush()
    return thread
