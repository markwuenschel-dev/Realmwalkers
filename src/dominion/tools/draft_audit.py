"""Shared audit queries for draft queue recovery tools."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, JobKind, JobStatus, ScenePacketStatus
from dominion.shared.models import Beat, Job, ScenePacket

AuditRow = dict[str, str | int | None]


@dataclass
class AuditReport:
    malformed_jobs: list[AuditRow] = field(default_factory=list)
    unlinked_beats: list[AuditRow] = field(default_factory=list)
    duplicate_packets: list[AuditRow] = field(default_factory=list)
    beat_packet_mismatches: list[AuditRow] = field(default_factory=list)
    repairable_beats: list[AuditRow] = field(default_factory=list)


async def audit_chapter(session: AsyncSession, chapter_id: uuid.UUID) -> AuditReport:
    report = AuditReport()

    jobs = list(
        (
            await session.execute(
                select(Job).where(
                    Job.chapter_id == chapter_id,
                    Job.kind == JobKind.DRAFT,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
                )
            )
        )
        .scalars()
        .all()
    )
    for j in jobs:
        if j.scene_packet_id is None or j.beat_id is None:
            report.malformed_jobs.append(
                {
                    "id": str(j.id),
                    "status": j.status,
                    "scene_no": j.scene_no,
                    "scene_packet_id": str(j.scene_packet_id) if j.scene_packet_id else None,
                    "beat_id": str(j.beat_id) if j.beat_id else None,
                }
            )
            continue
        packet = await session.get(ScenePacket, j.scene_packet_id)
        if packet is None or packet.status != ScenePacketStatus.APPROVED or packet.stale_reason:
            report.malformed_jobs.append(
                {
                    "id": str(j.id),
                    "status": j.status,
                    "scene_no": j.scene_no,
                    "reason": "invalid_scene_packet",
                }
            )

    beats = list(
        (await session.execute(select(Beat).where(Beat.chapter_id == chapter_id, Beat.status == BeatStatus.APPROVED)))
        .scalars()
        .all()
    )
    for b in beats:
        if b.scene_packet_id is None:
            matches = list(
                (
                    await session.execute(
                        select(ScenePacket).where(
                            ScenePacket.chapter_id == chapter_id,
                            ScenePacket.scene_no == b.scene_no,
                            ScenePacket.status == ScenePacketStatus.APPROVED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            non_stale = [p for p in matches if not p.stale_reason]
            if len(non_stale) == 1:
                report.repairable_beats.append(
                    {
                        "beat_id": str(b.id),
                        "scene_no": b.scene_no,
                        "scene_packet_id": str(non_stale[0].id),
                    }
                )
            else:
                report.unlinked_beats.append(
                    {
                        "beat_id": str(b.id),
                        "scene_no": b.scene_no,
                        "match_count": len(non_stale),
                    }
                )
        else:
            packet = await session.get(ScenePacket, b.scene_packet_id)
            if packet and (packet.chapter_id != b.chapter_id or packet.scene_no != b.scene_no):
                report.beat_packet_mismatches.append(
                    {
                        "beat_id": str(b.id),
                        "packet_id": str(packet.id),
                    }
                )

    dup_rows = (
        await session.execute(
            select(ScenePacket.chapter_id, ScenePacket.scene_no, func.count())
            .where(
                ScenePacket.chapter_id == chapter_id,
                ScenePacket.status == ScenePacketStatus.APPROVED,
            )
            .group_by(ScenePacket.chapter_id, ScenePacket.scene_no)
            .having(func.count() > 1)
        )
    ).all()
    for ch_id, scene_no, count in dup_rows:
        report.duplicate_packets.append(
            {
                "chapter_id": str(ch_id),
                "scene_no": scene_no,
                "count": int(count),
            }
        )

    return report
