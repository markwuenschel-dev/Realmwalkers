"""Import-adoption endpoints (ADR 0028, Slice 3b — Lane A5; ADR-0032 W1).

The operator "Start contract adoption" is the explicit, human-initiated entry point that turns an
uncontracted, evidence-only imported chapter into worker-claimable adoption work. It either CREATES a
`queued` ImportAdoption (queued == spend consent — the worker's claim loop drains it) or promotes an
existing `awaiting_start` adoption to `queued` (Q3/Q17). Unpause is not Start.

Start is now one of FOUR entry paths into the single adoption-entry lifecycle (ADR-0032 D1), not the
only writer: sync Revise (W3, `reviews.accept_revision_intent`) and boot reconciliation (W4,
`workers.boot_reconciliation`) enter through the same seam. `awaiting_start` rows are minted by
reconciliation (`RECORD_WITHOUT_SPEND`); what Start uniquely supplies is `operator_independent`
liveness — the operator command is itself durable demand, so reverse-cancellation never retires it.

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
authorizes exactly one fresh author pass. The route refuses (never overwrites) a contracted or
already-approved chapter.

ADR-0032 W1: both endpoints are now thin HTTP adapters over the adoption-owned seam
(`shared.adoption_entry.ensure_import_adoption`), which owns the chapter reload under the lock, the
evidence-only + operation-specific eligibility envelope, the transition table, liveness, the source
fingerprint, and force-token/lineage. The seam raises transport-agnostic domain errors; this module maps
them to the exact 404/409 payloads below.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy.exc import DBAPIError

from dominion.api.deps import SessionDep
from dominion.shared.adoption_entry import (
    AdoptionChapterNotFound,
    ChapterContractAlreadyApproved,
    ChapterHasContractedScenes,
    ChapterNotAmendable,
    ensure_import_adoption,
)
from dominion.shared.chapter_lock import (
    BUSY_DETAIL,
    DEFAULT_LOCK_TIMEOUT_MS,
    ChapterWorkflowBusy,
    is_lock_timeout,
)
from dominion.shared.enums import AdoptionOperation
from dominion.shared.schemas import AmendmentEligibilityOut, ImportAdoptionOut, ReauthorIn
from dominion.workers.packet import amendment

log = structlog.get_logger()
router = APIRouter(tags=["adoption"])

# The request-path wait ceiling for acquiring the per-chapter workflow lock (Q16). A module attribute so
# the busy-path oracle can patch it to a short value; production uses the shared 4s default.
LOCK_TIMEOUT_MS: int | None = DEFAULT_LOCK_TIMEOUT_MS


def _kick_adoption_drain(background: BackgroundTasks) -> None:
    """Hand a freshly-queued adoption to the worker that claims it.

    `queued` is spend consent. Without this kick the row sits until boot or a Revise path fires
    `drain_adoptions`. The drain is single-flight per process, so an idempotent remint is cheap.
    """
    from dominion.workers.import_adoption import drain_adoptions

    background.add_task(drain_adoptions)


@router.post("/chapters/{chapter_id}/adoption/start", response_model=ImportAdoptionOut)
async def start_contract_adoption(
    chapter_id: uuid.UUID, session: SessionDep, background: BackgroundTasks
) -> ImportAdoptionOut:
    """Start (or resume) import adoption for an evidence-only imported chapter.

    Creates a `queued` ImportAdoption, promotes an existing `awaiting_start` one to `queued`, or returns
    an already-`queued`/`running` one unchanged (idempotent). Refuses a chapter with any contracted scene
    (`409 chapter_has_contracted_scenes`) and a lock collision (`409 chapter_workflow_busy`). The whole
    decision + write runs under the per-chapter workflow lock, so nothing is queued from a stale read.
    """
    try:
        result = await ensure_import_adoption(
            session,
            chapter_id=chapter_id,
            operation=AdoptionOperation.OPERATOR_START,
            timeout_ms=LOCK_TIMEOUT_MS,
        )
    except AdoptionChapterNotFound as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc
    except ChapterHasContractedScenes as exc:
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
        ) from exc
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc

    adoption = result.adoption
    # The seam's wrapper owns the commit; refresh so server-side defaults (created_at) and the onupdate
    # (updated_at) are loaded before serialization instead of lazy-loading on the async session.
    await session.refresh(adoption)
    log.info("adoption.started", chapter=str(chapter_id), adoption=str(adoption.id), status=adoption.status)
    _kick_adoption_drain(background)
    return ImportAdoptionOut.model_validate(adoption)


@router.post("/chapters/{chapter_id}/adoption/reauthor", response_model=ImportAdoptionOut)
async def reauthor_contract_adoption(
    chapter_id: uuid.UUID, body: ReauthorIn, session: SessionDep, background: BackgroundTasks
) -> ImportAdoptionOut:
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
    try:
        result = await ensure_import_adoption(
            session,
            chapter_id=chapter_id,
            operation=AdoptionOperation.REAUTHOR,
            force_author_token=body.force_author_token,
            timeout_ms=LOCK_TIMEOUT_MS,
        )
    except AdoptionChapterNotFound as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc
    except ChapterHasContractedScenes as exc:
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
        ) from exc
    except ChapterContractAlreadyApproved as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "chapter_contract_already_approved",
                "message": (
                    "This chapter already has an approved contract. Changing approved material is an "
                    "amendment/revision, not a re-author — the force route will not overwrite it."
                ),
            },
        ) from exc
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc

    adoption = result.adoption
    await session.refresh(adoption)
    log.info(
        "adoption.reauthor",
        chapter=str(chapter_id),
        adoption=str(adoption.id),
        token=str(body.force_author_token),
        status=adoption.status,
    )
    _kick_adoption_drain(background)
    return ImportAdoptionOut.model_validate(adoption)


# ----------------------------------- amendment mode (#261) ---------------------------------------- #


@router.get("/chapters/{chapter_id}/amendment/eligibility", response_model=AmendmentEligibilityOut)
async def amendment_eligibility(chapter_id: uuid.UUID, session: SessionDep) -> AmendmentEligibilityOut:
    """Read-only preflight: may this chapter's approved contract be amended, and if not, WHY not.

    Exists so the Desk can show the refusal (and which action to take instead — re-derive a scene packet,
    run initial adoption, review the amendment already open) BEFORE the author commits to the model call
    that `POST .../amendment/start` buys. It takes no lock, writes nothing, and calls no model.

    ADVISORY ONLY. `POST .../packet/{packet_id}/approve-amendment` recomputes this same verdict under the
    chapter workflow lock and fails closed there, so an `eligible: true` answer here is not authorization
    — prose can move between this read and the commit.
    """
    try:
        verdict = await amendment.assess_chapter(session, chapter_id=chapter_id)
    except amendment.AmendmentChapterNotFound as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc

    return AmendmentEligibilityOut(
        chapter_id=verdict.chapter_id,
        eligible=verdict.eligible,
        reason=verdict.reason,
        # One source for the sentence (`amendment.REFUSAL_MESSAGES`), and only for a refusal — an eligible
        # chapter has nothing to explain. The eligible token is deliberately absent from that dict.
        message=None if verdict.eligible else amendment.REFUSAL_MESSAGES.get(verdict.reason),
        approved_packet_id=verdict.approved_packet_id,
        open_amendment_packet_id=verdict.open_amendment_packet_id,
        unseeded_scene_ids=list(verdict.unseeded_scene_ids),
        seeded_scene_ids=list(verdict.seeded_scene_ids),
        source_fingerprint=verdict.source_fingerprint,
    )


@router.post("/chapters/{chapter_id}/amendment/start", response_model=ImportAdoptionOut)
async def start_amendment(chapter_id: uuid.UUID, session: SessionDep, background: BackgroundTasks) -> ImportAdoptionOut:
    """Start amendment mode: author a REPLACEMENT chapter contract for a chapter whose approved contract
    has no seed for some imported scene (#261). The deliberate operator command that authorizes exactly
    one amendment author pass; approving the result is a separate, second human action.

    The FIFTH entry path into the one adoption-entry lifecycle, and the only one whose envelope is
    positive rather than evidence-only: it REQUIRES an approved ChapterPacket and tolerates contracted
    scenes (`shared/adoption_entry.py:177-183`). "May this chapter be amended" therefore has exactly one
    implementation — `packet.amendment.assess_chapter` — shared with the eligibility preflight above and
    with the locked approve transition, so the three cannot drift.

    Refuses an unamendable chapter with the assessment's own `reason` token (409), and a lock collision
    with `409 chapter_workflow_busy`. Idempotent: an amendment adoption already in flight is returned
    unchanged rather than spending a second author pass.
    """
    try:
        result = await ensure_import_adoption(
            session,
            chapter_id=chapter_id,
            operation=AdoptionOperation.AMENDMENT,
            timeout_ms=LOCK_TIMEOUT_MS,
        )
    except AdoptionChapterNotFound as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc
    except ChapterNotAmendable as exc:
        # The token comes from the eligibility verdict, and the sentence from the SAME dict the preflight
        # reads, so the Desk sees one message per condition on both surfaces. `str(exc)` is the fallback
        # for the eligible token (never a refusal) or any token added to the verdict but not the dict.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "message": amendment.REFUSAL_MESSAGES.get(exc.reason) or str(exc),
            },
        ) from exc
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    except DBAPIError as exc:
        # `SET LOCAL lock_timeout` from the acquire applies for the REST of the transaction, so a ROW lock
        # taken later inside the seam (the adoption insert's savepoint, the chapter reload) can time out
        # too — as a bare 55P03, not ChapterWorkflowBusy. Same operator-visible condition, same retryable
        # 409 rather than a 500 (`shared/chapter_lock.py:64-74`).
        if not is_lock_timeout(exc):
            raise
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc

    adoption = result.adoption
    await session.refresh(adoption)
    log.info(
        "adoption.amendment_started",
        chapter=str(chapter_id),
        adoption=str(adoption.id),
        mode=adoption.mode,
        status=adoption.status,
    )
    _kick_adoption_drain(background)
    return ImportAdoptionOut.model_validate(adoption)
