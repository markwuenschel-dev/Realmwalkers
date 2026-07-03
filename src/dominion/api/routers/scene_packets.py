"""Scene packet endpoints (scene-packet contract system).

A ScenePacket localizes an approved ChapterPacket into one scene's reader/POV/reveal/word contract.
This router derives them (Length Planner + ScenePacket Author + QA), serves them for the Desk, accepts
human edits/adjudication, re-runs QA, and gates approval — and on approval derives the chapter's Beats
from the approved ScenePackets. Drafting (elsewhere) requires an approved, non-stale ScenePacket.

Gates:
  * derive requires an APPROVED ChapterPacket (a blocked/absent one returns 409);
  * a BLOCKED ScenePacket, or one whose QA blocks drafting, cannot be approved;
  * editing a ScenePacket body after approval returns it to `proposed` unless re-approved explicitly;
  * a STALE ScenePacket blocks new draft jobs until re-derived or re-approved.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.api.packet_delete import hard_delete_scene_packet, hard_delete_scene_packets_for_chapter
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import JobKind, JobStatus, ScenePacketStatus
from dominion.shared.models import ChapterPacket, Job, Scene, ScenePacket
from dominion.shared.schemas import (
    DeleteScenePacketOut,
    DeleteScenePacketsOut,
    ScenePacketApproveIn,
    ScenePacketDeriveOut,
    ScenePacketDeriveStatusOut,
    ScenePacketOut,
    ScenePacketQaOut,
    ScenePacketSummaryOut,
    ScenePacketUpdateIn,
)
from dominion.workers import background_work, progress
from dominion.workers import packet as packet_pipeline
from dominion.workers.budget import TokenBudget
from dominion.workers.llm import LlmRateLimited
from dominion.workers.packet import approval_policy as packet_approval
from dominion.workers.scene_packet import approval_policy as sp_approval
from dominion.workers.scene_packet import beats as beats_mod
from dominion.workers.scene_packet import derive as derive_mod
from dominion.workers.scene_packet import qa as qa_mod
from dominion.workers.scene_packet.parse import valid_scene_packet_body

log = structlog.get_logger()
router = APIRouter(tags=["scene-packets"])


def _derive_key(chapter_id: uuid.UUID) -> str:
    return f"derive:{chapter_id}"


async def _get(session: AsyncSession, packet_id: uuid.UUID) -> ScenePacket:
    row = await session.get(ScenePacket, packet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scene packet not found")
    return row


async def _latest_approved_chapter_packet(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterPacket | None:
    return await packet_pipeline.latest_approved(session, chapter_id)


async def _run_derive(chapter_id: uuid.UUID) -> None:
    """Background derive for one chapter, on its own session+commit (the request already returned).
    The ScenePacket Author + QA run once per scene, so a 12-scene chapter is ~25 LLM calls — far too
    long to block the request."""
    try:
        async with SessionFactory() as session:
            cp = await _latest_approved_chapter_packet(session, chapter_id)
            if cp is not None:
                counts = await derive_mod.derive_scene_packets(session, packet=cp)
                await session.commit()
                background_work.set_derive_result(str(chapter_id), counts)
    except Exception as exc:  # noqa: BLE001 — never let a background crash strand the slot
        log.error("scene_packet.derive_bg_failed", chapter=str(chapter_id), error=str(exc))


async def _derive_task(chapter_id: uuid.UUID) -> None:
    try:
        await _run_derive(chapter_id)
    finally:
        background_work.finish(_derive_key(chapter_id))


@router.post("/chapters/{chapter_id}/scene-packets/derive", response_model=ScenePacketDeriveStatusOut)
async def derive_scene_packets(
    chapter_id: uuid.UUID, background: BackgroundTasks, session: SessionDep
) -> ScenePacketDeriveStatusOut:
    """Kick off scene-packet derivation in the BACKGROUND and return immediately. Requires an approved
    ChapterPacket (a blocked/absent one is a 409). The Desk polls `.../derive/status` for the live
    phase and refetches the list when it finishes. Single-flight: a re-trigger reports the running run."""
    cp = await _latest_approved_chapter_packet(session, chapter_id)
    if refusal := packet_approval.can_derive_scene_packets(cp):
        raise HTTPException(status_code=409, detail=refusal.detail)
    key = _derive_key(chapter_id)
    if background_work.begin_with_phase(key, "deriving"):
        background_work.pop_derive_result(str(chapter_id))
        background.add_task(_derive_task, chapter_id)
    phase, elapsed_s = progress.get(key)
    return ScenePacketDeriveStatusOut(
        running=background_work.is_running(key),
        phase=phase or "deriving",
        elapsed_s=elapsed_s,
    )


@router.get("/chapters/{chapter_id}/scene-packets/derive/status", response_model=ScenePacketDeriveStatusOut)
async def derive_status(chapter_id: uuid.UUID, session: SessionDep) -> ScenePacketDeriveStatusOut:
    """Live status of a background derive so the Desk (any tab) can rejoin a run in progress. `running`
    is False once it finishes; `result` then carries the counts."""
    key = _derive_key(chapter_id)
    phase, elapsed_s = progress.get(key)
    running = background_work.is_running(key)
    result: ScenePacketDeriveOut | None = None
    if not running and (counts := background_work.get_derive_result(str(chapter_id))) is not None:
        rows = (
            (
                await session.execute(
                    select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
                )
            )
            .scalars()
            .all()
        )
        result = ScenePacketDeriveOut(
            created=counts["created"],
            updated=counts["updated"],
            blocked=counts["blocked"],
            stale=counts["stale"],
            rate_limited=counts.get("rate_limited", 0),
            packets=[sp_approval.enrich_scene_packet_out(r) for r in rows],
            context_budget_report=counts.get("context_budget_report"),
        )
    return ScenePacketDeriveStatusOut(running=running, phase=phase, elapsed_s=elapsed_s, result=result)


async def _derive_sync(chapter_id: uuid.UUID, session: AsyncSession) -> ScenePacketDeriveOut:
    """Synchronous derive (used by tests). The HTTP route runs this in the background."""
    cp = await _latest_approved_chapter_packet(session, chapter_id)
    if refusal := packet_approval.can_derive_scene_packets(cp):
        raise HTTPException(status_code=409, detail=refusal.detail)
    assert cp is not None  # narrowed by can_derive_scene_packets
    counts = await derive_mod.derive_scene_packets(session, packet=cp)
    await session.commit()
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    return ScenePacketDeriveOut(
        created=counts["created"],
        updated=counts["updated"],
        blocked=counts["blocked"],
        stale=counts["stale"],
        rate_limited=counts.get("rate_limited", 0),
        packets=[sp_approval.enrich_scene_packet_out(r) for r in rows],
        context_budget_report=counts.get("context_budget_report"),
    )


@router.get("/chapters/{chapter_id}/scene-packets", response_model=list[ScenePacketOut])
async def list_scene_packets(chapter_id: uuid.UUID, session: SessionDep) -> list[ScenePacketOut]:
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    return [sp_approval.enrich_scene_packet_out(r) for r in rows]


@router.get("/chapters/{chapter_id}/scene-packets/summary", response_model=list[ScenePacketSummaryOut])
async def list_scene_packet_summaries(chapter_id: uuid.UUID, session: SessionDep) -> list[ScenePacketSummaryOut]:
    """Slim list rows for the Desk (statuses/counters only — no bodies, QA reports, or sources), so the
    scene-packet list renders from a small payload; full packets load per-card via GET
    /scene-packets/{id}. Includes each scene's prose state (missing/drafting/drafted/failed) so the UI
    can show contract, QA, and prose as the separate axes they are."""
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )

    scene_rows = (
        await session.execute(
            select(Scene.scene_no, Scene.prose)
            .where(Scene.chapter_id == chapter_id)
            .order_by(Scene.scene_no, Scene.created_at)
        )
    ).all()
    latest_prose: dict[int, str] = {}
    for scene_no, prose in scene_rows:  # ordered by created_at — latest row per scene_no wins
        latest_prose[scene_no] = prose or ""

    job_rows = (
        await session.execute(
            select(Job.scene_packet_id, Job.status).where(
                Job.chapter_id == chapter_id,
                Job.kind == JobKind.DRAFT,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
            )
        )
    ).all()
    active_jobs = {sp_id for sp_id, status in job_rows if sp_id and status in (JobStatus.QUEUED, JobStatus.RUNNING)}
    failed_jobs = {sp_id for sp_id, status in job_rows if sp_id and status == JobStatus.FAILED}

    def _prose_state(row: ScenePacket) -> str:
        if latest_prose.get(row.scene_no, "").strip():
            return "drafted"
        if row.id in active_jobs:
            return "drafting"
        if row.id in failed_jobs:
            return "failed"
        return "missing"

    out: list[ScenePacketSummaryOut] = []
    for row in rows:
        enriched = sp_approval.enrich_scene_packet_out(row)
        warnings = row.qa_warnings if isinstance(row.qa_warnings, dict) else {}
        raw_violations = warnings.get("violations")
        violations = raw_violations if isinstance(raw_violations, list) else []
        violation_counts: dict[str, int] = {}
        for v in violations:
            if isinstance(v, dict):
                sev = str(v.get("severity") or "warn")
                violation_counts[sev] = violation_counts.get(sev, 0) + 1
        raw_issues = warnings.get("issues")
        issues = raw_issues if isinstance(raw_issues, list) else []
        out.append(
            ScenePacketSummaryOut(
                id=row.id,
                chapter_id=row.chapter_id,
                scene_no=row.scene_no,
                status=str(row.status),
                qa_verdict=str(row.qa_verdict) if row.qa_verdict else None,
                stale_reason=row.stale_reason,
                can_approve=enriched.can_approve,
                approval_blockers=enriched.approval_blockers,
                blocked_reason=enriched.blocked_reason,
                blocker_source=enriched.blocker_source,
                body_valid=valid_scene_packet_body(row.body),
                violation_counts=violation_counts,
                issue_count=len(issues),
                prose_state=_prose_state(row),
                updated_at=row.updated_at,
            )
        )
    return out


@router.get("/scene-packets/{scene_packet_id}", response_model=ScenePacketOut)
async def get_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketOut:
    row = await _get(session, scene_packet_id)
    return sp_approval.enrich_scene_packet_out(row)


@router.put("/scene-packets/{scene_packet_id}", response_model=ScenePacketOut)
async def update_scene_packet(
    scene_packet_id: uuid.UUID, body: ScenePacketUpdateIn, session: SessionDep
) -> ScenePacketOut:
    """Human edit/adjudication. Editing the body returns an approved packet to `proposed` (re-approval
    required) unless the same call explicitly sets status back to approved."""
    row = await _get(session, scene_packet_id)
    explicit_status = (body.status or "").strip().lower() or None
    if body.body is not None:
        row.body = body.body
        if row.status == ScenePacketStatus.APPROVED and explicit_status != ScenePacketStatus.APPROVED:
            row.status = ScenePacketStatus.PROPOSED
    if explicit_status is not None:
        try:
            row.status = ScenePacketStatus(explicit_status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="status must be proposed|approved|blocked|stale|rate_limited"
            ) from exc
    await session.commit()
    await session.refresh(row)
    return sp_approval.enrich_scene_packet_out(row)


@router.post("/scene-packets/{scene_packet_id}/qa", response_model=ScenePacketQaOut)
async def qa_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketQaOut:
    """Re-run QA against the current body. QA is advisory: any usable verdict leaves status alone
    (and releases a legacy QA-held block); only a malformed response fails closed (blocks)."""
    row = await _get(session, scene_packet_id)
    if not valid_scene_packet_body(row.body):
        raise HTTPException(
            status_code=409,
            detail="Cannot rerun QA: packet has no valid scene contract — re-run derive instead.",
        )
    cp_body = None
    cp = await session.get(ChapterPacket, row.chapter_packet_id)
    if cp is not None:
        cp_body = cp.body
    try:
        result = await qa_mod.qa_scene_packet(
            row.body or {},
            chapter_packet_body=cp_body,
            budget=TokenBudget(
                max_tokens=settings.scene_packet_manual_qa_token_budget,
                hard_max_tokens=settings.scene_packet_manual_qa_hard_token_budget,
            ),
        )
    except LlmRateLimited as exc:
        # Transient provider refusal — do NOT fail the packet closed (apply_qa_rerun(None) would
        # block it as "no usable verdict"). The packet is untouched; the human just retries.
        raise HTTPException(
            status_code=429,
            detail=f"Provider rate limited the QA call — try again shortly. ({exc})",
        ) from exc
    sp_approval.apply_qa_rerun(row, result)
    await session.commit()
    return ScenePacketQaOut(packet_id=row.id, verdict=str(row.qa_verdict), warnings=row.qa_warnings)


@router.post("/scene-packets/{scene_packet_id}/approve", response_model=ScenePacketOut)
async def approve_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketOut:
    """Approve one ScenePacket, then derive the chapter's beats. Refused when blocked or when QA
    blocks drafting."""
    row = await _get(session, scene_packet_id)
    if refusal := sp_approval.can_approve(row):
        raise HTTPException(status_code=409, detail=refusal.detail)
    row.status = ScenePacketStatus.APPROVED
    derived = await beats_mod.derive_beats(session, chapter_id=row.chapter_id)
    await session.commit()
    await session.refresh(row)
    log.info("scene_packet.approved", packet=str(row.id), derived_beats=derived)
    return sp_approval.enrich_scene_packet_out(row)


@router.post("/chapters/{chapter_id}/scene-packets/approve", response_model=list[ScenePacketOut])
async def approve_scene_packets(
    chapter_id: uuid.UUID, session: SessionDep, body: ScenePacketApproveIn | None = None
) -> list[ScenePacketOut]:
    """Batch approve. Approves only packets that are not blocked and have no blocking QA issues, then
    derives the chapter's beats from the approved set."""
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=400, detail="no scene packets to approve for this chapter")
    selected = set(body.packet_ids) if body and body.packet_ids else None
    approved = 0
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        if not sp_approval.is_approvable_for_batch(row):
            continue
        row.status = ScenePacketStatus.APPROVED
        approved += 1
    derived = await beats_mod.derive_beats(session, chapter_id=chapter_id)
    await session.commit()
    log.info("scene_packet.batch_approved", chapter=str(chapter_id), approved=approved, derived_beats=derived)
    return [sp_approval.enrich_scene_packet_out(r) for r in rows]


@router.delete("/scene-packets/{scene_packet_id}", response_model=DeleteScenePacketOut)
async def delete_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> DeleteScenePacketOut:
    """Hard-delete one scene packet and detach dependent beats/jobs/scenes."""
    deleted_id, jobs_purged = await hard_delete_scene_packet(session, scene_packet_id)
    await session.commit()
    log.info("scene_packet.deleted", packet=str(deleted_id), jobs_purged=jobs_purged)
    return DeleteScenePacketOut(deleted=deleted_id, jobs_purged=jobs_purged)


@router.delete("/chapters/{chapter_id}/scene-packets", response_model=DeleteScenePacketsOut)
async def delete_scene_packets(chapter_id: uuid.UUID, session: SessionDep) -> DeleteScenePacketsOut:
    """Clear all scene packets for a chapter."""
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no scene packets for this chapter")
    deleted, jobs_purged = await hard_delete_scene_packets_for_chapter(session, chapter_id)
    await session.commit()
    log.info("scene_packet.batch_deleted", chapter=str(chapter_id), deleted=deleted, jobs_purged=jobs_purged)
    return DeleteScenePacketsOut(deleted=deleted, jobs_purged=jobs_purged)


@router.post("/chapters/{chapter_id}/scene-packets/mark-stale", response_model=list[ScenePacketOut])
async def mark_scene_packets_stale(
    chapter_id: uuid.UUID, session: SessionDep, body: ScenePacketApproveIn | None = None
) -> list[ScenePacketOut]:
    """Mark scene packets stale (optionally a subset) so they block new draft jobs until refreshed."""
    rows = (
        (
            await session.execute(
                select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )
    selected = set(body.packet_ids) if body and body.packet_ids else None
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        if row.status != ScenePacketStatus.BLOCKED:
            row.status = ScenePacketStatus.STALE
            row.stale_reason = "manually marked stale"
    await session.commit()
    return [sp_approval.enrich_scene_packet_out(r) for r in rows]
