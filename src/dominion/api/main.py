"""FastAPI app — the thin boundary between the React review app and Postgres (DESIGN §1).

Generation runs in-process as FastAPI background tasks (the drain in workers.background_work); this
module only wires routes, applies saved settings at boot, and resumes a drain that a redeploy killed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dominion.api.routers import (
    activity,
    adoption,
    beats,
    books,
    chapters,
    docs,
    enrich,
    health,
    jobs,
    learning,
    manuscript,
    markup,
    packets,
    parts,
    pipeline,
    production,
    reviews,
    runs,
    scene_packets,
    scenes,
    telemetry,
    threads,
    volumes,
    world,
)
from dominion.api.routers import (
    settings as settings_router,
)
from dominion.api.routers.settings import apply_model_overrides
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import ImportAdoptionStatus, JobStatus
from dominion.shared.models import ImportAdoption, Job, RepairTask
from dominion.workers import background_work, import_adoption

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """On startup, apply any saved per-agent model overrides to the live settings (so a model choice
    from the Settings screen survives a redeploy). Best-effort — a fresh DB has no table/rows yet."""
    try:
        async with SessionFactory() as session:
            n = await apply_model_overrides(session)
        if n:
            log.info("settings.model_overrides_applied", count=n)
    except Exception as exc:  # noqa: BLE001 — never block boot on an optional override load
        log.warning("settings.model_overrides_load_failed", error=str(exc))
    # Reconcile stranded imported-prose revise intent (ADR-0032 D7/D8, W4) — BEFORE the drains below.
    # The ordering is load-bearing: reconciliation is what turns a scene the last redeploy stranded at
    # `revision_requested` back into durable, adoption-linked work. Kicking the drains first would let
    # this boot's drain pass run against a queue that is still missing exactly that work, so recovery
    # would wait a whole extra deploy cycle. It awaits (rather than fire-and-forget) so the drains that
    # follow observe a reconciled queue, and it commits per chapter, so it cannot hold boot open on one
    # contended chapter. Never in `apply_lightweight_migrations` (D7) — that also builds the test fixture.
    try:
        from dominion.workers.boot_reconciliation import reconcile_legacy_revision_intent

        await reconcile_legacy_revision_intent()
    except Exception as exc:  # noqa: BLE001 — never block boot on recovery
        # ERROR, not warning: a wholesale reconciliation failure means stranded scenes stay stranded
        # and invisible, which is exactly the condition this step exists to surface.
        log.error("adoption_reconciliation_failed", error=str(exc))

    # Resume the drafting drain if a redeploy stranded QUEUED jobs. The drain is an in-process
    # background task, so a container swap kills it mid-queue and the jobs otherwise sit QUEUED
    # forever — silently, as "N queued" — until a human posts /jobs/draft-next. Fire-and-forget:
    # drain_queued_jobs already single-flights via its process-global lock and persists per-job
    # failures, so a boot-time kick is exactly as safe as a button-press kick.
    try:
        from sqlalchemy import func, select

        from dominion.shared.authorization import requires_explicit_authorization_clause

        async with SessionFactory() as session:
            paused = await background_work.load_queue_paused(session)
            # Any QUEUED kind, not just DRAFT: the worker's claim is kind-agnostic, and an
            # upload-originated revision (revise_full/revise_pass, no Run) would otherwise sit
            # stranded across redeploys because nothing else kicks its drain. The `book_id IS NOT NULL`
            # guard mirrors claim_one_job (ADR 0027): an ownerless job is never claimable, so counting it
            # here would only schedule a no-op drain against a phantom queue.
            queued = (
                await session.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.status == JobStatus.QUEUED, Job.book_id.is_not(None))
                )
            ).scalar_one()
            queued_repairs = (
                await session.execute(
                    select(func.count())
                    .select_from(RepairTask)
                    # A1c: same predicate the drain claims on — work within the DEFAULT authorization
                    # ceiling. Counting anything else would schedule a drain against work it can't take.
                    .where(RepairTask.status == "queued", ~requires_explicit_authorization_clause())
                )
            ).scalar_one()
            # Claimable adoptions strand for the same reason (ADR-0028's leased adoption worker is an
            # in-process drain too), plus one worse: an expired lease leaves a row at `running` that
            # nothing re-queues until a drain runs `recover_stale_adoptions`. Count both states.
            queued_adoptions = (
                await session.execute(
                    select(func.count())
                    .select_from(ImportAdoption)
                    .where(
                        ImportAdoption.status.in_(
                            (ImportAdoptionStatus.QUEUED.value, ImportAdoptionStatus.RUNNING.value)
                        )
                    )
                )
            ).scalar_one()
        if queued_adoptions:
            # drain_adoptions single-flights and honors the pause switch itself, so kick unconditionally
            # and let it decide — a paused queue still needs the stale-lease recovery it runs first.
            log.info("adoption.drain_resumed_on_boot", queued_adoptions=queued_adoptions)
            asyncio.get_running_loop().create_task(import_adoption.drain_adoptions())
        if (queued or queued_repairs) and paused:
            # Honor the human pause switch across redeploys — work stays queued until resumed.
            log.info("draft.drain_resume_skipped_paused", queued=queued, queued_repairs=queued_repairs)
        else:
            # The repair drain chains into the job drain, so kick at most one of the two.
            if queued_repairs:
                log.info("repair.drain_resumed_on_boot", queued_repairs=queued_repairs)
                asyncio.get_running_loop().create_task(background_work.drain_queued_repair_tasks())
            elif queued:
                log.info("draft.drain_resumed_on_boot", queued=queued)
                asyncio.get_running_loop().create_task(background_work.drain_queued_jobs())
    except Exception as exc:  # noqa: BLE001 — never block boot on the resume probe
        log.warning("draft.drain_resume_failed", error=str(exc))

    # Book-ownership integrity (ADR 0027): the boot migration already backfilled/quarantined; here we
    # surface the *result*. Log at error on every boot while holds exist, and append an Activity
    # transition ONLY when the holds fingerprint changes (Activity is append-only — per-boot emission
    # would flood the Desk), updating the singleton state atomically in the same commit.
    try:
        from dominion.shared.job_integrity import inspect_job_ownership
        from dominion.shared.models import JobIntegrityState
        from dominion.workers import activity

        async with SessionFactory() as session:
            report = await inspect_job_ownership(await session.connection())
            if report.has_holds:
                log.error(
                    "integrity.ownerless_jobs",
                    holds=report.hold_count,
                    quarantined=report.quarantined_total,
                    unresolved_null_book=report.null_book_total,
                    conflicts=report.conflicts,
                    promoted=report.promoted,
                )
            state = await session.get(JobIntegrityState, 1)
            if state is None or state.fingerprint != report.fingerprint:
                verb = "cleared" if report.hold_count == 0 else f"{report.hold_count} job(s) held"
                await activity.safe_record_activity(
                    session,
                    kind="integrity_hold",
                    title=f"Job ownership integrity: {verb}",
                    source="integrity",
                    severity="error" if report.has_holds else "success",
                    payload={
                        "hold_count": report.hold_count,
                        "quarantined": report.quarantined_total,
                        "conflicts": report.conflicts,
                        "promoted": report.promoted,
                        "fingerprint": report.fingerprint,
                    },
                )
                if state is None:
                    session.add(JobIntegrityState(id=1, fingerprint=report.fingerprint, hold_count=report.hold_count))
                else:
                    state.fingerprint = report.fingerprint
                    state.hold_count = report.hold_count
                await session.commit()
    except Exception as exc:  # noqa: BLE001 — never block boot on the integrity probe
        log.warning("integrity.boot_probe_failed", error=str(exc))

    # Start the autonomous self-repair + retention loop (workers/sweeper.py). One in-process background
    # task; it gates its own work behind the autonomy + queue-pause switches and single-flights each
    # tick. Cancelled on shutdown so a redeploy doesn't leave an orphan loop behind.
    sweeper_task: asyncio.Task[None] | None = None
    try:
        from dominion.workers import sweeper

        sweeper_task = asyncio.get_running_loop().create_task(sweeper.run_forever())
    except Exception as exc:  # noqa: BLE001 — a sweeper that fails to start must not block boot
        log.warning("sweeper.start_failed", error=str(exc))
    try:
        yield
    finally:
        if sweeper_task is not None:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Dominion Realm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(scenes.router)
app.include_router(reviews.router)
app.include_router(runs.router)
app.include_router(books.router)
app.include_router(chapters.router)
app.include_router(parts.router)
app.include_router(volumes.router)
app.include_router(beats.router)
app.include_router(packets.router)
app.include_router(scene_packets.router)
app.include_router(production.router)
app.include_router(pipeline.router)
app.include_router(telemetry.router)
app.include_router(jobs.router)
app.include_router(world.router)
app.include_router(activity.router)
app.include_router(threads.router)
app.include_router(markup.router)
app.include_router(learning.router)
app.include_router(settings_router.router)
app.include_router(docs.router)
app.include_router(manuscript.router)
app.include_router(enrich.router)
app.include_router(adoption.router)
