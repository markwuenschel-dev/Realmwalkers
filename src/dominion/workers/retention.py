"""Retention sweep — hard-deletes aged exhaust so the DB and the Activity feed don't grow forever.

Exhaust-only and conservative: it prunes old Activity rows, DONE jobs, and terminal CANCELLED/FAILED
production runs (abandoned attempts). It NEVER touches COMPLETED runs (they carry the successful
assembly lineage — remove those by hand) or any manuscript table (chapters/scenes). Called on a slow
cadence by the sweeper; also safe to call directly. Does not commit — the caller's transaction does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ProductionRunStatus
from dominion.shared.models import Activity, ProductionRun
from dominion.workers import activity, production_delete
from dominion.workers.draft_queue import purge_done_draft_jobs

log = structlog.get_logger()


async def run_retention(session: AsyncSession, *, days: int) -> dict[str, int]:
    """Prune exhaust older than `days`. Returns the per-category counts pruned (also emitted as one
    `retention_pruned` activity when anything was removed)."""
    if days <= 0:
        return {"activities": 0, "jobs": 0, "runs": 0}
    cutoff = datetime.now(UTC) - timedelta(days=days)

    # 1) Old activity rows (age is the signal, dismissed or not). Set-based delete — don't materialize
    #    every aged id into Python and ship it back as a giant IN(...) list (a first prune after weeks of
    #    per-tick activity writes could be huge / hit asyncpg's param limit); rowcount gives the count.
    act_result = await session.execute(delete(Activity).where(Activity.created_at < cutoff))
    activities_pruned = int(getattr(act_result, "rowcount", 0) or 0)

    # 2) DONE jobs past the window (their scenes persist independently).
    jobs_purged = (await purge_done_draft_jobs(session, older_than=cutoff)).purged

    # 3) Abandoned terminal runs (cancelled/failed) past the window — full cascade delete. COMPLETED
    #    runs are deliberately left for manual delete.
    run_ids = list(
        (
            await session.execute(
                select(ProductionRun.id).where(
                    ProductionRun.status.in_((ProductionRunStatus.CANCELLED, ProductionRunStatus.FAILED)),
                    ProductionRun.updated_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    runs_deleted = 0
    for rid in run_ids:
        if await production_delete.delete_production_run(session, rid):
            runs_deleted += 1

    counts = {"activities": activities_pruned, "jobs": jobs_purged, "runs": runs_deleted}
    if any(counts.values()):
        await activity.record_activity(
            session,
            kind="retention_pruned",
            title=(
                f"Retention pruned {counts['activities']} activities, "
                f"{counts['jobs']} finished jobs, {counts['runs']} runs"
            ),
            source="retention",
            severity="info",
            payload=counts,
        )
        log.info("retention.pruned", days=days, **counts)
    return counts
