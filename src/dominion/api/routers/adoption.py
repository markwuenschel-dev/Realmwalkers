"""Import-adoption endpoints (ADR 0028, Slice 3b — Lane A5; ADR-0032 W1).

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
from fastapi import APIRouter, HTTPException

from dominion.api.deps import SessionDep
from dominion.shared.adoption_entry import (
    AdoptionChapterNotFound,
    ChapterContractAlreadyApproved,
    ChapterHasContractedScenes,
    ensure_import_adoption,
)
from dominion.shared.chapter_lock import DEFAULT_LOCK_TIMEOUT_MS, ChapterWorkflowBusy
from dominion.shared.enums import AdoptionOperation
from dominion.shared.schemas import ImportAdoptionOut, ReauthorIn

log = structlog.get_logger()
router = APIRouter(tags=["adoption"])

# The request-path wait ceiling for acquiring the per-chapter workflow lock (Q16). A module attribute so
# the busy-path oracle can patch it to a short value; production uses the shared 4s default.
LOCK_TIMEOUT_MS: int | None = DEFAULT_LOCK_TIMEOUT_MS

_BUSY_DETAIL = {
    "reason": "chapter_workflow_busy",
    "message": "This chapter is busy with another workflow operation. Retry in a moment.",
}


@router.post("/chapters/{chapter_id}/adoption/start", response_model=ImportAdoptionOut)
async def start_contract_adoption(chapter_id: uuid.UUID, session: SessionDep) -> ImportAdoptionOut:
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
        raise HTTPException(status_code=409, detail=_BUSY_DETAIL) from exc

    adoption = result.adoption
    # The seam's wrapper owns the commit; refresh so server-side defaults (created_at) and the onupdate
    # (updated_at) are loaded before serialization instead of lazy-loading on the async session.
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
        raise HTTPException(status_code=409, detail=_BUSY_DETAIL) from exc

    adoption = result.adoption
    await session.refresh(adoption)
    log.info(
        "adoption.reauthor",
        chapter=str(chapter_id),
        adoption=str(adoption.id),
        token=str(body.force_author_token),
        status=adoption.status,
    )
    return ImportAdoptionOut.model_validate(adoption)
