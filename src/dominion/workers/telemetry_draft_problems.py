"""Draft readiness aggregation for book-level telemetry problems."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import Chapter
from dominion.workers.draft_readiness import compute_draft_readiness
from dominion.workers.telemetry_diagnostics import _problem


async def detect_draft_not_ready(session: AsyncSession, book_id: uuid.UUID) -> dict[str, Any] | None:
    chapters = list(
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )
    if not chapters:
        return None

    breakdown: list[dict[str, Any]] = []
    blocker_count = 0
    for ch in chapters:
        try:
            readiness = await compute_draft_readiness(session, ch.id)
        except ValueError:
            continue
        if readiness.draftable:
            continue
        issues: list[str] = []
        if not readiness.chapter_packet_approved:
            issues.append("chapter packet not approved")
        sp = readiness.scene_packets
        if sp.get("missing_scene_numbers"):
            issues.append(f"missing scene packets: {sp['missing_scene_numbers'][:5]}")
        if sp.get("stale", 0) > 0:
            issues.append(f"{sp['stale']} stale scene packet(s)")
        if sp.get("blocked", 0) > 0:
            issues.append(f"{sp['blocked']} blocked scene packet(s)")
        beats = readiness.beats
        if beats.get("unlinked"):
            issues.append(f"{len(beats['unlinked'])} unlinked beat(s)")
        jobs = readiness.jobs
        if jobs.get("malformed", 0) > 0:
            issues.append(f"{jobs['malformed']} malformed draft job(s)")
        for b in readiness.blockers:
            blocker_count += 1
            breakdown.append(
                {
                    "chapter_id": str(ch.id),
                    "chapter_no": ch.chapter_no,
                    "scene_no": b.scene_no,
                    "reason": b.reason,
                    "required_action": b.required_action,
                    "message": b.message[:160] if b.message else "",
                }
            )
        if not readiness.blockers and issues:
            blocker_count += 1
            breakdown.append(
                {
                    "chapter_id": str(ch.id),
                    "chapter_no": ch.chapter_no,
                    "reason": "not_draftable",
                    "required_action": "; ".join(issues),
                    "message": issues[0],
                }
            )

    if not breakdown:
        return None

    ch_count = len({b["chapter_id"] for b in breakdown})
    return _problem(
        kind="draft_not_ready",
        severity="warn",
        summary=f"Draft not ready in {ch_count} chapter{'s' if ch_count != 1 else ''} ({blocker_count} blocker(s))",
        count=blocker_count,
        breakdown=breakdown[:20],
        recommended_action="Open Packets / Inbox per chapter; approve packets and scene packets before drafting.",
        drill_down={"draft_not_ready": True},
    )
