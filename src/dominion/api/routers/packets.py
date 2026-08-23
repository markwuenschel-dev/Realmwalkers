"""Chapter knowledge packet endpoints (contract-first drafting, Phase 1).

The packet is authored + QA'd by agents, then adjudicated and approved by the human BEFORE any prose
is drafted. This router proposes a packet (synchronous, like the gate-1 plan-call), returns it for
review, accepts human edits, and gates approval: a blocked or red-confidence packet, or one with open
questions still outstanding, cannot be approved. (Later phases block drafting until approval.)
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from dominion.api.deps import SessionDep
from dominion.api.packet_delete import hard_delete_chapter_packets
from dominion.shared.chapter_lock import (
    BUSY_DETAIL,
    DEFAULT_LOCK_TIMEOUT_MS,
    ChapterWorkflowBusy,
    is_lock_timeout,
    run_under_chapter_workflow,
)
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    ChapterPacketApprovalSource,
    ImportAdoptionMode,
    PacketConfidence,
    PacketStatus,
)
from dominion.shared.models import Chapter, ChapterPacket
from dominion.shared.schemas import DeleteChapterPacketOut, PacketOut, PacketProposeOut, PacketUpdateIn
from dominion.workers import background_work, progress
from dominion.workers import packet as packet_pipeline
from dominion.workers.packet import amendment, master
from dominion.workers.packet import approval_policy as packet_approval
from dominion.workers.packet import open_questions as open_questions_policy
from dominion.workers.packet.surface_contract import build_surface_contract
from dominion.workers.packet.validation import evaluate_chapter_packet_internal
from dominion.workers.scene_packet import staleness as packet_staleness

log = structlog.get_logger()
router = APIRouter(prefix="/chapters", tags=["packets"])

# Module attribute (not the constant directly) so tests can shorten the busy wait, matching
# `routers/adoption.py:62` and `routers/scene_packets.py:71`.
LOCK_TIMEOUT_MS: int | None = DEFAULT_LOCK_TIMEOUT_MS


async def _latest(session: SessionDep, chapter_id: uuid.UUID) -> ChapterPacket | None:
    """Latest packet for the chapter.

    `populate_existing` is defensive, not load-bearing on today's HTTP paths: `deps.db_session` hands
    each request a fresh session and this is its first read, so there is no pre-lock instance in the
    identity map to go stale. It matters for same-session in-process callers (the direct-call tests,
    and any future internal caller that loads the row before taking the lock) — for those, a bare ORM
    SELECT returns the identity-mapped instance with its PRE-LOCK values and the reload-under-the-lock
    step of the chapter_lock protocol would silently do nothing."""
    return (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id)
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _run_propose(chapter_id: uuid.UUID) -> None:
    """Background author+QA for one chapter, on its own session+commit (the request that scheduled it
    has already returned). Fail-closed internally, so a malformed/timed-out agent still persists a
    blocked packet."""
    key = str(chapter_id)
    try:
        async with SessionFactory() as session:
            chapter = await session.get(Chapter, chapter_id)
            if chapter is not None:
                await packet_pipeline.propose_packet(session, chapter=chapter, progress_key=key)
                await session.commit()
    except ChapterWorkflowBusy:
        # #259: the packet WRITE now takes the chapter lock, so this background pass can lose the race
        # against a concurrent approve/update/delete/adoption-publish. `packet._persist` already
        # retries the acquire a bounded number of times, so reaching here means the chapter stayed busy
        # throughout — a transient, operator-retryable condition, NOT the crash the blanket handler
        # below reports. Logged distinctly so it is diagnosable rather than filed as a generic failure.
        # HONEST LIMIT: there is no durable operator-facing signal for this. `progress` is cleared by
        # `background_work.schedule`'s `finish()` the moment this returns, so the Desk sees only
        # `running=False` with no new packet; the operator re-triggers propose.
        log.warning("packet.propose_chapter_busy", chapter=key)
    except Exception as exc:  # noqa: BLE001 — never let a background crash strand the in-flight slot
        log.error("packet.propose_bg_failed", chapter=key, error=str(exc))


@router.post("/{chapter_id}/packet", response_model=PacketProposeOut)
async def propose_packet(chapter_id: uuid.UUID, background: BackgroundTasks, session: SessionDep) -> PacketProposeOut:
    """Kick off the Packet Author + Packet QA in the BACKGROUND and return immediately.

    The author call alone runs ~1-2 min, so blocking the request left the browser spinning and lost
    the work on a tab switch. Now the run lives in the API process; the Desk polls `.../packet/status`
    for the live phase ('authoring' -> 'qa') and refetches the packet when it finishes. Single-flight:
    a re-trigger while one is already running just reports the in-flight status."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    key = str(chapter_id)
    background_work.schedule(background, key, "authoring", lambda: _run_propose(chapter_id))
    phase, elapsed_s = progress.get(key)
    return PacketProposeOut(
        running=background_work.is_running(key),
        phase=phase or "authoring",
        elapsed_s=elapsed_s,
    )


@router.get("/{chapter_id}/packet/status", response_model=PacketProposeOut)
async def packet_status(chapter_id: uuid.UUID) -> PacketProposeOut:
    """Live status of a background proposal so the Desk (any tab) can rejoin a run in progress.
    `running` is False once the packet is persisted — the cue to GET the packet."""
    key = str(chapter_id)
    phase, elapsed_s = progress.get(key)
    return PacketProposeOut(running=background_work.is_running(key), phase=phase, elapsed_s=elapsed_s)


@router.get("/{chapter_id}/packet", response_model=PacketOut)
async def get_packet(chapter_id: uuid.UUID, session: SessionDep) -> PacketOut:
    """The chapter's NEWEST packet by `created_at` — which is NOT necessarily the one holding authority.

    `_latest` applies no status filter, so once an amendment is proposed (#261) this returns the proposed
    amendment while the APPROVED predecessor is still the chapter's authority. That read-vs-authority
    split is deliberate here (the review surface must be able to see the artifact awaiting review), and
    it is the caller's job to distinguish them: `status`, `origin_mode`, and `supersedes_packet_id` on
    `PacketOut` say exactly which row this is and what it would replace. The governing contract is
    `GET /chapters/{chapter_id}/packet/authority`."""
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    return packet_approval.enrich_packet_out(row)


@router.get("/{chapter_id}/packet/authority", response_model=PacketOut)
async def get_packet_authority(chapter_id: uuid.UUID, session: SessionDep) -> PacketOut:
    """The chapter's governing ChapterPacket: the unique `status=approved` row, or 404."""
    row = await packet_pipeline.latest_approved(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no approved packet for this chapter")
    return packet_approval.enrich_packet_out(row)


@router.put("/{chapter_id}/packet", response_model=PacketOut)
async def update_packet(chapter_id: uuid.UUID, body: PacketUpdateIn, session: SessionDep) -> PacketOut:
    """Human edit/adjudication: replace the body, clear open questions, and/or raise confidence after
    reviewing flags. An edited body is normalized to the canonical chapter_master_packet shape and its
    derived `_surface_contract` projection is rebuilt from the edited seeds (so scene-packet derivation
    never reads a stale projection); open questions live in the body's chapter_contract with the
    sibling column written as a derived sync. A blocked packet can be edited but stays blocked until
    re-proposed.

    Runs under the chapter workflow lock (ADR-0028, #259): open questions and confidence are
    approval-gating inputs, so an edit must not interleave with an approve or a re-propose. The
    chapter id is a path parameter, so the lock is taken BEFORE the row is read — a busy chapter
    never reaches its rows."""

    async def _body() -> ChapterPacket:
        return await _update_packet_locked(session, chapter_id, body)

    try:
        row = await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS)
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    except DBAPIError as exc:
        # `SET LOCAL lock_timeout` from the acquire applies for the REST of the transaction, so a ROW
        # lock taken later in the body can time out too — as a bare 55P03, not ChapterWorkflowBusy.
        # Same operator-visible condition (something else holds a lock; retry), so same retryable 409
        # rather than a 500. Without this, `delete_packet`'s cascade purging a draft Job that a running
        # worker holds for minutes would 500 where it previously blocked and then succeeded.
        if not is_lock_timeout(exc):
            raise
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    await session.refresh(row)  # post-commit, outside the lock body
    return packet_approval.enrich_packet_out(row)


async def _update_packet_locked(session: SessionDep, chapter_id: uuid.UUID, body: PacketUpdateIn) -> ChapterPacket:
    """The update transition, assuming the chapter workflow lock is held. Never commits — the wrapper
    owns the commit boundary (`shared/chapter_lock.py:116-121`)."""
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")

    # ---- #277 clause B: compare-and-set on open_questions, BEFORE any mutation -------------------
    # The resolve path is a whole-object read-modify-write from a client snapshot, and the per-chapter
    # advisory lock serializes COMMITS, not snapshots. Under fail-closed semantics that asymmetry is not
    # symmetric in cost: losing a RULING is safe (the item stays open), but losing an ITEM grants
    # approval. So a write that supplies open_questions must prove which state it was computed against.
    normalized_oq: dict[str, Any] | None = None
    if body.open_questions is not None:
        # A REAL row lock. `_latest` passes populate_existing but no `with_for_update`, so without this
        # the compare-and-set could read a snapshot another transaction is already rewriting. House
        # pattern: `scene_packet/blockers.py:98-105`.
        row = await session.get(ChapterPacket, row.id, populate_existing=True, with_for_update=True)
        if row is None:  # pragma: no cover - the row was read moments ago in this transaction
            raise HTTPException(status_code=404, detail="no packet for this chapter yet")
        current_token = packet_approval.open_questions_state_token(row)
        supplied = (body.expected_open_questions_token or "").strip()
        try:
            # NOT timestamp-stripped: this value is compared against `current_token`, which is computed
            # from the stored row WITH its server timestamps. Stripping here would guarantee a mismatch
            # for any row that already carries a ruling, and the idempotent-replay branch below could
            # never fire. A client that fakes an `at` only makes its own request match the state that
            # already exists, which is precisely the no-op case.
            submitted = open_questions_policy.normalize(body.open_questions, mint=False)
        except open_questions_policy.OpenQuestionsInvalid as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason": "open_questions_malformed", "message": f"{exc} Nothing was changed."},
            ) from exc
        if not supplied:
            # 422 for absent, 409 for stale — the same split `revision_taxonomy.py:96-97,108-109`
            # already ruled for `expected_prose_hash`, reused rather than reinvented.
            raise HTTPException(
                status_code=422,
                detail={
                    "reason": "open_questions_token_required",
                    "message": (
                        "A write that changes open questions must echo the `open_questions_token` it read, "
                        "so the server can prove the ruling applies to the state it is replacing. "
                        "Refetch the packet and retry. Nothing was changed."
                    ),
                },
            )
        if supplied != current_token:
            if open_questions_policy.state_token(submitted) == current_token:
                # Idempotent replay: the token is stale, but the submitted value IS the current state, so
                # this request already succeeded and is being delivered twice. A no-op 200 is correct —
                # rejecting it would make a safe retry look like a conflict.
                return row
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "open_questions_stale",
                    "message": (
                        "Someone else changed this chapter's open questions after you loaded them, so this "
                        "write was refused rather than applied on top of state you never saw — it could "
                        "have erased a question you did not know about. NOTHING WAS CHANGED. Refetch and "
                        "check whether your ruling already landed."
                    ),
                },
            )
        try:
            stored = open_questions_policy.normalize(row.open_questions, mint=False)
            normalized_oq = open_questions_policy.normalize(
                open_questions_policy.strip_client_timestamps(body.open_questions),
                mint=True,
                previous=open_questions_policy.stored_ruling_times(stored),
            )
        except open_questions_policy.OpenQuestionsInvalid as exc:
            raise HTTPException(
                status_code=422,
                detail={"reason": "open_questions_malformed", "message": f"{exc} Nothing was changed."},
            ) from exc

    body_changed = False
    if body.body is not None:
        # Stamp ids on any seeds the human added so they stay linkable once derived; existing ids are
        # preserved (reassign, not in-place mutate, so SQLAlchemy flags the JSONB change).
        new_body = body.body
        packet_pipeline.mint_seed_ids(new_body)
        # Same deterministic pipeline as propose: roster normalization -> canonical shape -> fresh
        # surface projection. A human edit can introduce roster contradictions or surface leaks; those
        # become repair/warn violations on the packet (a dict body can never hard-block here).
        internal = evaluate_chapter_packet_internal(new_body)
        canonical = master.to_master_packet(
            internal.normalized_body,
            open_questions=normalized_oq if normalized_oq is not None else row.open_questions,
            book_id=row.book_id,
            chapter_id=row.chapter_id,
            status=row.status,
        )
        surface = build_surface_contract({k: v for k, v in canonical.items() if k != "_surface_contract"})
        canonical["_surface_contract"] = surface.surface_body
        edit_violations = [
            *(v.as_dict() for v in internal.violations),
            *(v.as_dict() for v in surface.violations),
            *master.validate_master_packet(canonical),
        ]
        qa_warnings = dict(row.qa_warnings or {})
        if edit_violations:
            qa_warnings["violations"] = edit_violations
        else:
            qa_warnings.pop("violations", None)
        row.qa_warnings = qa_warnings
        body_changed = canonical != row.body
        row.body = canonical
        row.open_questions = canonical["chapter_contract"]["open_questions"]
    elif normalized_oq is not None:
        # THE D7 REPAIR. This used to be `row.open_questions = body.open_questions` — a raw assignment
        # that skipped the normalizer entirely, so a single PUT of `{"open_questions": {"items": "x"}}`
        # made the gate's `isinstance` check fall through to `[]` and OPENED chapter approval, while the
        # body mirror still held the real state. Both write paths now persist ONE normalized value,
        # derived once above, to the column AND the body mirror in the same transaction.
        row.open_questions = normalized_oq
        row.body = master.with_open_questions(row.body, normalized_oq)
    if body.confidence is not None:
        try:
            row.confidence = PacketConfidence(body.confidence.strip().lower())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="confidence must be green|yellow|red") from exc
    # A chapter-packet body edit can invalidate already-derived scene packets — mark drifted ones
    # stale so they block drafting until re-derived/re-approved (staleness detection).
    if body_changed:
        await packet_staleness.recompute_and_mark(session, chapter_id=row.chapter_id)
    return row


@router.delete("/{chapter_id}/packet", response_model=DeleteChapterPacketOut)
async def delete_packet(chapter_id: uuid.UUID, session: SessionDep) -> DeleteChapterPacketOut:
    """Clear the chapter packet and all derived scene packets for this chapter.

    Runs under the chapter workflow lock (ADR-0028, #259) — the widest blast radius of the four
    transitions: it cascades to ScenePackets, purges their draft Jobs, and detaches Beat/Scene/Job/
    Critique/DraftAttempt references. NOTE the honest limit: the drafting drain claims jobs with
    `FOR UPDATE SKIP LOCKED` and does NOT take this lock, so a draft already running is not serialized
    against the purge — this lock serializes chapter-tier authority writes, not job execution."""

    async def _body() -> tuple[int, int]:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="chapter not found")
        if await _latest(session, chapter_id) is None:
            raise HTTPException(status_code=404, detail="no packet for this chapter yet")
        return await hard_delete_chapter_packets(session, chapter_id)

    try:
        cp_deleted, sp_deleted = await run_under_chapter_workflow(
            session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS
        )
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    except DBAPIError as exc:
        # `SET LOCAL lock_timeout` from the acquire applies for the REST of the transaction, so a ROW
        # lock taken later in the body can time out too — as a bare 55P03, not ChapterWorkflowBusy.
        # Same operator-visible condition (something else holds a lock; retry), so same retryable 409
        # rather than a 500. Without this, `delete_packet`'s cascade purging a draft Job that a running
        # worker holds for minutes would 500 where it previously blocked and then succeeded.
        if not is_lock_timeout(exc):
            raise
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    log.info("packet.deleted", chapter=str(chapter_id), chapter_packets=cp_deleted, scene_packets=sp_deleted)
    return DeleteChapterPacketOut(deleted_chapter_packets=cp_deleted, deleted_scene_packets=sp_deleted)


@router.post("/{chapter_id}/packet/approve", response_model=PacketOut)
async def approve_packet(chapter_id: uuid.UUID, session: SessionDep) -> PacketOut:
    """Approve the packet so drafting may proceed. Refused only when the packet is blocked or open
    questions remain; confidence and QA verdicts are advisory, so a red/repair-laden packet approves
    (approve-with-repairs — repairs still gate final export). No auto-approve during tuning: even a
    green packet needs this human action.

    Runs under the chapter workflow lock (ADR-0028, #259). The `can_approve` gate is evaluated INSIDE
    the lock with the row reloaded, so the gate and the write are atomic: previously a concurrent
    `update_packet` could add an open question between the check and the commit, approving a packet
    that was no longer approvable.

    REFUSES a proposed AMENDMENT (`409 amendment_requires_amendment_approval`, #261). `_latest` resolves
    by recency with no status filter, so once an amendment is proposed it IS the newest row and this route
    would otherwise approve it while leaving its predecessor approved — bypassing the supersede + scene
    staling that approving an amendment means. Until this guard, that failed closed only by accident:
    `uq_chapter_packets_active_chapter` rejected the second approved row and the author got a raw 500."""

    async def _body() -> ChapterPacket:
        row = await _latest(session, chapter_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no packet for this chapter yet")
        # Checked HERE — inside the locked body, on the post-lock reload (`_latest` uses
        # `populate_existing`) — not on a pre-lock read: an amendment can be published by the adoption
        # worker between a caller's read and this transaction, and a guard that ran outside the lock would
        # miss exactly that interleaving. Ordered BEFORE `can_approve` on purpose: this is a wrong-endpoint
        # error, so it stays true even after the author clears every blocker the gate would report.
        if (
            str(row.origin_mode) == ImportAdoptionMode.AMENDMENT.value
            and str(row.status) == PacketStatus.PROPOSED.value
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "amendment_requires_amendment_approval",
                    "message": (
                        "This chapter's newest packet is a proposed AMENDMENT, and an amendment cannot be "
                        "approved here. Approving one must supersede the contract it replaces and mark the "
                        "scene contracts derived from that predecessor stale — in the SAME transaction — "
                        "which this route does not do. Use POST /chapters/"
                        f"{chapter_id}/packet/{row.id}/approve-amendment instead."
                    ),
                },
            )
        if refusal := packet_approval.can_approve(row):
            raise HTTPException(status_code=409, detail=refusal.detail)
        # Delegate the WRITE to the one shared authority transition (#261) instead of setting the status
        # here. An ordinary approve is the degenerate case of that transition — no predecessor to supersede
        # and no children to stale — so routing through it keeps exactly ONE function that moves a
        # ChapterPacket into `approved`. Two write sites is how "two approved packets" becomes reachable
        # again, and a route-level guard alone would not have recorded the approval PROVENANCE: before this,
        # every normally-approved packet landed with `approval_source`/`approved_at` NULL, which is the same
        # shape `migrations._BACKFILLS` uses to mean "approved before provenance existed" — so a reader could
        # not tell a fresh deliberate approval from an unproven legacy one.
        outcome = await amendment.apply_authority_locked(
            session,
            chapter_id=chapter_id,
            packet_id=row.id,
            approval_source=ChapterPacketApprovalSource.MANUAL_COMMAND,
            expect_amendment=False,
        )
        refreshed = await session.get(ChapterPacket, outcome.packet_id, populate_existing=True)
        if refreshed is None:  # pragma: no cover - the row was just written in this transaction
            raise HTTPException(status_code=404, detail="no packet for this chapter yet")
        return refreshed

    try:
        row = await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS)
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    except DBAPIError as exc:
        # `SET LOCAL lock_timeout` from the acquire applies for the REST of the transaction, so a ROW
        # lock taken later in the body can time out too — as a bare 55P03, not ChapterWorkflowBusy.
        # Same operator-visible condition (something else holds a lock; retry), so same retryable 409
        # rather than a 500. Without this, `delete_packet`'s cascade purging a draft Job that a running
        # worker holds for minutes would 500 where it previously blocked and then succeeded.
        if not is_lock_timeout(exc):
            raise
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    await session.refresh(row)  # post-commit, outside the lock body
    # Scene-packet contract system: chapter-packet approval no longer derives beats directly. The
    # human next derives ScenePackets (POST .../scene-packets/derive), approves them, and beats are
    # derived from the approved ScenePackets — the writer drafts against the scene-local contract.
    log.info("packet.approved", chapter=str(chapter_id), packet=str(row.id))
    return packet_approval.enrich_packet_out(row)


@router.post("/{chapter_id}/packet/{packet_id}/approve-amendment", response_model=PacketOut)
async def approve_amendment_packet(chapter_id: uuid.UUID, packet_id: uuid.UUID, session: SessionDep) -> PacketOut:
    """Approve an AMENDMENT packet and supersede its predecessor as one chapter-locked transaction (#261).

    Distinct from `POST .../packet/approve` above only in what it takes on: the amendment names a
    predecessor, so approving it hands chapter authority over — the predecessor becomes `superseded` and
    the ScenePackets derived from it are marked stale for re-derivation. It is NOT a second approval seam:
    both routes funnel into `workers/packet/amendment._apply_authority_locked`, and an ordinary approve is
    the degenerate case with no predecessor.

    The packet id is explicit in the path (rather than "the latest packet" as the ordinary route uses)
    because the author is approving one reviewed artifact — resolving it by recency would let a
    concurrently-published packet be approved in its place.

    Fails CLOSED: the eligibility verdict, the prose fingerprint, and the predecessor's authority are all
    re-checked under the lock, and any drift refuses with nothing written (`409
    amendment_source_drifted`). Idempotent — an already-approved amendment returns its current state.
    """
    try:
        outcome = await amendment.approve_amendment(
            session,
            chapter_id=chapter_id,
            packet_id=packet_id,
            timeout_ms=LOCK_TIMEOUT_MS,
        )
    except amendment.AmendmentChapterNotFound as exc:
        raise HTTPException(status_code=404, detail="chapter not found") from exc
    except amendment.AmendmentPacketNotFound as exc:
        raise HTTPException(status_code=404, detail="no such chapter packet for this chapter") from exc
    except amendment.AmendmentNotEligible as exc:
        # Same token + same sentence as the eligibility preflight and `.../amendment/start`, both sourced
        # from `amendment.REFUSAL_MESSAGES`, so one condition never grows two operator-facing messages.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": exc.reason,
                "message": amendment.REFUSAL_MESSAGES.get(exc.reason) or str(exc),
            },
        ) from exc
    except amendment.AmendmentSourceDrifted as exc:
        # Invariant 4. The message must say NOTHING CHANGED, because the operator's next move depends on
        # it: this is not a partial write to clean up, it is a refusal to promote an amendment authored
        # against prose that no longer exists. The fingerprints ride along as extra keys for the Desk's
        # diff, never in place of the sentence.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "amendment_source_drifted",
                "message": (
                    "The chapter's prose changed after this amendment was authored, so NOTHING was "
                    "changed — the approved contract and every scene packet are exactly as they were. "
                    "Re-run the amendment against the current prose, then approve that one."
                ),
                "expected": exc.expected,
                "actual": exc.actual,
            },
        ) from exc
    except amendment.AmendmentPredecessorMissing as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "amendment_predecessor_missing",
                "message": (
                    "This amendment no longer has an approved predecessor to replace — another operation "
                    "changed the chapter's contract first. Nothing was changed; re-check the chapter's "
                    "amendment eligibility and re-run the amendment."
                ),
            },
        ) from exc
    except ChapterWorkflowBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc
    except DBAPIError as exc:
        # `SET LOCAL lock_timeout` from the acquire applies for the REST of the transaction, so a ROW lock
        # taken later in the body (the packet/predecessor reloads, the `FOR UPDATE` over the scene packets
        # being staled) can time out too — as a bare 55P03, not ChapterWorkflowBusy. Same retryable 409.
        if not is_lock_timeout(exc):
            raise
        raise HTTPException(status_code=409, detail=BUSY_DETAIL) from exc

    # Post-commit, outside the lock. `populate_existing` is load-bearing here for the same reason it is
    # inside the transition: `expire_on_commit=False` (shared/db.py:22) means a bare `session.get` would
    # hand back the identity-mapped instance untouched, so the lineage/provenance columns the transition
    # wrote — and the predecessor's — are read from the authoritative row, not from memory.
    row = await session.get(ChapterPacket, outcome.packet_id, populate_existing=True)
    if row is None:  # pragma: no cover — the row was just committed under the chapter lock
        raise HTTPException(status_code=404, detail="no such chapter packet for this chapter")
    log.info(
        "packet.amendment_approved",
        chapter=str(chapter_id),
        packet=str(outcome.packet_id),
        superseded=str(outcome.superseded_packet_id) if outcome.superseded_packet_id else None,
        staled_scene_packets=len(outcome.staled_scene_packet_ids),
        approval_source=outcome.approval_source,
        already_approved=outcome.was_already_approved,
    )
    return packet_approval.enrich_packet_out(row)
