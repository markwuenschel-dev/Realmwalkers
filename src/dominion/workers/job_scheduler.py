"""Schedule draft and revision jobs after human approval (gate-1, pause_each, redraft).

Orchestration lives here; job_routing mints Job rows. API routers call these functions as thin adapters.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, GateMode, JobStatus
from dominion.shared.models import Beat, Chapter, Job, Run, Scene
from dominion.workers.job_routing import (
    draft_job_for_beat,
    draft_job_for_scene,
    revision_job_for_scene,
)


async def _latest_run(session: AsyncSession, book_id: uuid.UUID) -> Run | None:
    return (await session.execute(
        select(Run).where(Run.book_id == book_id).order_by(Run.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _find_queued_draft(
    session: AsyncSession,
    *,
    chapter_no: int,
    scene_no: int,
    book_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    allow_null_run: bool = False,
) -> uuid.UUID | None:
    """Return an existing QUEUED draft job id, or None. Dedupe scope matches the original call site."""
    if book_id is not None:
        return (await session.execute(
            select(Job.id).join(Run, Job.run_id == Run.id).where(
                Run.book_id == book_id,
                Job.chapter_no == chapter_no,
                Job.scene_no == scene_no,
                Job.status == JobStatus.QUEUED,
            )
        )).scalars().first()
    dedup = select(Job.id).where(
        Job.chapter_no == chapter_no,
        Job.scene_no == scene_no,
        Job.status == JobStatus.QUEUED,
    )
    if run_id is not None:
        dedup = dedup.where(Job.run_id == run_id)
    elif not allow_null_run:
        return None
    return (await session.execute(dedup)).scalars().first()


async def schedule_next_after_approval(session: AsyncSession, scene: Scene) -> uuid.UUID | None:
    """In pause_each, queue the next scene's draft if its beat exists and nothing is queued yet."""
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    if run is None or run.gate_mode != GateMode.PAUSE_EACH:
        return None
    next_no = scene.scene_no + 1
    beat = (await session.execute(
        select(Beat).where(Beat.chapter_id == scene.chapter_id, Beat.scene_no == next_no)
    )).scalar_one_or_none()
    if beat is None:
        return None
    existing = await _find_queued_draft(
        session, book_id=chapter.book_id, chapter_no=chapter.chapter_no, scene_no=next_no
    )
    if existing is not None:
        return existing
    job = draft_job_for_beat(beat=beat, chapter=chapter, run=run)
    session.add(job)
    await session.flush()
    return job.id


async def schedule_revision(
    session: AsyncSession, scene: Scene, *, target_pass: str | None
) -> uuid.UUID | None:
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    job = await revision_job_for_scene(
        session, scene=scene, chapter=chapter, run=run, target_pass=target_pass
    )
    session.add(job)
    await session.flush()
    return job.id


async def schedule_beats_on_gate1_approval(
    session: AsyncSession, chapter: Chapter, beats: list[Beat], run: Run | None
) -> list[uuid.UUID]:
    job_ids: list[uuid.UUID] = []
    for beat in beats:
        existing = await _find_queued_draft(
            session,
            book_id=chapter.book_id,
            chapter_no=chapter.chapter_no,
            scene_no=beat.scene_no,
        )
        if existing is not None:
            job_ids.append(existing)
            continue
        job = draft_job_for_beat(beat=beat, chapter=chapter, run=run)
        session.add(job)
        await session.flush()
        job_ids.append(job.id)
    return job_ids


async def schedule_scene_redrafts(
    session: AsyncSession, chapter: Chapter, scenes: list[Scene], run: Run | None
) -> list[uuid.UUID]:
    job_ids: list[uuid.UUID] = []
    for scene in scenes:
        existing = await _find_queued_draft(
            session,
            book_id=chapter.book_id,
            chapter_no=chapter.chapter_no,
            scene_no=scene.scene_no,
        )
        if existing is not None:
            job_ids.append(existing)
            continue
        job = await draft_job_for_scene(session, scene=scene, chapter=chapter, run=run)
        session.add(job)
        await session.flush()
        job_ids.append(job.id)
    return job_ids


async def schedule_undrafted_beats(
    session: AsyncSession, chapter: Chapter, run: Run | None
) -> list[uuid.UUID]:
    """Queue a DRAFT job for every APPROVED beat with no scene prose yet. Idempotent on queued jobs."""
    beats = (await session.execute(
        select(Beat).where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.APPROVED)
        .order_by(Beat.scene_no)
    )).scalars().all()
    drafted = {
        n for (n,) in (await session.execute(
            select(Scene.scene_no).where(Scene.chapter_id == chapter.id)
        )).all()
    }
    job_ids: list[uuid.UUID] = []
    for beat in beats:
        if beat.scene_no in drafted:
            continue
        existing = await _find_queued_draft(
            session,
            chapter_no=chapter.chapter_no,
            scene_no=beat.scene_no,
            run_id=run.id if run is not None else None,
            allow_null_run=run is None,
        )
        if existing is not None:
            job_ids.append(existing)
            continue
        job = draft_job_for_beat(beat=beat, chapter=chapter, run=run)
        session.add(job)
        await session.flush()
        job_ids.append(job.id)
    return job_ids
