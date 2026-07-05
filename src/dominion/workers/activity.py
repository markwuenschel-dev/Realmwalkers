"""Central activity feed — the single writer for the app-wide Activity drawer.

Every mutating surface (production, jobs, reviews, runs, the autonomous sweeper, retention) routes
through `record_activity`, so the drawer reads ONE source (`GET /activity`) instead of scraping the
Jobs table, per-run AgentEvents, and an in-memory list. Keep this dependency-light: it is imported by
routers, workers, and the sweeper. It never commits — it flushes and rides the caller's transaction,
so an activity that records an action which is later rolled back rolls back with it.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import Activity, ProductionRun

log = structlog.get_logger()

# Kinds the drawer treats as terminal history — the "Clear finished" button dismisses these and
# leaves in-flight/queued activity alone. `scope="all"` dismisses everything regardless.
FINISHED_KINDS: frozenset[str] = frozenset(
    {
        "draft_done",
        "draft_failed",
        "repair_verified",
        "run_completed",
        "run_cancelled",
        "run_deleted",
        "retention_pruned",
    }
)


async def record_activity(
    session: AsyncSession,
    *,
    kind: str,
    title: str,
    source: str,
    severity: str = "info",
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    production_run_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Activity:
    """Append one activity row (+ flush, mirroring production._record_event). The ONLY constructor of
    Activity rows. Does not commit — the caller's own commit persists it."""
    activity = Activity(
        kind=kind,
        title=title,
        source=source,
        severity=severity,
        book_id=book_id,
        chapter_id=chapter_id,
        production_run_id=production_run_id,
        job_id=job_id,
        detail=detail,
        payload_json=payload,
    )
    session.add(activity)
    await session.flush()
    return activity


def _production_severity(event_type: str) -> str:
    et = event_type.lower()
    if any(k in et for k in ("error", "failed", "blocked")):
        return "error"
    if any(k in et for k in ("required", "waiting", "parked", "escalat")):
        return "warn"
    if any(k in et for k in ("completed", "verified", "approved", "applied", "assembled", "resolved", "started")):
        return "success" if "started" not in et else "info"
    return "info"


async def record_from_production_event(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    event_type: str,
    stage: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Mirror a production AgentEvent into the central feed. Best-effort: a feed failure must never
    break the production pipeline, so everything here is swallowed and logged. Resolves book/chapter
    from the run (usually an identity-map hit — the run is already loaded in these flows)."""
    try:
        run = await session.get(ProductionRun, run_id)
        title = message or event_type.replace("_", " ").capitalize()
        await record_activity(
            session,
            kind=event_type,
            title=title,
            source="production",
            severity=_production_severity(event_type),
            book_id=run.book_id if run else None,
            chapter_id=run.chapter_id if run else None,
            production_run_id=run_id,
            detail=stage,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — the feed is advisory; never fail the caller's action
        log.warning("activity.production_mirror_failed", event_type=event_type, error=str(exc))


async def safe_record_activity(session: AsyncSession, **kwargs: Any) -> None:
    """record_activity that swallows/logs its own failure — for hot paths (job transitions) where an
    activity write must never strand the primary work."""
    try:
        await record_activity(session, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("activity.record_failed", kind=kwargs.get("kind"), error=str(exc))
