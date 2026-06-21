"""Track-changes suggestions on a scene version (Changes tab + inline `sugg` markers).

New persistent concept (DESIGN §3 proposed); the table is created by `scripts/init_db.py`. A
suggestion proposes replacing `quote` with `new_text`; accepting/rejecting only records the verdict —
applying an accepted edit to the prose stays a separate, explicit author action (hand-edit on decide).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.enums import SuggestionStatus
from dominion.shared.models import Scene, Suggestion
from dominion.shared.schemas import SuggestionDecisionIn, SuggestionIn, SuggestionOut

router = APIRouter(tags=["suggestions"])


@router.get("/scenes/{scene_id}/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(scene_id: uuid.UUID, session: SessionDep) -> list[Suggestion]:
    rows = (await session.execute(
        select(Suggestion).where(Suggestion.scene_id == scene_id).order_by(Suggestion.created_at)
    )).scalars().all()
    return list(rows)


@router.post("/scenes/{scene_id}/suggestions", response_model=SuggestionOut)
async def create_suggestion(
    scene_id: uuid.UUID, body: SuggestionIn, session: SessionDep
) -> Suggestion:
    if await session.get(Scene, scene_id) is None:
        raise HTTPException(status_code=404, detail="scene not found")
    sugg = Suggestion(
        scene_id=scene_id, version=body.version, quote=body.quote,
        new_text=body.new_text, author=body.author, why=body.why,
        status=SuggestionStatus.PENDING,
    )
    session.add(sugg)
    await session.flush()
    return sugg


@router.post("/suggestions/{suggestion_id}/decision", response_model=SuggestionOut)
async def decide_suggestion(
    suggestion_id: uuid.UUID, body: SuggestionDecisionIn, session: SessionDep
) -> Suggestion:
    sugg = await session.get(Suggestion, suggestion_id)
    if sugg is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    status = body.status.strip().lower()
    if status not in (SuggestionStatus.ACCEPTED, SuggestionStatus.REJECTED):
        raise HTTPException(status_code=400, detail="status must be 'accepted' or 'rejected'")
    sugg.status = status
    await session.flush()
    return sugg
