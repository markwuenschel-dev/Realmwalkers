"""Schedule draft and revision jobs after human approval (contract-first).

Orchestration lives here; job_routing mints Job rows. All draft paths delegate to draft_queue.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, GateMode
from dominion.shared.models import Beat, Chapter, Run, Scene
from dominion.workers.draft_queue import DraftScheduleResult, schedule_contract_first_draft_jobs
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


async def schedule_revision(session: AsyncSession, scene: Scene, *, target_pass: str | None) -> uuid.UUID | None:
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    job = await revision_job_for_scene(session, scene=scene, chapter=chapter, run=run, target_pass=target_pass)
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
