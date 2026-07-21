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

Operator Re-author (Q11 tier-C, "reauthor" endpoint): the explicit human override for the ONE case the
tiered idempotency won't help — the operator wants a fresh proposal even though nothing changed. The
client supplies an immutable `force_author_token` (a stable idempotency key, never server-generated) that
authorizes exactly one fresh author pass: the worker BYPASSES the reuse gate for a token-carrying claim,
authors fresh, and a partial UNIQUE index on the token makes a retried Re-author return the same adoption
rather than reroll again. The route refuses (never overwrites) a contracted or already-approved chapter.
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
from dominion.shared.enums import ImportAdoptionMode, ImportAdoptionStatus, PacketStatus, SceneStatus
from dominion.shared.models import Chapter, ChapterPacket, ImportAdoption, Scene
from dominion.shared.prose_fingerprint import chapter_source_fingerprint
from dominion.shared.schemas import ImportAdoptionOut, ReauthorIn

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


async def _adoption_by_force_token(session: AsyncSession, token: uuid.UUID) -> ImportAdoption | None:
    """The adoption already created for this operator Re-author token, if any — the idempotency-key lookup
    (a retried Re-author returns the same row, never a second spend). The token is globally unique (a
    partial UNIQUE index enforces it), so this is not scoped to the chapter: the DB guarantees at most one."""
    return (
        await session.execute(select(ImportAdoption).where(ImportAdoption.force_author_token == token).limit(1))
    ).scalar_one_or_none()


async def _has_approved_chapter_packet(session: AsyncSession, chapter_id: uuid.UUID) -> bool:
    """Whether the chapter already carries an APPROVED ChapterPacket. Re-authoring approved material is an
    amendment/revision concern, not a hidden overwrite — the Re-author route refuses it (Q11 tier-C, oracle 6)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED.value)
        )
    ).scalar_one() > 0


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


@router.post("/chapters/{chapter_id}/adoption/reauthor", response_model=ImportAdoptionOut)
async def reauthor_contract_adoption(chapter_id: uuid.UUID, body: ReauthorIn, session: SessionDep) -> ImportAdoptionOut:
    """Operator Re-author (Q11 tier-C force override): explicitly author a FRESH chapter contract from the
    imported prose, bypassing the worker's tiered-idempotency reuse gate that would otherwise return the
    existing packet. The deliberate "I want a new proposal even though nothing changed" action.

    The client supplies `force_author_token` (a UUID) — a client-stable idempotency key, never
    server-generated, so a network retry cannot silently buy a second reroll. Under the per-chapter
    workflow lock, in order: 404 if the chapter is missing; refuse (`409 chapter_has_contracted_scenes` /
    `409 chapter_contract_already_approved`) rather than overwrite contracted or approved material; return
    the existing adoption unchanged if this token already spent (idempotency); return an already in-flight
    (`queued`/`running`) adoption rather than race a parallel author pass (serialize); else create a fresh
    `queued`, force-flagged adoption linked to the prior proposed contract for audit. A lock collision maps
    to `409 chapter_workflow_busy`.
    """

    async def _body() -> ImportAdoption:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="chapter not found")

        # (b) Never a hidden overwrite (oracle 6): refuse a chapter that is not purely evidence-only, or
        # whose contract is already APPROVED (that is an amendment/revision concern, not a re-author).
        if await _contracted_scene_count(session, chapter_id) > 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "chapter_has_contracted_scenes",
                    "message": (
                        "This chapter already has at least one contracted scene, so it is not "
                        "evidence-only. Re-author reconstructs a contract only for a chapter of purely "
                        "imported, uncontracted scenes."
                    ),
                },
            )
        if await _has_approved_chapter_packet(session, chapter_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "chapter_contract_already_approved",
                    "message": (
                        "This chapter already has an approved contract. Changing approved material is an "
                        "amendment/revision, not a re-author — the force route will not overwrite it."
                    ),
                },
            )

        # (c) Idempotency: the same operator token never spends twice — return the row it already created.
        existing = await _adoption_by_force_token(session, body.force_author_token)
        if existing is not None:
            return existing

        # (d) Serialize: a chapter already being adopted returns its in-flight row rather than authoring in
        # parallel — no second, competing force pass.
        active = await _existing_adoption(session, chapter_id, _ACTIVE_STATUSES)
        if active is not None:
            return active

        # (e) Create a fresh queued, force-flagged adoption. `reauthor_of_adoption_id` links the prior
        # proposed contract this supersedes (or NULL). mode=initial; the current source fingerprint is a
        # starting point (the worker re-captures it in its leased claim txn).
        prior = await _existing_adoption(session, chapter_id, (ImportAdoptionStatus.CONTRACT_PROPOSED.value,))
        adoption = ImportAdoption(
            book_id=chapter.book_id,
            chapter_id=chapter_id,
            mode=ImportAdoptionMode.INITIAL.value,
            status=ImportAdoptionStatus.QUEUED.value,
            source_fingerprint=await _source_fingerprint(session, chapter_id),
            force_author_token=body.force_author_token,
            reauthor_of_adoption_id=(prior.id if prior is not None else None),
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

    await session.refresh(adoption)
    log.info(
        "adoption.reauthor",
        chapter=str(chapter_id),
        adoption=str(adoption.id),
        token=str(body.force_author_token),
        status=adoption.status,
    )
    return ImportAdoptionOut.model_validate(adoption)
