"""World threads: curated narrative arcs tracked across scenes (DESIGN §7, §9).

The mock Ledger invented these (Soren ⇄ Lyra, Ember Affinity, …). Here they're real rows the writer
curates from the Desk: a Thread is a named arc with a state; its beats pin a label (+ optional flag)
to a scene number.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, Thread, ThreadBeat
from dominion.shared.schemas import (
    ThreadBeatIn,
    ThreadBeatOut,
    ThreadIn,
    ThreadOut,
    ThreadUpdateIn,
)

router = APIRouter(tags=["threads"])


async def _render(session: SessionDep, thread: Thread) -> ThreadOut:
    beats = (
        (
            await session.execute(
                select(ThreadBeat).where(ThreadBeat.thread_id == thread.id).order_by(ThreadBeat.scene_no)
            )
        )
        .scalars()
        .all()
    )
    return ThreadOut(
        id=thread.id,
        name=thread.name,
        kind=thread.kind,
        state=thread.state,
        note=thread.note,
        beats=[ThreadBeatOut.model_validate(b) for b in beats],
    )


@router.get("/books/{book_id}/threads", response_model=list[ThreadOut])
async def list_threads(book_id: uuid.UUID, session: SessionDep) -> list[ThreadOut]:
    threads = (
        (await session.execute(select(Thread).where(Thread.book_id == book_id).order_by(Thread.created_at)))
        .scalars()
        .all()
    )
    if not threads:
        return []
    beats = (
        (
            await session.execute(
                select(ThreadBeat)
                .where(ThreadBeat.thread_id.in_([t.id for t in threads]))
                .order_by(ThreadBeat.scene_no)
            )
        )
        .scalars()
        .all()
    )
    by_thread: dict[uuid.UUID, list[ThreadBeat]] = {}
    for b in beats:
        by_thread.setdefault(b.thread_id, []).append(b)
    return [
        ThreadOut(
            id=t.id,
            name=t.name,
            kind=t.kind,
            state=t.state,
            note=t.note,
            beats=[ThreadBeatOut.model_validate(b) for b in by_thread.get(t.id, [])],
        )
        for t in threads
    ]


@router.post("/books/{book_id}/threads", response_model=ThreadOut)
async def create_thread(book_id: uuid.UUID, body: ThreadIn, session: SessionDep) -> ThreadOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    thread = Thread(book_id=book_id, name=body.name, kind=body.kind, state=body.state, note=body.note)
    session.add(thread)
    await session.commit()
    return await _render(session, thread)


@router.put("/threads/{thread_id}", response_model=ThreadOut)
async def update_thread(thread_id: uuid.UUID, body: ThreadUpdateIn, session: SessionDep) -> ThreadOut:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(thread, key, value)
    await session.commit()
    return await _render(session, thread)


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    # No DB cascade configured — clear the child beats first (and flush, so the parent delete below
    # doesn't trip the FK constraint via the unit-of-work's flush ordering).
    await session.execute(delete(ThreadBeat).where(ThreadBeat.thread_id == thread_id))
    await session.flush()  # child beats first, so the parent delete doesn't trip the FK
    await session.delete(thread)
    await session.commit()
    return {"deleted": str(thread_id)}


@router.post("/threads/{thread_id}/beats", response_model=ThreadOut)
async def add_thread_beat(thread_id: uuid.UUID, body: ThreadBeatIn, session: SessionDep) -> ThreadOut:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread not found")
    session.add(
        ThreadBeat(
            thread_id=thread_id,
            scene_no=body.scene_no,
            label=body.label,
            flag=body.flag,
        )
    )
    await session.commit()
    return await _render(session, thread)
