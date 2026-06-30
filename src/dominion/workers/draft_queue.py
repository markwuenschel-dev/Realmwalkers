"""Contract-first draft job scheduling — single gate for all draft queue paths.

Every draft job must carry a validated approved ScenePacket before insert. See docs/contract_first_drafting.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, JobKind, JobStatus, ScenePacketStatus
from dominion.shared.models import Beat, Chapter, DraftAttempt, Job, Run, Scene, ScenePacket
from dominion.workers.context.types import ScenePacketRequiredError
from dominion.workers.job_routing import draft_job_for_beat, draft_job_for_scene
from dominion.workers.scene_packet import approval_policy as sp_approval

log = structlog.get_logger()

DraftBlockerReason = Literal[
    "no_approved_scene_packet",
    "scene_packet_stale",
    "scene_packet_not_approved",
    "beat_not_approved",
    "beat_scene_packet_mismatch",
    "duplicate_approved_scene_packets",
    "already_queued",
    "already_drafted",
    "missing_scene_no",
    "legacy_job_unreconcilable",
]


@dataclass
class DraftQueueBlocker:
    chapter_id: uuid.UUID
    scene_no: int | None
    beat_id: uuid.UUID | None
    scene_packet_id: uuid.UUID | None
    reason: str
    message: str
    required_action: str


@dataclass
class DraftScheduleResult:
    queued_job_ids: list[uuid.UUID] = field(default_factory=list)
    skipped: list[DraftQueueBlocker] = field(default_factory=list)
    repaired_beats: int = 0


@dataclass
class RequeueResult:
    requested: int = 0
    queued: int = 0
    skipped: list[DraftQueueBlocker] = field(default_factory=list)


@dataclass
class PurgeResult:
    purged: int = 0


def _blocker(
    *,
    chapter_id: uuid.UUID,
    scene_no: int | None,
    beat_id: uuid.UUID | None,
    scene_packet_id: uuid.UUID | None,
    reason: DraftBlockerReason,
    message: str,
    required_action: str,
) -> DraftQueueBlocker:
    return DraftQueueBlocker(
        chapter_id=chapter_id,
        scene_no=scene_no,
        beat_id=beat_id,
        scene_packet_id=scene_packet_id,
        reason=reason,
        message=message,
        required_action=required_action,
    )


def _validate_packet_for_beat(beat: Beat, packet: ScenePacket) -> DraftQueueBlocker | None:
    if packet.chapter_id != beat.chapter_id or packet.scene_no != beat.scene_no:
        return _blocker(
            chapter_id=beat.chapter_id,
            scene_no=beat.scene_no,
            beat_id=beat.id,
            scene_packet_id=packet.id,
            reason="beat_scene_packet_mismatch",
            message=f"Beat ch{beat.chapter_id} sc{beat.scene_no} linked to mismatched packet",
            required_action="Re-derive beats from ScenePackets or repair links.",
        )
    try:
        sp_approval.assert_draft_ready(packet)
    except ScenePacketRequiredError as exc:
        reason: DraftBlockerReason = (
            "scene_packet_stale" if packet.status == ScenePacketStatus.STALE else "scene_packet_not_approved"
        )
        return _blocker(
            chapter_id=beat.chapter_id,
            scene_no=beat.scene_no,
            beat_id=beat.id,
            scene_packet_id=packet.id,
            reason=reason,
            message=str(exc),
            required_action="Approve or re-derive the ScenePacket before drafting.",
        )
    return None


async def _lookup_approved_packets(session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int) -> list[ScenePacket]:
    return list(
        (
            await session.execute(
                select(ScenePacket).where(
                    ScenePacket.chapter_id == chapter_id,
                    ScenePacket.scene_no == scene_no,
                    ScenePacket.status == ScenePacketStatus.APPROVED,
                )
            )
        )
        .scalars()
        .all()
    )


async def resolve_approved_scene_packet_for_beat(
    session: AsyncSession,
    *,
    beat: Beat,
    repair: bool = True,
) -> ScenePacket | DraftQueueBlocker:
    """Resolve an approved ScenePacket for a beat; optionally repair beat.scene_packet_id."""
    if beat.scene_no is None:
        return _blocker(
            chapter_id=beat.chapter_id,
            scene_no=None,
            beat_id=beat.id,
            scene_packet_id=beat.scene_packet_id,
            reason="missing_scene_no",
            message="Beat has no scene number.",
            required_action="Fix beat metadata or re-derive from ScenePackets.",
        )

    if beat.scene_packet_id is not None:
        packet = await session.get(ScenePacket, beat.scene_packet_id)
        if packet is not None:
            if _validate_packet_for_beat(beat, packet) is None:
                return packet

    matches = await _lookup_approved_packets(session, chapter_id=beat.chapter_id, scene_no=beat.scene_no)
    non_stale = [p for p in matches if p.status == ScenePacketStatus.APPROVED and not p.stale_reason]
    if len(non_stale) == 0:
        return _blocker(
            chapter_id=beat.chapter_id,
            scene_no=beat.scene_no,
            beat_id=beat.id,
            scene_packet_id=None,
            reason="no_approved_scene_packet",
            message=f"Ch{beat.chapter_id} sc{beat.scene_no} has no approved ScenePacket.",
            required_action="Derive and approve ScenePackets, then draft.",
        )
    if len(non_stale) > 1:
        return _blocker(
            chapter_id=beat.chapter_id,
            scene_no=beat.scene_no,
            beat_id=beat.id,
            scene_packet_id=None,
            reason="duplicate_approved_scene_packets",
            message=f"Ch{beat.chapter_id} sc{beat.scene_no} has {len(non_stale)} approved ScenePackets.",
            required_action="Resolve duplicate approved ScenePackets before drafting.",
        )
    packet = non_stale[0]
    if repair and beat.scene_packet_id != packet.id:
        beat.scene_packet_id = packet.id
        log.info(
            "draft_schedule.repaired_beat_link",
            chapter_id=str(beat.chapter_id),
            scene_no=beat.scene_no,
            beat_id=str(beat.id),
            scene_packet_id=str(packet.id),
        )
    return packet


async def has_active_draft_job_for_scene_packet(
    session: AsyncSession,
    *,
    scene_packet_id: uuid.UUID,
    target_scene_id: uuid.UUID | None = None,
) -> bool:
    """True when a QUEUED or RUNNING draft job already targets this contract."""
    return (
        await find_active_draft_job_id(session, scene_packet_id=scene_packet_id, target_scene_id=target_scene_id)
        is not None
    )


async def find_active_draft_job_id(
    session: AsyncSession,
    *,
    scene_packet_id: uuid.UUID,
    target_scene_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    q = select(Job.id).where(
        Job.kind == JobKind.DRAFT,
        Job.scene_packet_id == scene_packet_id,
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
    )
    if target_scene_id is not None:
        q = q.where(Job.target_scene_id == target_scene_id)
    else:
        q = q.where(Job.target_scene_id.is_(None))
    return (await session.execute(q.limit(1))).scalar_one_or_none()


async def schedule_contract_first_draft_jobs(
    session: AsyncSession,
    *,
    chapter: Chapter,
    beats: list[Beat] | None = None,
    scenes: list[Scene] | None = None,
    run: Run | None,
    skip_drafted: bool = True,
) -> DraftScheduleResult:
    """Queue draft jobs only when an approved ScenePacket resolves for each beat/scene."""
    result = DraftScheduleResult()
    log.info(
        "draft_schedule.requested",
        chapter_id=str(chapter.id),
        book_id=str(chapter.book_id),
        beat_count=len(beats or []),
        scene_count=len(scenes or []),
    )

    drafted: set[int] = set()
    if skip_drafted and scenes is None:
        drafted = {
            n for (n,) in (await session.execute(select(Scene.scene_no).where(Scene.chapter_id == chapter.id))).all()
        }

    if scenes is not None:
        for scene in scenes:
            beat = (
                await session.execute(
                    select(Beat).where(Beat.chapter_id == chapter.id, Beat.scene_no == scene.scene_no)
                )
            ).scalar_one_or_none()
            if beat is None:
                result.skipped.append(
                    _blocker(
                        chapter_id=chapter.id,
                        scene_no=scene.scene_no,
                        beat_id=None,
                        scene_packet_id=scene.scene_packet_id,
                        reason="no_approved_scene_packet",
                        message=f"No beat for scene {scene.scene_no}.",
                        required_action="Derive beats from approved ScenePackets.",
                    )
                )
                continue
            resolved = await resolve_approved_scene_packet_for_beat(session, beat=beat)
            if isinstance(resolved, DraftQueueBlocker):
                result.skipped.append(resolved)
                log.info("draft_schedule.skipped", reason=resolved.reason, scene_no=scene.scene_no)
                continue
            packet = resolved
            if beat.scene_packet_id != packet.id:
                result.repaired_beats += 1
            if await has_active_draft_job_for_scene_packet(
                session, scene_packet_id=packet.id, target_scene_id=scene.id
            ):
                existing_id = await find_active_draft_job_id(
                    session, scene_packet_id=packet.id, target_scene_id=scene.id
                )
                if existing_id is not None:
                    result.queued_job_ids.append(existing_id)
                continue
            beat.scene_packet_id = packet.id
            job = await draft_job_for_scene(session, scene=scene, chapter=chapter, run=run)
            job.beat_id = beat.id
            job.scene_packet_id = packet.id
            session.add(job)
            await session.flush()
            result.queued_job_ids.append(job.id)
            log.info(
                "draft_schedule.queued",
                job_id=str(job.id),
                scene_no=scene.scene_no,
                scene_packet_id=str(packet.id),
            )
        return result

    for beat in beats or []:
        if beat.status != BeatStatus.APPROVED:
            result.skipped.append(
                _blocker(
                    chapter_id=chapter.id,
                    scene_no=beat.scene_no,
                    beat_id=beat.id,
                    scene_packet_id=beat.scene_packet_id,
                    reason="beat_not_approved",
                    message=f"Beat sc{beat.scene_no} is {beat.status}, not approved.",
                    required_action="Approve ScenePackets to derive approved beats.",
                )
            )
            continue
        if skip_drafted and beat.scene_no in drafted:
            result.skipped.append(
                _blocker(
                    chapter_id=chapter.id,
                    scene_no=beat.scene_no,
                    beat_id=beat.id,
                    scene_packet_id=beat.scene_packet_id,
                    reason="already_drafted",
                    message=f"Scene {beat.scene_no} already has prose.",
                    required_action="Use redraft for existing scenes.",
                )
            )
            continue
        prior_link = beat.scene_packet_id
        resolved = await resolve_approved_scene_packet_for_beat(session, beat=beat)
        if isinstance(resolved, DraftQueueBlocker):
            result.skipped.append(resolved)
            log.info(
                "draft_schedule.blocked_missing_scene_packet",
                reason=resolved.reason,
                scene_no=beat.scene_no,
            )
            continue
        packet = resolved
        if prior_link != packet.id:
            result.repaired_beats += 1
        if await has_active_draft_job_for_scene_packet(session, scene_packet_id=packet.id):
            existing_id = await find_active_draft_job_id(session, scene_packet_id=packet.id)
            if existing_id is not None:
                result.queued_job_ids.append(existing_id)
            continue
        beat.scene_packet_id = packet.id
        job = draft_job_for_beat(beat=beat, chapter=chapter, run=run)
        session.add(job)
        await session.flush()
        result.queued_job_ids.append(job.id)
        log.info(
            "draft_schedule.queued",
            job_id=str(job.id),
            scene_no=beat.scene_no,
            scene_packet_id=str(packet.id),
        )
    return result


async def reconcile_and_requeue_failed_draft_jobs(
    session: AsyncSession,
    *,
    book_id: uuid.UUID | None = None,
) -> RequeueResult:
    """Create fresh draft jobs for FAILED jobs; never clone null scene_packet_id."""
    failed_q = select(Job).where(Job.status == JobStatus.FAILED, Job.kind == JobKind.DRAFT)
    if book_id is not None:
        failed_q = failed_q.where(Job.run_id.in_(select(Run.id).where(Run.book_id == book_id)))
    failed_jobs = list((await session.execute(failed_q)).scalars().all())
    result = RequeueResult(requested=len(failed_jobs))
    log.info("draft_requeue.requested", requested=result.requested, book_id=str(book_id) if book_id else None)

    seen_scenes: set[tuple[uuid.UUID, int, uuid.UUID | None]] = set()

    for old in failed_jobs:
        chapter_id = old.chapter_id
        scene_no = old.scene_no
        if chapter_id is None or scene_no is None:
            result.skipped.append(
                _blocker(
                    chapter_id=chapter_id or uuid.UUID(int=0),
                    scene_no=scene_no,
                    beat_id=old.beat_id,
                    scene_packet_id=old.scene_packet_id,
                    reason="legacy_job_unreconcilable",
                    message=f"Job {old.id} missing chapter_id or scene_no.",
                    required_action="Cancel this job and use Draft Chapter after fixing ScenePackets.",
                )
            )
            continue

        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            result.skipped.append(
                _blocker(
                    chapter_id=chapter_id,
                    scene_no=scene_no,
                    beat_id=old.beat_id,
                    scene_packet_id=old.scene_packet_id,
                    reason="legacy_job_unreconcilable",
                    message=f"Chapter {chapter_id} not found for job {old.id}.",
                    required_action="Cancel orphaned job.",
                )
            )
            continue

        beat = None
        if old.beat_id is not None:
            beat = await session.get(Beat, old.beat_id)
        if beat is None:
            beat = (
                await session.execute(select(Beat).where(Beat.chapter_id == chapter_id, Beat.scene_no == scene_no))
            ).scalar_one_or_none()
        if beat is None:
            result.skipped.append(
                _blocker(
                    chapter_id=chapter_id,
                    scene_no=scene_no,
                    beat_id=old.beat_id,
                    scene_packet_id=old.scene_packet_id,
                    reason="legacy_job_unreconcilable",
                    message=f"No beat for ch sc{scene_no}.",
                    required_action="Derive beats from approved ScenePackets.",
                )
            )
            continue

        dedupe_key = (chapter_id, scene_no, old.target_scene_id)
        if dedupe_key in seen_scenes:
            old.status = JobStatus.FAILED
            old.last_error = (old.last_error or "") + " [superseded by requeue dedupe]"
            continue
        seen_scenes.add(dedupe_key)

        resolved = await resolve_approved_scene_packet_for_beat(session, beat=beat, repair=True)
        if isinstance(resolved, DraftQueueBlocker):
            result.skipped.append(resolved)
            log.info("draft_requeue.skipped", reason=resolved.reason, scene_no=scene_no)
            continue
        packet = resolved

        if await has_active_draft_job_for_scene_packet(
            session, scene_packet_id=packet.id, target_scene_id=old.target_scene_id
        ):
            result.skipped.append(
                _blocker(
                    chapter_id=chapter_id,
                    scene_no=scene_no,
                    beat_id=beat.id,
                    scene_packet_id=packet.id,
                    reason="already_queued",
                    message=f"Active job already exists for sc{scene_no}.",
                    required_action="Wait for current job.",
                )
            )
            continue

        run = await session.get(Run, old.run_id) if old.run_id else None
        if old.target_scene_id is not None:
            scene = await session.get(Scene, old.target_scene_id)
            if scene is None:
                result.skipped.append(
                    _blocker(
                        chapter_id=chapter_id,
                        scene_no=scene_no,
                        beat_id=beat.id,
                        scene_packet_id=packet.id,
                        reason="legacy_job_unreconcilable",
                        message=f"Target scene {old.target_scene_id} missing.",
                        required_action="Use redraft from Chapters board.",
                    )
                )
                continue
            job = await draft_job_for_scene(session, scene=scene, chapter=chapter, run=run)
        else:
            job = draft_job_for_beat(beat=beat, chapter=chapter, run=run)
        job.beat_id = beat.id
        job.scene_packet_id = packet.id
        session.add(job)
        await session.flush()

        old.status = JobStatus.FAILED
        old.last_error = (old.last_error or "") + f" [superseded by requeue → {job.id}]"
        result.queued += 1
        log.info(
            "draft_requeue.reconciled",
            old_job_id=str(old.id),
            new_job_id=str(job.id),
            scene_packet_id=str(packet.id),
        )

    return result


async def _purge_job_ids(session: AsyncSession, job_ids: list[uuid.UUID]) -> int:
    if not job_ids:
        return 0
    await session.execute(update(DraftAttempt).where(DraftAttempt.job_id.in_(job_ids)).values(job_id=None))
    await session.execute(delete(Job).where(Job.id.in_(job_ids)))
    return len(job_ids)


async def purge_draft_jobs_for_scene(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    scene_no: int,
) -> int:
    """Delete all draft jobs for a chapter/scene slot (queued, running, or failed)."""
    job_ids = list(
        (
            await session.execute(
                select(Job.id).where(
                    Job.kind == JobKind.DRAFT,
                    Job.chapter_id == chapter_id,
                    Job.scene_no == scene_no,
                )
            )
        )
        .scalars()
        .all()
    )
    purged = await _purge_job_ids(session, job_ids)
    if purged:
        log.info(
            "draft_purge.scene_jobs",
            chapter_id=str(chapter_id),
            scene_no=scene_no,
            purged=purged,
        )
    return purged


async def purge_failed_draft_jobs(
    session: AsyncSession,
    *,
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
) -> PurgeResult:
    """Delete FAILED draft jobs without re-queueing (dismiss from Desk)."""
    failed_q = select(Job.id).where(Job.status == JobStatus.FAILED, Job.kind == JobKind.DRAFT)
    if book_id is not None:
        failed_q = failed_q.where(Job.run_id.in_(select(Run.id).where(Run.book_id == book_id)))
    if chapter_id is not None:
        failed_q = failed_q.where(Job.chapter_id == chapter_id)
    job_ids = list((await session.execute(failed_q)).scalars().all())
    if not job_ids:
        log.info(
            "draft_purge.cleared",
            purged=0,
            book_id=str(book_id) if book_id else None,
            chapter_id=str(chapter_id) if chapter_id else None,
        )
        return PurgeResult(purged=0)

    purged = await _purge_job_ids(session, job_ids)
    result = PurgeResult(purged=purged)
    log.info(
        "draft_purge.cleared",
        purged=result.purged,
        book_id=str(book_id) if book_id else None,
        chapter_id=str(chapter_id) if chapter_id else None,
    )
    return result
