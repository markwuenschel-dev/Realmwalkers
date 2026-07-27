"""Chapter knowledge packet endpoints (contract-first drafting, Phase 1).

The packet is authored + QA'd by agents, then adjudicated and approved by the human BEFORE any prose
is drafted. This router proposes a packet (synchronous, like the gate-1 plan-call), returns it for
review, accepts human edits, and gates approval: a blocked or red-confidence packet, or one with open
questions still outstanding, cannot be approved. (Later phases block drafting until approval.)
"""

from __future__ import annotations

import uuid

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
from dominion.shared.enums import PacketConfidence, PacketStatus
from dominion.shared.models import Chapter, ChapterPacket
from dominion.shared.schemas import DeleteChapterPacketOut, PacketOut, PacketProposeOut, PacketUpdateIn
from dominion.workers import background_work, progress
from dominion.workers import packet as packet_pipeline
from dominion.workers.packet import approval_policy as packet_approval
from dominion.workers.packet import master
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
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
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
            open_questions=body.open_questions if body.open_questions is not None else row.open_questions,
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
    elif body.open_questions is not None:
        row.open_questions = body.open_questions
        # Keep the body's canonical section in sync (no-op for legacy bodies — the column stays their
        # adjudicated source until the next body edit / propose canonicalizes them).
        row.body = master.with_open_questions(row.body, body.open_questions)
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
    that was no longer approvable."""

    async def _body() -> ChapterPacket:
        row = await _latest(session, chapter_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no packet for this chapter yet")
        if refusal := packet_approval.can_approve(row):
            raise HTTPException(status_code=409, detail=refusal.detail)
        row.status = PacketStatus.APPROVED
        # Keep the canonical artifact's lifecycle mirror truthful (body.status mirrors the column; the
        # column stays the operational gate). Legacy bodies are left untouched.
        if isinstance(row.body, dict) and row.body.get("schema_version"):
            row.body = {**row.body, "status": PacketStatus.APPROVED.value}
        return row

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
