"""Shared hard-delete for scene rows and their dependents (Inbox bulk delete, clear-draft)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import (
    Annotation,
    Approval,
    CharacterState,
    Critique,
    DraftAttempt,
    EditPair,
    Issue,
    Job,
    KnowledgeFact,
    RepairTask,
    Scene,
    Suggestion,
    Summary,
)
from dominion.workers.draft_queue import purge_draft_jobs_for_scene
from dominion.workers.scene_packet import staleness as packet_staleness


async def hard_delete_scene(session: AsyncSession, scene_id: uuid.UUID) -> tuple[uuid.UUID, int]:
    """Remove one scene and detach/delete dependents. Returns (scene_id, jobs_purged)."""
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    jobs_purged = await purge_draft_jobs_for_scene(session, chapter_id=scene.chapter_id, scene_no=scene.scene_no)

    for model in (Critique, Annotation, Suggestion, Approval, EditPair):
        await session.execute(delete(model).where(model.scene_id == scene_id))
    await session.execute(
        update(CharacterState).where(CharacterState.as_of_scene_id == scene_id).values(as_of_scene_id=None)
    )
    await session.execute(update(Summary).where(Summary.up_to_scene_id == scene_id).values(up_to_scene_id=None))
    await session.execute(update(Job).where(Job.target_scene_id == scene_id).values(target_scene_id=None))
    await session.execute(update(Issue).where(Issue.scene_id == scene_id).values(scene_id=None))
    await session.execute(update(RepairTask).where(RepairTask.scene_id == scene_id).values(scene_id=None))
    await session.execute(update(Scene).where(Scene.parent_scene_id == scene_id).values(parent_scene_id=None))
    await session.execute(update(DraftAttempt).where(DraftAttempt.scene_id == scene_id).values(scene_id=None))
    for col in (
        KnowledgeFact.source_scene_id,
        KnowledgeFact.known_by_reader_after_scene_id,
        KnowledgeFact.known_by_character_after_scene_id,
    ):
        await session.execute(update(KnowledgeFact).where(col == scene_id).values({col: None}))
    await session.execute(delete(Scene).where(Scene.id == scene_id))
    await packet_staleness.mark_stale_after_scene_delete(session, chapter_id=scene.chapter_id, scene_no=scene.scene_no)
    return scene_id, jobs_purged
