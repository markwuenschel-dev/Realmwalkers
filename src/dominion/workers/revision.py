"""The single revise-intent writer (ADR 0028 Slice 2; locked body per ADR-0032 W3).

`_accept_revision_request_locked` is the ONLY constructor of a `RevisionRequest`. It precomputes the DB
facts the pure `classify_revision` (revision_taxonomy) needs, delegates the 200/202/404/422/409 decision
to it, then persists durable author intent — replacing the old "schedule_revision -> 409 rollback" dead
end with a durable, observable `awaiting_contract` state.

**Ownership (ADR-0032 D4).** This module owns `RevisionRequest` and NOTHING ELSE — it never constructs,
promotes, or cancels an `ImportAdoption`. Adoption entry is the adoption module's (`shared.adoption_entry`),
and the two are sequenced by the command coordinator `accept_revision_intent`
(`api/routers/reviews.py`), which owns the chapter lock, the transaction boundary, and ordering. That is
why the accept body is `_locked`: it ASSUMES `run_under_chapter_workflow` is held and NEVER commits.

On a scene that already has an approved contract it mints the explicit revise Job (never a silent draft
— D8) and links it via `job.revision_request_id`, which lights up the already-wired-but-dormant reader
in `workers/context/revision.py`. On an imported/uncontracted scene it leaves the request at
`awaiting_contract`; nothing advances it until the Slice 3 adoption engine exists — but the intent
(target scene + version + prose hash + feedback + pass + origin) is now durable and recoverable.

Every 404/422/409 raises before any row is added, so the caller's transaction rolls back and neither the
Approval nor the RevisionRequest persists (the ADR-0028 rollback guarantee).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    Decision,
    ImportAdoptionStatus,
    JobStatus,
    RequestDisposition,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    ScenePacketStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Approval,
    Book,
    Chapter,
    ImportAdoption,
    Job,
    RevisionRequest,
    Scene,
    ScenePacket,
)
from dominion.shared.prose_fingerprint import chapter_source_fingerprint, prose_sha256
from dominion.shared.schemas import RevisionRequestOut
from dominion.workers.job_scheduler import schedule_revision
from dominion.workers.revision_taxonomy import RevisionFacts, RevisionOutcome, classify_revision

_ACTIVE_STATUSES = (
    RevisionRequestStatus.AWAITING_CONTRACT.value,
    RevisionRequestStatus.QUEUED.value,
    RevisionRequestStatus.RUNNING.value,
)

# status -> (display phase, required action). Server-derived, never stored (ADR 0028). Slice 2 maps
# from the coarse status alone; Slice 3 refines the awaiting_contract phase from the adoption + packets.
_PHASES: dict[str, tuple[str, str | None]] = {
    RevisionRequestStatus.AWAITING_CONTRACT.value: (
        "Preparing contract",
        "This scene needs an approved story contract before the revision can run.",
    ),
    RevisionRequestStatus.QUEUED.value: ("Queued", "The revision job is queued."),
    RevisionRequestStatus.RUNNING.value: ("Revising", None),
    RevisionRequestStatus.COMPLETED.value: ("Ready for review", "Review the revised scene."),
    RevisionRequestStatus.HELD.value: ("Held", "The revision produced a partial result — review needed."),
    RevisionRequestStatus.FAILED.value: ("Failed", "The revision failed — retry to reactivate it."),
    RevisionRequestStatus.SUPERSEDED.value: ("Superseded", None),
    RevisionRequestStatus.CANCELLED.value: ("Cancelled", None),
}


def prose_hash(text: str | None) -> str:
    """sha256 hex of the exact prose snapshot — the concurrency token pinned on a RevisionRequest and
    checked against the scene's current prose. Delegates to the canonical `prose_fingerprint.prose_sha256`
    (R4) so revise-intent and adoption evidence share ONE hash implementation. None/absent → hash of ''."""
    return prose_sha256(text)


def derive_display_phase(status: str) -> tuple[str, str | None]:
    """Pure: map the coarse persisted status to the fine UI phase + next action (server-derived)."""
    return _PHASES.get(status, (status, None))


def revision_request_out(request: RevisionRequest) -> RevisionRequestOut:
    """Serialize a RevisionRequest to the wire DTO, deriving the display phase + required action."""
    phase, action = derive_display_phase(request.status)
    return RevisionRequestOut(
        id=request.id,
        book_id=request.book_id,
        chapter_id=request.chapter_id,
        target_scene_id=request.target_scene_id,
        scene_no=request.scene_no,
        target_scene_version=request.target_scene_version,
        target_prose_hash=request.target_prose_hash,
        feedback=request.feedback,
        target_pass=request.target_pass,
        origin=request.origin,
        status=request.status,
        display_phase=phase,
        required_action=action,
        job_id=request.job_id,
        import_adoption_id=request.import_adoption_id,
        result_scene_id=request.result_scene_id,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@dataclass(frozen=True)
class AcceptResult:
    """What the revision owner did to the REQUEST — one of ADR-0032 D11's two independent facts. The
    other (did anything move forward) is the coordinator's to compute, because the adoption half of it
    is not this module's to know. `job_minted` is reported, not interpreted: it is the raw fact that
    THIS invocation minted the linked revise Job."""

    request: RevisionRequest
    disposition: RequestDisposition
    job_minted: bool


async def _active_request(session: AsyncSession, scene_id: uuid.UUID) -> RevisionRequest | None:
    """The one active request for this scene, if any (the partial unique index guarantees at most one;
    order+limit is belt-and-braces)."""
    return (
        await session.execute(
            select(RevisionRequest)
            .where(RevisionRequest.target_scene_id == scene_id, RevisionRequest.status.in_(_ACTIVE_STATUSES))
            .order_by(RevisionRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _supported_passes() -> frozenset[str]:
    """Valid `target_pass` names, derived from the router's lane table so this can't drift from the
    passes that actually run (imported lazily to avoid any import-time cycle through the router)."""
    from dominion.workers.router import DRAFT_PASSES

    return frozenset(DRAFT_PASSES)


async def _accept_revision_request_locked(
    session: AsyncSession,
    *,
    scene: Scene,
    feedback: str | None,
    target_pass: str | None,
    expected_prose_hash: str | None,
    origin: RevisionRequestOrigin,
) -> AcceptResult:
    """Precompute facts, classify, and persist durable revise intent for an existing `scene`.

    ASSUMES `run_under_chapter_workflow` is held and NEVER commits (ADR-0032 D4) — the coordinator
    `accept_revision_intent` owns the lock and the transaction boundary. `scene` must have been
    reloaded UNDER that lock; a caller-side pre-lock read would classify against stale prose.

    Raises HTTPException(404/422/409) BEFORE adding any row, so the coordinator's transaction rolls
    back and nothing persists. On accept, records the source Approval + the RevisionRequest atomically;
    if a contract already exists, mints the linked revise Job and advances the request to `queued`.
    """
    chapter = await session.get(Chapter, scene.chapter_id)
    book = await session.get(Book, chapter.book_id) if chapter is not None else None
    current_hash = prose_hash(scene.prose)

    active = await _active_request(session, scene.id)
    is_replay = bool(
        active is not None
        and active.target_scene_version == scene.version
        and active.target_prose_hash == expected_prose_hash
        and (active.target_pass or None) == (target_pass or None)
        and (active.feedback or None) == (feedback or None)
    )

    # Ambiguity: more than one active, non-stale APPROVED ScenePacket for this slot (stale duplicates
    # don't count). Stored status is the plain string "approved" (see conftest.seed_scene_packet).
    ambiguous = False
    if chapter is not None:
        approved_count = (
            await session.execute(
                select(func.count())
                .select_from(ScenePacket)
                .where(
                    ScenePacket.chapter_id == scene.chapter_id,
                    ScenePacket.scene_no == scene.scene_no,
                    ScenePacket.status == "approved",
                    ScenePacket.stale_reason.is_(None),
                )
            )
        ).scalar_one()
        ambiguous = approved_count > 1

    facts = RevisionFacts(
        scene_exists=True,  # the seam takes a resolved Scene; callers 404 on a missing id first
        chapter_exists=chapter is not None,
        ownership_ok=book is not None,
        scene_superseded=scene.status == SceneStatus.SUPERSEDED,
        expected_prose_hash=expected_prose_hash,
        current_prose_hash=current_hash,
        source_present=bool((scene.prose or "").strip()),
        pass_supported=target_pass is None or target_pass in (await _supported_passes()),
        ambiguous_active_contract=ambiguous,
        active_request_status=(RevisionRequestStatus(active.status) if active is not None else None),
        active_is_exact_replay=is_replay,
    )

    decision = classify_revision(facts)

    if decision.outcome == RevisionOutcome.NOT_FOUND:
        raise HTTPException(status_code=404, detail={"reason": decision.reason})
    if decision.outcome == RevisionOutcome.UNPROCESSABLE:
        raise HTTPException(status_code=422, detail={"reason": decision.reason})
    if decision.outcome == RevisionOutcome.CONFLICT:
        raise HTTPException(status_code=409, detail={"blockers": [{"reason": decision.reason}]})
    if decision.outcome == RevisionOutcome.REPLAY_EXISTING:
        assert active is not None  # REPLAY_EXISTING is only returned when an active request exists
        # No new request, and no Job minted HERE. The coordinator still reconciles adoption entry for
        # this replay (D5) — otherwise a reconciliation-restored request plus an `awaiting_start`
        # adoption would leave a fresh explicit Revise click stuck behind operator Start.
        return AcceptResult(request=active, disposition=RequestDisposition.REPLAYED, job_minted=False)

    # ACCEPTED_REPLACEMENT: supersede the active request and cancel its still-queued (unclaimed) job.
    # A job only leaves QUEUED by being claimed (-> RUNNING), so QUEUED == unclaimed; deleting it is a
    # true cancel (there is no JobStatus.CANCELLED to set).
    disposition = RequestDisposition.CREATED
    if decision.outcome == RevisionOutcome.ACCEPTED_REPLACEMENT and active is not None:
        disposition = RequestDisposition.REPLACED
        active.status = RevisionRequestStatus.SUPERSEDED.value
        if active.job_id is not None:
            job = await session.get(Job, active.job_id)
            if job is not None and job.status == JobStatus.QUEUED:
                await session.delete(job)

    assert chapter is not None and book is not None  # guaranteed: ownership_ok gated the 409 above

    # Source Approval + durable request, together (feedback lives immutably on the request, ADR 0028).
    approval = Approval(
        scene_id=scene.id, version=scene.version, decision=Decision.REVISE, target_pass=target_pass, feedback=feedback
    )
    session.add(approval)
    await session.flush()

    request = RevisionRequest(
        book_id=book.id,
        chapter_id=chapter.id,
        target_scene_id=scene.id,
        scene_no=scene.scene_no,
        target_scene_version=scene.version,
        target_prose_hash=current_hash,
        feedback=feedback,
        target_pass=target_pass,
        origin=origin.value,
        status=RevisionRequestStatus.AWAITING_CONTRACT.value,
        approval_id=approval.id,
    )
    session.add(request)
    await session.flush()

    # If a contract already exists, mint the explicit revise Job now and link it (D8) — the request
    # advances to queued. If not (imported/uncontracted), it stays awaiting_contract: durable, not a
    # 409, and the coordinator then routes it into adoption entry (D4).
    minted = await _mint_and_queue_revision(session, request=request, scene=scene)

    return AcceptResult(request=request, disposition=disposition, job_minted=minted is not None)


async def _mint_and_queue_revision(
    session: AsyncSession, *, request: RevisionRequest, scene: Scene
) -> uuid.UUID | None:
    """Mint the explicit linked revise Job for `scene` and advance `request` -> queued, in ONE place.

    `schedule_revision` is contract-first: it mints a Job only when the scene's Beat is backed by an
    approved ScenePacket, otherwise it returns a `revision_contract_required` blocker (or None if the
    chapter vanished). On a minted Job the request advances awaiting_contract -> queued and links it;
    on a refusal the request is left untouched (still awaiting_contract). Shared by the Slice-2 accept
    path (a scene that already has a contract) and the Slice-3b adoption resume (a scene that just got
    one), so the mint + advance logic is never duplicated. Returns the minted job id, or None.
    """
    minted = await schedule_revision(session, scene, target_pass=request.target_pass, revision_request_id=request.id)
    if isinstance(minted, uuid.UUID):
        request.status = RevisionRequestStatus.QUEUED.value
        request.job_id = minted
        return minted
    return None


async def reconstruct_revision_request_locked(
    session: AsyncSession, *, scene: Scene, chapter: Chapter, approval: Approval
) -> RevisionRequest:
    """Rebuild the durable request a stranding redeploy lost, from the scene's current-row REVISE
    `Approval` (ADR-0032 D7). Revision-owned so this module stays the ONLY `RevisionRequest`
    constructor; boot reconciliation coordinates, it does not write.

    ASSUMES `run_under_chapter_workflow` is held; NEVER commits. Deliberately narrower than
    `_accept_revision_request_locked`: it classifies nothing (the caller already proved the scan
    predicate under the lock), creates NO new `Approval` — it PRESERVES the source `approval_id` —
    and mints NO Job, because reconciliation records intent without consenting to spend.

    Legacy `Approval` carries no prose hash, so the reconstructed request pins the scene's CURRENT
    prose hash. That is honest about its provenance: intent re-anchored from VERSION-level evidence
    (the approval's `version` matched the scene's), never a validated historical snapshot.
    """
    request = RevisionRequest(
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        target_scene_id=scene.id,
        scene_no=scene.scene_no,
        target_scene_version=scene.version,
        target_prose_hash=prose_hash(scene.prose),
        feedback=approval.feedback,
        target_pass=approval.target_pass,
        origin=RevisionRequestOrigin.LEGACY_RECONCILIATION.value,
        status=RevisionRequestStatus.AWAITING_CONTRACT.value,
        approval_id=approval.id,
    )
    session.add(request)
    await session.flush()
    return request


async def resume_awaiting_contract_on_approval(session: AsyncSession, *, scene_packet: ScenePacket) -> uuid.UUID | None:
    """Close the adoption loop when an adoption-derived ScenePacket is approved (ADR-0028 Slice 3b, Q2/Q18).

    An imported scene's revise landed a durable RevisionRequest at `awaiting_contract` (Slice 2). Once
    that scene's reconstructed contract — the adoption-derived ScenePacket bound to it via
    `source_scene_id` at derive — is APPROVED, this advances the waiting request `awaiting_contract` ->
    `queued` by minting the linked revise Job through the shared Slice-2 seam.

    Runs UNDER the caller's per-chapter workflow lock, AFTER beats are derived (so `schedule_revision`
    can resolve the approved packet for the source scene's beat). REVALIDATES three invariants before
    scheduling and is a fail-closed NO-OP (returns None) on any miss:
      * packet-approved   — the locked packet is APPROVED (not a raced demotion);
      * prose-hash        — the request's pinned `target_prose_hash` still matches the source scene's
                            current prose (the scene was not edited out from under the intent);
      * source-fingerprint — the producing adoption's captured `source_fingerprint` still matches the
                            chapter's current source fingerprint (the chapter did not drift post-adoption).
    The caller owns the commit and the post-commit drain kick. Returns the minted job id, or None.
    """
    if scene_packet.status != ScenePacketStatus.APPROVED:
        return None
    # Only adoption-derived packets carry a source-scene binding; an ordinary planning-path packet has
    # source_scene_id NULL and never resumes anything.
    source_scene_id = scene_packet.source_scene_id
    if source_scene_id is None:
        return None

    request = (
        await session.execute(
            select(RevisionRequest)
            .where(
                RevisionRequest.target_scene_id == source_scene_id,
                RevisionRequest.status == RevisionRequestStatus.AWAITING_CONTRACT.value,
            )
            .order_by(RevisionRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if request is None:
        return None

    scene = await session.get(Scene, source_scene_id)
    if scene is None:
        return None
    # prose-hash concurrency token: the request pinned the exact prose it was raised against; if the
    # source scene was edited since, the contract no longer matches the intent — fail closed.
    if request.target_prose_hash != prose_hash(scene.prose):
        return None

    # source-fingerprint: the adoption that produced this packet's chapter contract captured the
    # chapter's source fingerprint; if the chapter's non-superseded prose drifted since, don't resume
    # off a now-stale reconstruction.
    adoption = (
        await session.execute(
            select(ImportAdoption)
            .where(
                ImportAdoption.chapter_packet_id == scene_packet.chapter_packet_id,
                ImportAdoption.status == ImportAdoptionStatus.CONTRACT_PROPOSED.value,
            )
            .order_by(ImportAdoption.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if adoption is None:
        return None
    rows = (
        await session.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == scene_packet.chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    current_fingerprint = chapter_source_fingerprint((int(r[0]), r[1], int(r[2]), r[3]) for r in rows)
    if current_fingerprint != adoption.source_fingerprint:
        return None

    # All three invariants hold: link the serving adoption and advance to queued via the shared seam.
    request.import_adoption_id = adoption.id
    return await _mint_and_queue_revision(session, request=request, scene=scene)


async def mirror_job_status_to_request(
    session: AsyncSession, *, revision_request_id: uuid.UUID | None, request_status: RevisionRequestStatus
) -> None:
    """Mirror a revise Job's lifecycle onto its linked RevisionRequest (ADR 0028): claim -> running,
    done -> completed, failed -> failed. This is what makes the taxonomy's `revision_in_progress`
    conflict reachable — a second, DIFFERENT revise while one is RUNNING is refused (409) rather than
    silently superseded into a concurrent job — and it lets a FAILED request be retried rather than
    stay stuck. No-op for a job with no link (a draft, or a revise minted before durable requests). A
    bare UPDATE, so it is safe on the worker's post-rollback failure path where the Job ORM is expired.
    """
    if revision_request_id is None:
        return
    await session.execute(
        update(RevisionRequest).where(RevisionRequest.id == revision_request_id).values(status=request_status.value)
    )


async def _cancel_active_requests_for_scene_locked(session: AsyncSession, scene_id: uuid.UUID) -> int:
    """Cancel every active RevisionRequest for a scene; return how many. ASSUMES the per-chapter workflow
    lock is held and NEVER commits — the committing caller (ADR-0032 W2 `accept_scene_approval`) owns the
    lock + transaction boundary, and reverse-reconciles adoption demand in the SAME locked transaction (D9).

    An inbox APPROVE makes any pending revise intent moot, and an APPROVED scene must never coexist with an
    active request — that orphan is the partial-unique-index / Slice-3 poison this closes. Deletes a
    still-QUEUED (unclaimed) job; a RUNNING job is left to land its version (killing an in-flight generation
    is out of scope), but its request is cancelled regardless."""
    active = (
        (
            await session.execute(
                select(RevisionRequest).where(
                    RevisionRequest.target_scene_id == scene_id, RevisionRequest.status.in_(_ACTIVE_STATUSES)
                )
            )
        )
        .scalars()
        .all()
    )
    for req in active:
        req.status = RevisionRequestStatus.CANCELLED.value
        if req.job_id is not None:
            job = await session.get(Job, req.job_id)
            if job is not None and job.status == JobStatus.QUEUED:
                await session.delete(job)
    return len(active)
