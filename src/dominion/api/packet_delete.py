"""Shared hard-delete for chapter/scene packets and FK cleanup."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import (
    Beat,
    ChapterPacket,
    Critique,
    DraftAttempt,
    Job,
    Scene,
    ScenePacket,
)
from dominion.workers.draft_queue import purge_draft_jobs_for_scene_packet


async def _detach_scene_packet_refs(session: AsyncSession, scene_packet_id: uuid.UUID) -> None:
    """Null every nullable FK pointing at a scene packet before the row is removed."""
    for model in (Beat, Scene, Job, Critique, DraftAttempt):
        await session.execute(
            update(model).where(model.scene_packet_id == scene_packet_id).values(scene_packet_id=None)
        )


async def hard_delete_scene_packet(session: AsyncSession, scene_packet_id: uuid.UUID) -> tuple[uuid.UUID, int]:
    """Remove one ScenePacket and detach/delete dependents. Returns (id, jobs_purged)."""
    row = await session.get(ScenePacket, scene_packet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scene packet not found")
    jobs_purged = await purge_draft_jobs_for_scene_packet(session, scene_packet_id=scene_packet_id)
    await _detach_scene_packet_refs(session, scene_packet_id)
    await session.delete(row)
    return scene_packet_id, jobs_purged


async def hard_delete_scene_packets_for_chapter(
    session: AsyncSession, chapter_id: uuid.UUID, *, packet_ids: set[uuid.UUID] | None = None
) -> tuple[int, int]:
    """Delete scene packets for a chapter (all, or a subset). Returns (deleted, jobs_purged)."""
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    deleted = 0
    jobs_purged = 0
    for row in rows:
        if packet_ids is not None and row.id not in packet_ids:
            continue
        _, purged = await hard_delete_scene_packet(session, row.id)
        deleted += 1
        jobs_purged += purged
    return deleted, jobs_purged


async def hard_delete_chapter_packets(session: AsyncSession, chapter_id: uuid.UUID) -> tuple[int, int]:
    """Delete all chapter packets for a chapter and their scene packets. Returns (chapter_deleted, scene_deleted)."""
    scene_deleted, _ = await hard_delete_scene_packets_for_chapter(session, chapter_id)
    rows = (await session.execute(select(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id))).scalars().all()
    for row in rows:
        await session.delete(row)
    return len(rows), scene_deleted
