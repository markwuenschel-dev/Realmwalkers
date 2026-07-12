"""Contract-first draft job scheduling — single gate for all draft queue paths.

Every draft job must carry a validated approved ScenePacket before insert. See docs/contract_first_drafting.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared import job_policy
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, ScenePacketStatus
from dominion.shared.job_policy import scope_jobs_to_book
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
    "sequence_budget_mismatch",  # lane 3: scene word budgets contradict the chapter envelope
    "revision_contract_required",  # revise requested on a scene with no approved Beat/ScenePacket (e.g. imported)
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


def resolve_approved_scene_packet_for_beat_prefetched(
    beat: Beat,
    *,
    packet_by_id: dict[uuid.UUID, ScenePacket],
    packets_by_scene_no: dict[int, list[ScenePacket]],
) -> ScenePacket | DraftQueueBlocker:
    """Read-only twin of `resolve_approved_scene_packet_for_beat(repair=False)` over prefetched rows.

    Same decision tree, no DB access and never a repair — so a caller resolving N beats (readiness)
    issues TWO queries total instead of ~3 per beat. That N+1 made GET /draft/readiness take multiple
    seconds against a networked Postgres. `packets_by_scene_no` must contain only this chapter's
    packets; a dangling cross-chapter `beat.scene_packet_id` simply misses `packet_by_id` and falls
    through to the scene_no lookup, exactly like the DB twin's mismatch validation would."""
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
        packet = packet_by_id.get(beat.scene_packet_id)
        if packet is not None and _validate_packet_for_beat(beat, packet) is None:
            return packet

    matches = packets_by_scene_no.get(beat.scene_no, [])
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
    return non_stale[0]


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
    production_run_id: uuid.UUID | None = None,
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
            job = await draft_job_for_scene(
                session, scene=scene, chapter=chapter, run=run, production_run_id=production_run_id
            )
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
        job = draft_job_for_beat(beat=beat, chapter=chapter, run=run, production_run_id=production_run_id)
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
    """Re-queue FAILED jobs. DRAFT jobs get a fresh draft (never clone null scene_packet_id); a FAILED
    revision (revise_full/revise_pass) keeps its target scene + Approval feedback intact, so it's reset
    in place back to QUEUED rather than rebuilt."""
    failed_q = scope_jobs_to_book(
        select(Job).where(
            Job.status.in_(job_policy.RETRYABLE),
            Job.kind.in_((JobKind.DRAFT, JobKind.REVISE_FULL, JobKind.REVISE_PASS)),
        ),
        book_id,
    )
    failed_jobs = list((await session.execute(failed_q)).scalars().all())
    result = RequeueResult(requested=len(failed_jobs))
    log.info("draft_requeue.requested", requested=result.requested, book_id=str(book_id) if book_id else None)

    seen_scenes: set[tuple[uuid.UUID, int, uuid.UUID | None]] = set()

    for old in failed_jobs:
        if old.kind in (JobKind.REVISE_FULL, JobKind.REVISE_PASS):
            # Nothing to rebuild — the revision reads its prior prose via target_scene_id and its
            # feedback from the latest Approval(decision=revise). Reset in place so the drain re-runs it.
            if old.target_scene_id is None:
                result.skipped.append(
                    _blocker(
                        chapter_id=old.chapter_id or uuid.UUID(int=0),
                        scene_no=old.scene_no,
                        beat_id=old.beat_id,
                        scene_packet_id=old.scene_packet_id,
                        reason="legacy_job_unreconcilable",
                        message=f"Revision job {old.id} has no target scene.",
                        required_action="Cancel this job and request revisions again.",
                    )
                )
                continue
            old.status = JobStatus.QUEUED
            old.last_error = None
            old.claimed_by = None
            old.claimed_at = None
            old.finished_at = None
            result.queued += 1
            log.info("draft_requeue.revision_reset", job_id=str(old.id), target_scene_id=str(old.target_scene_id))
            continue

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
            old.finished_at = datetime.now(UTC)
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
        old.finished_at = datetime.now(UTC)
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


async def cancel_queued_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Cancel exactly one QUEUED job (Activity drawer ×). skip_locked mirrors the worker's claim:
    a job being claimed RIGHT NOW is invisible here (the worker holds its row lock for the whole
    scene generation), so the caller sees a clean 'not cancellable' instead of a minutes-long
    blocking wait. Returns the (detached) job info on success, None when missing/running/locked."""
    row = (
        await session.execute(
            select(Job).where(Job.id == job_id, Job.status == JobStatus.QUEUED).with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    # Capture primitives before deletion — the ORM object expires once the row is gone.
    cancelled = Job(
        id=row.id, kind=row.kind, chapter_no=row.chapter_no, scene_no=row.scene_no, token_budget=row.token_budget
    )
    await _purge_job_ids(session, [job_id])
    return cancelled


async def purge_draft_jobs_for_scene_packet(
    session: AsyncSession,
    *,
    scene_packet_id: uuid.UUID,
) -> int:
    """Delete all draft jobs tied to one scene packet (queued, running, or failed)."""
    job_ids = list(
        (
            await session.execute(
                select(Job.id).where(
                    Job.kind == JobKind.DRAFT,
                    Job.scene_packet_id == scene_packet_id,
                )
            )
        )
        .scalars()
        .all()
    )
    purged = await _purge_job_ids(session, job_ids)
    if purged:
        log.info("draft_purge.scene_packet_jobs", scene_packet_id=str(scene_packet_id), purged=purged)
    return purged


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
    """Delete FAILED jobs without re-queueing (dismiss from Desk) — every kind, not just DRAFT.

    The Desk's failed count and banner (`_queue_counts`, `GET /jobs/failed`) are kind-agnostic, so a
    failed REVISE_FULL/REVISE_PASS job (e.g. an auto-queued revision that errored) shows there too. A
    DRAFT-only purge left those behind: the count never dropped and the user "couldn't clear" them.
    Dismiss deletes whatever the banner shows, so it stays consistent across job kinds.
    """
    failed_q = scope_jobs_to_book(select(Job.id).where(Job.status.in_(job_policy.DISMISSABLE)), book_id)
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


async def purge_done_draft_jobs(
    session: AsyncSession,
    *,
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    older_than: datetime | None = None,
) -> PurgeResult:
    """Delete DONE jobs (every kind) — the counterpart to purge_failed_draft_jobs for the "recently
    finished" list. The Activity drawer's "Clear finished" calls this with no `older_than` (clear all
    finished); the retention sweep passes a cutoff so only aged rows are pruned. DONE jobs are pure
    exhaust — the scenes they produced live in the scenes table and are untouched here."""
    done_q = scope_jobs_to_book(select(Job.id).where(Job.status.in_(job_policy.RETENTION_PURGEABLE)), book_id)
    if chapter_id is not None:
        done_q = done_q.where(Job.chapter_id == chapter_id)
    if older_than is not None:
        done_q = done_q.where(Job.finished_at < older_than)
    job_ids = list((await session.execute(done_q)).scalars().all())
    purged = await _purge_job_ids(session, job_ids)
    log.info(
        "draft_purge.done",
        purged=purged,
        book_id=str(book_id) if book_id else None,
        chapter_id=str(chapter_id) if chapter_id else None,
        older_than=older_than.isoformat() if older_than else None,
    )
    return PurgeResult(purged=purged)
