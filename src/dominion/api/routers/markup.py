"""Scene markup: human margin notes (annotations) + tracked-change suggestions (DESIGN §9, §11).

Both are advisory — they never touch scene.status. Annotations are quote-anchored margin notes.
Suggestions propose replacing a quote with new text; they sit pending until you accept/reject, and the
Desk folds accepted ones into the prose when you approve the scene (so they reach canon through the
same human gate as any edit). Quote-by-substring anchoring matches the Desk's tokenize().
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Annotation, Scene, Suggestion
from dominion.shared.schemas import (
    AnnotationIn,
    AnnotationOut,
    SuggestionDecisionIn,
    SuggestionIn,
    SuggestionOut,
)

router = APIRouter(tags=["markup"])


async def _scene_or_404(session: SessionDep, scene_id: uuid.UUID) -> Scene:
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    return scene


# --- annotations ----------------------------------------------------------------------------------


@router.get("/scenes/{scene_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(scene_id: uuid.UUID, session: SessionDep) -> list[Annotation]:
    rows = (
        (
            await session.execute(
                select(Annotation).where(Annotation.scene_id == scene_id).order_by(Annotation.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/scenes/{scene_id}/annotations", response_model=AnnotationOut)
async def create_annotation(scene_id: uuid.UUID, body: AnnotationIn, session: SessionDep) -> Annotation:
    scene = await _scene_or_404(session, scene_id)
    ann = Annotation(
        scene_id=scene.id,
        version=scene.version,
        quote=body.quote,
        author=body.author,
        note=body.note,
    )
    session.add(ann)
    await session.commit()
    return ann


@router.delete("/annotations/{annotation_id}")
async def delete_annotation(annotation_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    ann = await session.get(Annotation, annotation_id)
    if ann is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    await session.delete(ann)
    await session.commit()
    return {"deleted": str(annotation_id)}


# --- suggestions ----------------------------------------------------------------------------------


@router.get("/scenes/{scene_id}/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(scene_id: uuid.UUID, session: SessionDep) -> list[Suggestion]:
    rows = (
        (
            await session.execute(
                select(Suggestion).where(Suggestion.scene_id == scene_id).order_by(Suggestion.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/scenes/{scene_id}/suggestions", response_model=SuggestionOut)
async def create_suggestion(scene_id: uuid.UUID, body: SuggestionIn, session: SessionDep) -> Suggestion:
    scene = await _scene_or_404(session, scene_id)
    sug = Suggestion(
        scene_id=scene.id,
        version=scene.version,
        quote=body.quote,
        new_text=body.new_text,
        author=body.author,
        why=body.why,
    )
    session.add(sug)
    await session.commit()
    return sug


@router.post("/suggestions/{suggestion_id}/decision", response_model=SuggestionOut)
async def decide_suggestion(suggestion_id: uuid.UUID, body: SuggestionDecisionIn, session: SessionDep) -> Suggestion:
    sug = await session.get(Suggestion, suggestion_id)
    if sug is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    sug.status = body.status
    await session.commit()
    return sug


@router.delete("/suggestions/{suggestion_id}")
async def delete_suggestion(suggestion_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    sug = await session.get(Suggestion, suggestion_id)
    if sug is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    await session.delete(sug)
    await session.commit()
    return {"deleted": str(suggestion_id)}
