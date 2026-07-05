"""Central activity feed endpoints — the single source the Activity drawer reads from.

Rows are written app-wide via `workers.activity.record_activity`; this router only reads them and
handles clearing. "Dismiss" and "Clear" soft-hide (stamp `dismissed_at`); the retention sweep is what
hard-deletes aged rows (see workers/sweeper.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from dominion.api.deps import SessionDep
from dominion.shared.models import Activity
from dominion.shared.schemas import ActivityClearIn, ActivityClearOut, ActivityOut
from dominion.workers.activity import FINISHED_KINDS

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=list[ActivityOut])
async def list_activity(
    session: SessionDep,
    book_id: uuid.UUID | None = None,
    since: datetime | None = None,
    kind: str | None = None,
    include_dismissed: bool = False,
    limit: int = 100,
) -> list[ActivityOut]:
    """Recent activity, newest first. The drawer polls this while open; capped so the feed can't
    return an unbounded history."""
    limit = max(1, min(limit, 200))
    # Order by the monotonic seq, not created_at: same-transaction emits share a timestamp (PG now()).
    stmt = select(Activity).order_by(Activity.seq.desc()).limit(limit)
    if book_id is not None:
        stmt = stmt.where(Activity.book_id == book_id)
    if since is not None:
        stmt = stmt.where(Activity.created_at >= since)
    if kind is not None:
        stmt = stmt.where(Activity.kind == kind)
    if not include_dismissed:
        stmt = stmt.where(Activity.dismissed_at.is_(None))
    rows = (await session.execute(stmt)).scalars().all()
    return [ActivityOut.model_validate(row) for row in rows]


@router.post("/activity/{activity_id}/dismiss", response_model=ActivityOut)
async def dismiss_activity(activity_id: uuid.UUID, session: SessionDep) -> ActivityOut:
    row = await session.get(Activity, activity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="activity not found")
    if row.dismissed_at is None:
        row.dismissed_at = datetime.now(UTC)
    await session.commit()
    return ActivityOut.model_validate(row)


@router.post("/activity/clear", response_model=ActivityClearOut)
async def clear_activity(body: ActivityClearIn, session: SessionDep) -> ActivityClearOut:
    """Bulk soft-hide. scope="finished" clears terminal history (FINISHED_KINDS); scope="all" clears
    everything still showing. Optional book scope keeps one book's clear from touching another's."""
    q = select(Activity.id).where(Activity.dismissed_at.is_(None))
    if body.book_id is not None:
        q = q.where(Activity.book_id == body.book_id)
    if body.scope == "finished":
        q = q.where(Activity.kind.in_(FINISHED_KINDS))
    ids = list((await session.execute(q)).scalars().all())
    if ids:
        await session.execute(update(Activity).where(Activity.id.in_(ids)).values(dismissed_at=datetime.now(UTC)))
    await session.commit()
    return ActivityClearOut(dismissed=len(ids))
