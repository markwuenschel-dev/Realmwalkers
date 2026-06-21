"""Margin notes anchored to a quote in a scene version (Notes tab + inline `anno` markers).

New persistent concept (DESIGN §3 proposed); the table is created by `scripts/init_db.py`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Annotation, Scene
from dominion.shared.schemas import AnnotationIn, AnnotationOut

router = APIRouter(tags=["annotations"])


@router.get("/scenes/{scene_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(scene_id: uuid.UUID, session: SessionDep) -> list[Annotation]:
    rows = (await session.execute(
        select(Annotation).where(Annotation.scene_id == scene_id).order_by(Annotation.created_at)
    )).scalars().all()
    return list(rows)


@router.post("/scenes/{scene_id}/annotations", response_model=AnnotationOut)
async def create_annotation(
    scene_id: uuid.UUID, body: AnnotationIn, session: SessionDep
) -> Annotation:
    if await session.get(Scene, scene_id) is None:
        raise HTTPException(status_code=404, detail="scene not found")
    ann = Annotation(
        scene_id=scene_id, version=body.version, quote=body.quote,
        author=body.author, note=body.note,
    )
    session.add(ann)
    await session.flush()
    return ann


@router.delete("/scenes/{scene_id}/annotations/{annotation_id}")
async def delete_annotation(
    scene_id: uuid.UUID, annotation_id: uuid.UUID, session: SessionDep
) -> dict[str, str]:
    ann = await session.get(Annotation, annotation_id)
    if ann is None or ann.scene_id != scene_id:
        raise HTTPException(status_code=404, detail="annotation not found for this scene")
    await session.delete(ann)
    return {"deleted": str(annotation_id)}
