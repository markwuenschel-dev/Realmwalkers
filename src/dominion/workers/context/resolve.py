"""Resolve a Job row to book/chapter/beat entities (direct IDs + legacy fallbacks)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus
from dominion.shared.models import Beat, Chapter, Job, PovProfile, Run
from dominion.workers.context.types import ResolvedJob
from dominion.workers.pov import effective_pov


async def resolve_job(session: AsyncSession, job: Job) -> ResolvedJob:
    """Route by the job's DIRECT ids (book/chapter/beat/scene_packet). `run_id` is provenance only;
    the legacy (run_id, chapter_no, scene_no) lookup is a fallback for jobs created before direct-ID
    routing."""
    book_id = job.book_id
    if book_id is None:
        if job.run_id is None:
            raise ValueError("job is missing book_id and run_id — cannot resolve the book")
        book_id = (await session.execute(select(Run.book_id).where(Run.id == job.run_id))).scalar_one()

    chapter: Chapter | None = None
    if job.chapter_id is not None:
        chapter = await session.get(Chapter, job.chapter_id)
    if chapter is None and job.chapter_no is not None:
        chapter = (
            (
                await session.execute(
                    select(Chapter)
                    .where(Chapter.book_id == book_id, Chapter.chapter_no == job.chapter_no)
                    .order_by(Chapter.id)
                )
            )
            .scalars()
            .first()
        )
    if chapter is None:
        raise ValueError("no chapter for this job (missing chapter_id / chapter_no)")

    beat: Beat | None = None
    if job.beat_id is not None:
        beat = await session.get(Beat, job.beat_id)
    if beat is None:
        scene_no = job.scene_no
        if scene_no is None:
            raise ValueError("job is missing beat_id and scene_no — cannot resolve the beat")
        beats = (
            (
                await session.execute(
                    select(Beat).where(Beat.chapter_id == chapter.id, Beat.scene_no == scene_no).order_by(Beat.id)
                )
            )
            .scalars()
            .all()
        )
        beat = next((b for b in beats if b.status == BeatStatus.APPROVED), beats[0] if beats else None)
    if beat is None:
        raise ValueError(f"no beat for ch{chapter.chapter_no} sc{job.scene_no} — derive/approve a scene packet first")

    # The scene's effective POV (the beat's per-scene override, else the chapter POV) selects the voice
    # profile, so an overridden scene draws the OVERRIDE character's voice_spec/exemplars — not a label.
    pov = effective_pov(beat, chapter)
    profile = (
        await session.execute(
            select(PovProfile)
            .where(PovProfile.book_id == book_id, PovProfile.character == pov)
            .order_by(PovProfile.id)
            .limit(1)
        )
    ).scalar_one_or_none()

    scene_no = beat.scene_no if beat.scene_no is not None else job.scene_no
    assert scene_no is not None
    return ResolvedJob(
        book_id=book_id,
        chapter=chapter,
        beat=beat,
        profile=profile,
        scene_no=scene_no,
        scene_packet_id=job.scene_packet_id or beat.scene_packet_id,
    )
