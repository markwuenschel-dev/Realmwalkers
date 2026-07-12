"""Schedule draft and revision jobs after human approval (contract-first).

Orchestration lives here; job_routing mints Job rows. All draft paths delegate to draft_queue.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, GateMode
from dominion.shared.models import Beat, Chapter, Run, Scene
from dominion.workers.draft_queue import (
    DraftQueueBlocker,
    DraftScheduleResult,
    resolve_approved_scene_packet_for_beat,
    schedule_contract_first_draft_jobs,
)
from dominion.workers.job_routing import revision_job_for_scene


async def _latest_run(session: AsyncSession, book_id: uuid.UUID) -> Run | None:
    return (
        await session.execute(select(Run).where(Run.book_id == book_id).order_by(Run.created_at.desc()).limit(1))
    ).scalar_one_or_none()


async def schedule_next_after_approval(session: AsyncSession, scene: Scene) -> uuid.UUID | None:
    """In pause_each, queue the next scene's draft if its beat exists and contract resolves."""
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    if run is None or run.gate_mode != GateMode.PAUSE_EACH:
        return None
    next_no = scene.scene_no + 1
    beat = (
        await session.execute(select(Beat).where(Beat.chapter_id == scene.chapter_id, Beat.scene_no == next_no))
    ).scalar_one_or_none()
    if beat is None:
        return None
    result = await schedule_contract_first_draft_jobs(
        session, chapter=chapter, beats=[beat], run=run, skip_drafted=True
    )
    if not result.queued_job_ids:
        return None
    return result.queued_job_ids[0]


async def schedule_revision(
    session: AsyncSession, scene: Scene, *, target_pass: str | None, production_run_id: uuid.UUID | None = None
) -> uuid.UUID | DraftQueueBlocker | None:
    """Queue a REVISE_* job for a scene, or refuse with an actionable blocker.

    Contract-first guard: `resolve_job` needs the scene's Beat backed by an approved ScenePacket, so a
    revision is only queueable when that contract exists. An imported scene has prose but no Beat/packet
    at any tier, so queuing would mint a job the worker rejects at drain time (the exact defect this
    guard closes). Return a `revision_contract_required` blocker instead of an impossible job; the caller
    surfaces it as a 409 (reviews) or a human escalation (production repair). Returns None only when the
    scene's chapter is gone.
    """
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    beat = (
        (
            await session.execute(
                select(Beat)
                .where(Beat.chapter_id == scene.chapter_id, Beat.scene_no == scene.scene_no)
                .order_by(Beat.id)
            )
        )
        .scalars()
        .first()
    )
    resolved = (
        await resolve_approved_scene_packet_for_beat(session, beat=beat, repair=False) if beat is not None else None
    )
    if beat is None or isinstance(resolved, DraftQueueBlocker):
        return DraftQueueBlocker(
            chapter_id=scene.chapter_id,
            scene_no=scene.scene_no,
            beat_id=beat.id if beat is not None else None,
            scene_packet_id=None,
            reason="revision_contract_required",
            message=(
                f"Scene {scene.scene_no} has no approved story contract (it was imported, not derived), "
                "so it can't be revised yet."
            ),
            required_action=(
                "Derive and approve a scene packet for this scene (Packets tab) to create its contract, "
                "then request the revision again."
            ),
        )
    run = await _latest_run(session, chapter.book_id)
    job = await revision_job_for_scene(
        session, scene=scene, chapter=chapter, run=run, target_pass=target_pass, production_run_id=production_run_id
    )
    session.add(job)
    await session.flush()
    return job.id


async def schedule_beats_on_gate1_approval(
    session: AsyncSession, chapter: Chapter, beats: list[Beat], run: Run | None
) -> DraftScheduleResult:
    """Legacy name — delegates to contract-first scheduler (API must not call without ScenePackets)."""
    return await schedule_contract_first_draft_jobs(session, chapter=chapter, beats=beats, run=run, skip_drafted=False)


async def schedule_scene_redrafts(
    session: AsyncSession, chapter: Chapter, scenes: list[Scene], run: Run | None
) -> DraftScheduleResult:
    return await schedule_contract_first_draft_jobs(
        session, chapter=chapter, scenes=scenes, run=run, skip_drafted=False
    )


async def schedule_undrafted_beats(session: AsyncSession, chapter: Chapter, run: Run | None) -> DraftScheduleResult:
    """Queue a DRAFT job for every APPROVED beat with no scene prose yet."""
    beats = (
        (
            await session.execute(
                select(Beat)
                .where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.APPROVED)
                .order_by(Beat.scene_no)
            )
        )
        .scalars()
        .all()
    )
    return await schedule_contract_first_draft_jobs(
        session, chapter=chapter, beats=list(beats), run=run, skip_drafted=True
    )
