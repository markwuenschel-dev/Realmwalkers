"""Prior draft prose and author revision feedback for revise jobs."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import Decision
from dominion.shared.models import Approval, Scene
from dominion.workers.context.types import RevisionState


async def load_revision_state(
    session: AsyncSession, target_scene_id: uuid.UUID
) -> RevisionState:
    prior = await session.get(Scene, target_scene_id)
    prior_prose = prior.prose if prior else None
    revise_feedback = (await session.execute(
        select(Approval.feedback)
        .where(Approval.scene_id == target_scene_id, Approval.decision == Decision.REVISE)
        .order_by(Approval.decided_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    return RevisionState(prior_prose=prior_prose, revise_feedback=revise_feedback)
