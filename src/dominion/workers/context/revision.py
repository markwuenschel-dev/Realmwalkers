"""Prior draft prose and author revision feedback for revise jobs.

Feedback is resolved through the durable RevisionRequest the Job was minted for (ADR 0028). A revise job
with no `revision_request_id` resolves feedback from its latest revise Approval instead — the current,
demonstrated path for any job minted without a link (ADR-0031 D11: this is NOT 'legacy'; it stays valid
until the request-linked path is fully cut over).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import Decision
from dominion.shared.models import Approval, Job, RevisionRequest, Scene
from dominion.workers.context.types import RevisionState


async def load_revision_state(session: AsyncSession, job: Job) -> RevisionState:
    prior = await session.get(Scene, job.target_scene_id) if job.target_scene_id is not None else None
    prior_prose = prior.prose if prior else None

    revise_feedback: str | None = None
    if job.revision_request_id is not None:
        # Authoritative: the immutable feedback captured on the durable request. If the author gave no
        # feedback, that None is authoritative too — do NOT fall back to an Approval.
        request = await session.get(RevisionRequest, job.revision_request_id)
        revise_feedback = request.feedback if request else None
    elif job.target_scene_id is not None:
        # No linked request: resolve feedback from the latest revise Approval — the current demonstrated
        # path for a job minted without a link (ADR-0031 D11; not 'legacy', valid until full cutover).
        revise_feedback = (
            await session.execute(
                select(Approval.feedback)
                .where(Approval.scene_id == job.target_scene_id, Approval.decision == Decision.REVISE)
                .order_by(Approval.decided_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    return RevisionState(prior_prose=prior_prose, revise_feedback=revise_feedback)
