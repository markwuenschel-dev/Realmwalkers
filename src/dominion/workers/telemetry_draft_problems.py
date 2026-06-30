"""Draft readiness aggregation for book-level telemetry problems."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import Chapter
from dominion.workers.draft_readiness import compute_draft_readiness
from dominion.workers.telemetry_diagnostics import _problem


def _int_field(d: dict[str, object], key: str) -> int:
    v = d.get(key, 0)
    return int(v) if isinstance(v, int) else 0


def _list_field(d: dict[str, object], key: str) -> list[Any]:
    v = d.get(key)
    return list(v) if isinstance(v, list) else []


def _aggregate_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group blocker rows by (chapter_no, scene_no, required_action) with a count field."""
    grouped: dict[tuple[int | None, int | None, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("chapter_no"), row.get("scene_no"), row.get("required_action", ""))
        if key not in grouped:
            grouped[key] = {**row, "count": 0}
        grouped[key]["count"] += 1
    return sorted(
        grouped.values(),
        key=lambda r: (
            r.get("chapter_no") is None,
            r.get("chapter_no"),
            r.get("scene_no") is None,
            r.get("scene_no"),
            r.get("required_action", ""),
        ),
    )


async def detect_draft_not_ready(session: AsyncSession, book_id: uuid.UUID) -> dict[str, Any] | None:
    chapters = list(
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )
    if not chapters:
        return None

    raw_breakdown: list[dict[str, Any]] = []
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
        missing = _list_field(sp, "missing_scene_numbers")
        if missing:
            issues.append(f"missing scene packets: {missing[:5]}")
        if _int_field(sp, "stale") > 0:
            issues.append(f"{_int_field(sp, 'stale')} stale scene packet(s)")
        if _int_field(sp, "blocked") > 0:
            issues.append(f"{_int_field(sp, 'blocked')} blocked scene packet(s)")
        beats = readiness.beats
        unlinked = _list_field(beats, "unlinked")
        if unlinked:
            issues.append(f"{len(unlinked)} unlinked beat(s)")
        jobs = readiness.jobs
        if _int_field(jobs, "malformed") > 0:
            issues.append(f"{_int_field(jobs, 'malformed')} malformed draft job(s)")
        for b in readiness.blockers:
            blocker_count += 1
            raw_breakdown.append(
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
            raw_breakdown.append(
                {
                    "chapter_id": str(ch.id),
                    "chapter_no": ch.chapter_no,
                    "reason": "not_draftable",
                    "required_action": "; ".join(issues),
                    "message": issues[0],
                }
            )

    breakdown = _aggregate_breakdown(raw_breakdown)
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
