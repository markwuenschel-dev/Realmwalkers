"""Build draft/revision Jobs that carry direct context IDs (scene-packet contract system).

Every new job routes by direct IDs — book_id, chapter_id, beat_id, scene_packet_id — so
`assemble_context` resolves work without `run_id` (run_id is now batch/provenance metadata). These
helpers are the single place those ids are populated, so no creation path can forget one.

A draft job must carry a non-null scene_packet_id (drafting requires an approved scene contract); the
caller is responsible for only enqueuing beats whose ScenePacket is approved.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import JobKind, JobStatus
from dominion.shared.models import Beat, Chapter, Job, Run, Scene


def draft_job_for_beat(
    *,
    beat: Beat,
    chapter: Chapter,
    run: Run | None,
    target_scene_id: uuid.UUID | None = None,
    production_run_id: uuid.UUID | None = None,
) -> Job:
    """A DRAFT job for one beat, carrying the full direct-ID routing tuple.

    production_run_id (when present) scopes DraftRunTimeline memory and post-scene updates
    to the owning production execution.
    """
    job = Job(
        run_id=run.id if run else None,
        kind=JobKind.DRAFT,
        target_scene_id=target_scene_id,
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        beat_id=beat.id,
        scene_packet_id=beat.scene_packet_id,
        chapter_no=chapter.chapter_no,
        scene_no=beat.scene_no,
        token_budget=run.token_budget if run else settings.scene_token_budget,
        status=JobStatus.QUEUED,
    )
    if production_run_id:
        job.production_run_id = production_run_id
    return job


async def _beat_for_scene(session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int) -> Beat | None:
    return (
        (
            await session.execute(
                select(Beat).where(Beat.chapter_id == chapter_id, Beat.scene_no == scene_no).order_by(Beat.id)
            )
        )
        .scalars()
        .first()
    )


async def draft_job_for_scene(
    session: AsyncSession,
    *,
    scene: Scene,
    chapter: Chapter,
    run: Run | None,
    production_run_id: uuid.UUID | None = None,
) -> Job:
    """A DRAFT job that re-drafts (supersedes) an existing scene. Direct IDs come from the scene and
    its beat; scene_packet_id is the scene's own contract (falling back to the beat's)."""
    beat = await _beat_for_scene(session, chapter_id=scene.chapter_id, scene_no=scene.scene_no)
    job = Job(
        run_id=run.id if run else None,
        kind=JobKind.DRAFT,
        target_scene_id=scene.id,
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        beat_id=beat.id if beat else None,
        scene_packet_id=scene.scene_packet_id or (beat.scene_packet_id if beat else None),
        chapter_no=chapter.chapter_no,
        scene_no=scene.scene_no,
        token_budget=run.token_budget if run else settings.scene_token_budget,
        status=JobStatus.QUEUED,
    )
    if production_run_id:
        job.production_run_id = production_run_id
    return job


async def revision_job_for_scene(
    session: AsyncSession,
    *,
    scene: Scene,
    chapter: Chapter,
    run: Run | None,
    target_pass: str | None,
    production_run_id: uuid.UUID | None = None,
) -> Job:
    """A revision job targeting an existing scene, with direct IDs resolved from the scene + its beat."""
    beat = await _beat_for_scene(session, chapter_id=scene.chapter_id, scene_no=scene.scene_no)
    job = Job(
        run_id=run.id if run else None,
        kind=JobKind.REVISE_PASS if target_pass else JobKind.REVISE_FULL,
        target_scene_id=scene.id,
        target_pass=target_pass,
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        beat_id=beat.id if beat else None,
        scene_packet_id=scene.scene_packet_id or (beat.scene_packet_id if beat else None),
        chapter_no=chapter.chapter_no,
        scene_no=scene.scene_no,
        token_budget=run.token_budget if run else settings.scene_token_budget,
        status=JobStatus.QUEUED,
    )
    if production_run_id:
        job.production_run_id = production_run_id
    return job
