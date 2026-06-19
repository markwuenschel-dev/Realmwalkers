"""Beat editing (gate 1). Beats are a review surface: you edit a proposed beat before approving it
(DESIGN §4, OPEN-2). Only fields you supply are changed; the rest stay as proposed."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from dominion.api.deps import SessionDep
from dominion.shared.models import Beat
from dominion.shared.schemas import BeatOut, BeatUpdateIn

router = APIRouter(prefix="/beats", tags=["beats"])


@router.put("/{beat_id}", response_model=BeatOut)
async def update_beat(beat_id: uuid.UUID, body: BeatUpdateIn, session: SessionDep) -> Beat:
    beat = await session.get(Beat, beat_id)
    if beat is None:
        raise HTTPException(status_code=404, detail="beat not found")
    fields = body.model_dump(exclude_unset=True)
    for key, value in fields.items():
        setattr(beat, key, value)
    await session.flush()
    return beat
