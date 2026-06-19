"""Read endpoints for the review inbox (DESIGN §9). These are real."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Critique, Scene
from dominion.shared.schemas import CritiqueOut, SceneDetail, SceneOut, SceneVersionOut

router = APIRouter(prefix="/scenes", tags=["scenes"])


@router.get("/pending", response_model=list[SceneOut])
async def pending(session: SessionDep) -> list[Scene]:
    rows = (
        await session.execute(
            select(Scene)
            .where(Scene.status == SceneStatus.PENDING_REVIEW)
            .order_by(Scene.created_at)
        )
    ).scalars().all()
    return list(rows)


@router.get("/{scene_id}", response_model=SceneDetail)
async def scene_detail(scene_id: uuid.UUID, session: SessionDep) -> SceneDetail:
    scene = (
        await session.execute(select(Scene).where(Scene.id == scene_id))
    ).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    crits = (
        await session.execute(select(Critique).where(Critique.scene_id == scene_id))
    ).scalars().all()
    detail = SceneDetail.model_validate(scene)
    detail.critiques = [CritiqueOut.model_validate(c) for c in crits]
    return detail


@router.get("/{scene_id}/versions", response_model=list[SceneVersionOut])
async def scene_versions(scene_id: uuid.UUID, session: SessionDep) -> list[Scene]:
    """Full lineage of a scene: every version sharing its (chapter, scene_no), oldest first."""
    scene = (
        await session.execute(select(Scene).where(Scene.id == scene_id))
    ).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    rows = (
        await session.execute(
            select(Scene)
            .where(Scene.chapter_id == scene.chapter_id, Scene.scene_no == scene.scene_no)
            .order_by(Scene.version)
        )
    ).scalars().all()
    return list(rows)
