"""Import-adoption endpoints (ADR 0028, Slice 3b — Lane A5).

The operator "Start contract adoption" is the explicit, human-initiated entry point that turns an
uncontracted, evidence-only imported chapter into worker-claimable adoption work. It either CREATES a
`queued` ImportAdoption (queued == spend consent — the worker's claim loop drains it) or promotes an
existing `awaiting_start` adoption to `queued` (Q3/Q17). The auto-start-on-revise reconciliation writer
that mints `awaiting_start` rows is a Slice 3c non-goal and is NOT built here; unpause is not Start.

Guards:
  * only EVIDENCE-ONLY chapters may start — every non-superseded scene must be imported/uncontracted
    (no scene of record bound to an approved ScenePacket). A chapter with any contracted scene is a typed
    `chapter_has_contracted_scenes` refuse (409), never a silent proceed (Q6).
  * Start is an authority-changing chapter mutation, so it runs INSIDE `run_under_chapter_workflow` — the
    chapter workflow lock precedes any row lock (Q15). A lock collision maps to `409 chapter_workflow_busy`
    at the API boundary (Q16); nothing is written and the operator retries.

Idempotency: a chapter that already has a `queued`/`running` adoption returns that row unchanged (no
duplicate spend). Re-running author work over unchanged inputs is the worker's tiered-idempotency concern
(Q11), not this endpoint's.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.chapter_lock import (
    DEFAULT_LOCK_TIMEOUT_MS,
    ChapterWorkflowBusy,
    run_under_chapter_workflow,
)
from dominion.shared.enums import ImportAdoptionMode, ImportAdoptionStatus, SceneStatus
from dominion.shared.models import Chapter, ImportAdoption, Scene
from dominion.shared.prose_fingerprint import chapter_source_fingerprint
from dominion.shared.schemas import ImportAdoptionOut

log = structlog.get_logger()
router = APIRouter(tags=["adoption"])

# The request-path wait ceiling for acquiring the per-chapter workflow lock (Q16). A module attribute so
# the busy-path oracle can patch it to a short value; production uses the shared 4s default.
LOCK_TIMEOUT_MS: int | None = DEFAULT_LOCK_TIMEOUT_MS

# Adoptions the worker has already been consented to spend on: a fresh Start over such a chapter is an
# idempotent no-op that returns the in-flight row rather than double-spending.
_ACTIVE_STATUSES = (ImportAdoptionStatus.QUEUED.value, ImportAdoptionStatus.RUNNING.value)


async def _contracted_scene_count(session: AsyncSession, chapter_id: uuid.UUID) -> int:
    """How many of the chapter's non-superseded scenes are CONTRACTED — bound to an approved ScenePacket
    of record (`scene_packet_id IS NOT NULL`). An imported/uncontracted scene has prose but no such link;
    a nonzero count means the chapter is not evidence-only and adoption must refuse (Q6)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Scene)
            .where(
                Scene.chapter_id == chapter_id,
                Scene.status != SceneStatus.SUPERSEDED,
                Scene.scene_packet_id.is_not(None),
            )
        )
    ).scalar_one()


async def _source_fingerprint(session: AsyncSession, chapter_id: uuid.UUID) -> str:
    """The chapter's current prose-hash source fingerprint over its non-superseded scenes (Q10). Set at
    creation so the NOT-NULL column is populated; the worker re-captures it in its short leased claim txn,
    so this value is a starting point, not the compare-and-set authority."""
    rows = (
        await session.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return chapter_source_fingerprint((int(r[0]), r[1], int(r[2]), r[3]) for r in rows)


async def _existing_adoption(
    session: AsyncSession, chapter_id: uuid.UUID, statuses: tuple[str, ...]
) -> ImportAdoption | None:
    """The most recent adoption for this chapter in one of `statuses`, if any (newest first)."""
    return (
        await session.execute(
            select(ImportAdoption)
            .where(ImportAdoption.chapter_id == chapter_id, ImportAdoption.status.in_(statuses))
            .order_by(ImportAdoption.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.post("/chapters/{chapter_id}/adoption/start", response_model=ImportAdoptionOut)
async def start_contract_adoption(chapter_id: uuid.UUID, session: SessionDep) -> ImportAdoptionOut:
    """Start (or resume) import adoption for an evidence-only imported chapter.

    Creates a `queued` ImportAdoption, promotes an existing `awaiting_start` one to `queued`, or returns
    an already-`queued`/`running` one unchanged (idempotent). Refuses a chapter with any contracted scene
    (`409 chapter_has_contracted_scenes`) and a lock collision (`409 chapter_workflow_busy`). The whole
    decision + write runs under the per-chapter workflow lock, so nothing is queued from a stale read.
    """

    async def _body() -> ImportAdoption:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="chapter not found")

        # Q6: only evidence-only chapters may start. Checked under the workflow lock so a concurrent
        # scene-packet approval (which also holds the lock) cannot contract a scene between this read
        # and the queue.
        if await _contracted_scene_count(session, chapter_id) > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "chapter_has_contracted_scenes",
                    "message": (
                        "This chapter already has at least one contracted scene, so it is not "
                        "evidence-only. Import adoption reconstructs a contract only for a chapter of "
                        "purely imported, uncontracted scenes."
                    ),
                },
            )

        # Idempotency / duplicate-spend guard: a chapter already being adopted returns its in-flight row.
        active = await _existing_adoption(session, chapter_id, _ACTIVE_STATUSES)
        if active is not None:
            return active

        # Q17: promote a legacy reconciliation-created awaiting_start row to spend-consented queued.
        awaiting = await _existing_adoption(session, chapter_id, (ImportAdoptionStatus.AWAITING_START.value,))
        if awaiting is not None:
            awaiting.status = ImportAdoptionStatus.QUEUED.value
            awaiting.error = None
            return awaiting

        adoption = ImportAdoption(
            book_id=chapter.book_id,
            chapter_id=chapter_id,
            mode=ImportAdoptionMode.INITIAL.value,
            status=ImportAdoptionStatus.QUEUED.value,
            source_fingerprint=await _source_fingerprint(session, chapter_id),
        )
        session.add(adoption)
        await session.flush()
        return adoption

    try:
        adoption = await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS)
    except ChapterWorkflowBusy as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "chapter_workflow_busy",
                "message": "This chapter is busy with another workflow operation. Retry in a moment.",
            },
        ) from exc

    # run_under_chapter_workflow owns the commit; refresh so server-side defaults (created_at) and the
    # onupdate (updated_at) are loaded before serialization instead of lazy-loading on the async session.
    await session.refresh(adoption)
    log.info("adoption.started", chapter=str(chapter_id), adoption=str(adoption.id), status=adoption.status)
    return ImportAdoptionOut.model_validate(adoption)
