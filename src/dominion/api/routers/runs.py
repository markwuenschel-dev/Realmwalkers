"""Run + gate-1 beat proposal (DESIGN §4, §8). Stubbed until Phase 1/2."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dominion.api.deps import SessionDep
from dominion.shared.schemas import RunIn

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", status_code=501)
async def start_run(body: RunIn, session: SessionDep) -> dict[str, str]:
    # DESIGN §8: create a Run, then the bounded plan-call proposes per-scene beats from the
    # chapter outline for your approval (gate 1) before any scene-draft jobs are enqueued.
    raise HTTPException(status_code=501, detail="Phase 1/2: start run + propose beats (gate 1).")
