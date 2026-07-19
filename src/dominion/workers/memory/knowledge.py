"""Knowledge-fact ledger population (scene-packet knowledge layer).

When a scene is approved, the facts its ScenePacket said the reader must learn become known-to-reader
as of that scene. This records them as durable KnowledgeFact rows (separate from the lossy rolling
summaries), so later tooling can answer "what did the reader know before scene N?" deterministically.

Best-effort + idempotent: re-approving a scene re-upserts the same facts without duplicating, and a
failure here never blocks the approval (the caller treats it as advisory).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.chapter_order import chapter_position
from dominion.shared.enums import KnowledgeStatus
from dominion.shared.models import Chapter, KnowledgeFact, Scene, ScenePacket


def _reader_facts(body: dict[str, Any]) -> list[str]:
    learned = body.get("learned_during_scene") or {}
    out: list[str] = []
    for fact in learned.get("reader_must_learn") or []:
        text = str(fact).strip()
        if text:
            out.append(text)
    return out


async def _story_order(session: AsyncSession, scene: Scene) -> tuple[int, int]:
    """Global reading-order key for a scene: (chapter reading position, scene_no). Lets the reader-
    knowledge marker stay at the EARLIEST revealing scene rather than the last writer (KNOW-MONO)."""
    ch = await session.get(Chapter, scene.chapter_id)
    pos = chapter_position(ch.kind, ch.chapter_no) if ch is not None else 0
    return (pos, scene.scene_no)


async def record_scene_reveals(session: AsyncSession, *, scene_id: uuid.UUID) -> int:
    """Upsert KnowledgeFact rows for the reveals of the scene's ScenePacket. Returns the count
    recorded. Idempotent per (book, fact): updates the reveal scene rather than duplicating."""
    scene = await session.get(Scene, scene_id)
    if scene is None or scene.scene_packet_id is None:
        return 0
    sp = await session.get(ScenePacket, scene.scene_packet_id)
    if sp is None or not isinstance(sp.body, dict):
        return 0
    facts = _reader_facts(sp.body)
    if not facts:
        return 0
    book_id = (
        await session.execute(select(Chapter.book_id).where(Chapter.id == scene.chapter_id))
    ).scalar_one_or_none()
    if book_id is None:
        return 0

    existing = {
        row.fact: row
        for row in (
            await session.execute(
                select(KnowledgeFact).where(KnowledgeFact.book_id == book_id, KnowledgeFact.fact.in_(facts))
            )
        ).scalars()
    }
    new_order = await _story_order(session, scene)
    for fact in facts:
        row = existing.get(fact)
        if row is None:
            session.add(
                KnowledgeFact(
                    book_id=book_id,
                    fact=fact,
                    source_scene_id=scene.id,
                    known_by_reader_after_scene_id=scene.id,
                    status=KnowledgeStatus.REVEALED,
                    metadata_json={"scene_no": scene.scene_no},
                )
            )
        else:
            # KNOW-MONO: the marker is "known after scene X" and must track the EARLIEST reveal. Advance it
            # only when THIS scene reveals the fact earlier in story order — a later recap must not push
            # "known after" forward (re-approving a genuinely earlier scene correctly moves it back).
            marker_id = row.known_by_reader_after_scene_id
            marker = await session.get(Scene, marker_id) if marker_id is not None else None
            if marker is None or new_order < await _story_order(session, marker):
                row.known_by_reader_after_scene_id = scene.id
            row.status = KnowledgeStatus.REVEALED
            if row.source_scene_id is None:
                row.source_scene_id = scene.id
    return len(facts)
