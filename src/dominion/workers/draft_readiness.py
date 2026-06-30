"""Draft readiness queries — read-only diagnostics for contract-first drafting."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, JobKind, JobStatus, PacketStatus, ScenePacketStatus
from dominion.shared.models import Beat, Chapter, ChapterPacket, Job, ScenePacket
from dominion.shared.schemas import DraftQueueBlockerOut, DraftReadinessOut
from dominion.workers.draft_queue import DraftQueueBlocker, resolve_approved_scene_packet_for_beat


def blocker_out(b: DraftQueueBlocker) -> DraftQueueBlockerOut:
    return DraftQueueBlockerOut(
        chapter_id=b.chapter_id,
        scene_no=b.scene_no,
        beat_id=b.beat_id,
        scene_packet_id=b.scene_packet_id,
        reason=b.reason,
        message=b.message,
        required_action=b.required_action,
    )


async def compute_draft_readiness(session: AsyncSession, chapter_id: uuid.UUID) -> DraftReadinessOut:
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("chapter not found")

    cp = (await session.execute(
        select(ChapterPacket).where(
            ChapterPacket.chapter_id == chapter_id,
            ChapterPacket.status == PacketStatus.APPROVED,
        ).limit(1)
    )).scalar_one_or_none()
    chapter_packet_approved = cp is not None

    sp_rows = list((await session.execute(
        select(ScenePacket).where(ScenePacket.chapter_id == chapter_id)
    )).scalars().all())
    approved_sp = [p for p in sp_rows if p.status == ScenePacketStatus.APPROVED]
    stale_sp = [p for p in sp_rows if p.status == ScenePacketStatus.STALE]
    blocked_sp = [p for p in sp_rows if p.status == ScenePacketStatus.BLOCKED]
    seed_count = len((cp.body or {}).get("scene_seeds", [])) if cp else 0
    approved_nos = {p.scene_no for p in approved_sp}
    missing = sorted(set(range(1, seed_count + 1)) - approved_nos) if seed_count else []

    beats = list((await session.execute(
        select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)
    )).scalars().all())
    approved_beats = [b for b in beats if b.status == BeatStatus.APPROVED]
    linked = [b for b in approved_beats if b.scene_packet_id is not None]
    unlinked = [b.id for b in approved_beats if b.scene_packet_id is None]

    active_jobs = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.chapter_id == chapter_id,
            Job.kind == JobKind.DRAFT,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )).scalar_one() or 0

    malformed = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.chapter_id == chapter_id,
            Job.kind == JobKind.DRAFT,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
            Job.scene_packet_id.is_(None),
        )
    )).scalar_one() or 0

    sp_required_failed = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.chapter_id == chapter_id,
            Job.kind == JobKind.DRAFT,
            Job.status == JobStatus.FAILED,
            Job.last_error.ilike("%ScenePacket%"),
        )
    )).scalar_one() or 0

    blockers: list[DraftQueueBlockerOut] = []
    draftable_scenes = 0
    for beat in approved_beats:
        resolved = await resolve_approved_scene_packet_for_beat(session, beat=beat, repair=False)
        if isinstance(resolved, DraftQueueBlocker):
            blockers.append(blocker_out(resolved))
        else:
            draftable_scenes += 1

    draftable = (
        chapter_packet_approved
        and len(approved_sp) > 0
        and len(unlinked) == 0
        and len(blockers) == 0
        and draftable_scenes > 0
    )

    return DraftReadinessOut(
        chapter_id=chapter_id,
        chapter_packet_approved=chapter_packet_approved,
        scene_packets={
            "approved": len(approved_sp),
            "blocked": len(blocked_sp),
            "stale": len(stale_sp),
            "missing_scene_numbers": missing,
        },
        beats={
            "approved": len(approved_beats),
            "linked": len(linked),
            "unlinked": [str(i) for i in unlinked],
        },
        jobs={
            "active": int(active_jobs),
            "malformed": int(malformed),
            "failed_scene_packet_required": int(sp_required_failed),
        },
        draftable=draftable,
        blockers=blockers,
    )
