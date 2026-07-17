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
from dominion.shared.models import ApprovalBlocker, ChapterPacket, Job, Scene, ScenePacket
from dominion.shared.schemas import (
    ApprovalBlockerOut,
    ApprovalBlockerRaiseIn,
    ApprovalBlockerResolveIn,
    DeleteScenePacketOut,
    DeleteScenePacketsOut,
    DraftReadinessOut,
    FidelityAcceptIn,
    FidelityRequirementActionIn,
    FidelityViolationOut,
    ScenePacketApproveIn,
    ScenePacketDeriveOut,
    ScenePacketDeriveStatusOut,
    ScenePacketFidelityOut,
    ScenePacketOut,
    ScenePacketQaOut,
    ScenePacketSummaryOut,
    ScenePacketUpdateIn,
)
from dominion.workers import background_work, progress
from dominion.workers import scene_packet as scene_packet_pipeline
from dominion.workers.budget import TokenBudget
from dominion.workers.draft_readiness import compute_draft_readiness
from dominion.workers.llm import LlmRateLimited, PromptBudgetExceeded
from dominion.workers.scene_fidelity import (
    active_requirements,
    fidelity_contract_fingerprint,
    validate_active_requirements,
)
from dominion.workers.scene_packet import blockers as _blockers

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
    return await scene_packet_pipeline.latest_approved_chapter_packet(session, chapter_id)


async def _run_derive(chapter_id: uuid.UUID) -> None:
    """Background derive for one chapter, on its own session+commit (the request already returned).
    The ScenePacket Author + QA run once per scene, so a 12-scene chapter is ~25 LLM calls — far too
    long to block the request. Beats are reconciled afterwards so a re-derive also prunes orphaned
    beats (legacy beat-first rows, beats of no-longer-approved packets) that would otherwise hold the
    draft gate as "unlinked" forever."""
    try:
        async with SessionFactory() as session:
            cp = await _latest_approved_chapter_packet(session, chapter_id)
            if cp is not None:
                counts = await scene_packet_pipeline.derive_scene_packets(session, chapter_packet=cp)
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
    if refusal := scene_packet_pipeline.can_derive_scene_packets(cp):
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
        # Counts only — no packet bodies. The Desk refetches the (slim) list itself when the derive
        # finishes; embedding every full contract here made each 1.5s status poll a ~100KB download.
        result = ScenePacketDeriveOut(
            created=counts["created"],
            updated=counts["updated"],
            blocked=counts["blocked"],
            stale=counts["stale"],
            rate_limited=counts.get("rate_limited", 0),
            skipped=counts.get("skipped", 0),
            packets=[],
            context_budget_report=counts.get("context_budget_report"),
        )
    return ScenePacketDeriveStatusOut(running=running, phase=phase, elapsed_s=elapsed_s, result=result)


async def _derive_sync(chapter_id: uuid.UUID, session: AsyncSession) -> ScenePacketDeriveOut:
    """Synchronous derive (used by tests). The HTTP route runs this in the background."""
    cp = await _latest_approved_chapter_packet(session, chapter_id)
    if refusal := scene_packet_pipeline.can_derive_scene_packets(cp):
        raise HTTPException(status_code=409, detail=refusal.detail)
    assert cp is not None  # narrowed by can_derive_scene_packets
    counts = await scene_packet_pipeline.derive_scene_packets(session, chapter_packet=cp)
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
        skipped=counts.get("skipped", 0),
        packets=await scene_packet_pipeline.scene_outs_with_blockers(session, list(rows)),
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
    return await scene_packet_pipeline.scene_outs_with_blockers(session, list(rows))


@router.post("/chapters/{chapter_id}/beats/derive", response_model=DraftReadinessOut)
async def rederive_beats(chapter_id: uuid.UUID, session: SessionDep) -> DraftReadinessOut:
    """Reconcile beats with the CURRENT approved scene packets: upsert one beat per approved packet
    and prune orphans (legacy beat-first rows, beats of no-longer-approved packets). The escape hatch
    for a gate stuck on 'N approved beats are not linked' when every packet is already approved — no
    approval state changes, so it is safe to run any time. Returns fresh readiness."""
    derived = await scene_packet_pipeline.reconcile_beats(session, chapter_id=chapter_id)
    await session.commit()
    log.info("scene_packet.beats_rederived", chapter=str(chapter_id), beats=derived)
    return await compute_draft_readiness(session, chapter_id)


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

    # Blocker-aware projection for the whole list in ONE bulk load (A1c F6): the summary's approval fields
    # come from the same fail-closed path as the detail endpoint, so a packet with an active
    # ApprovalBlocker is never advertised approvable in the list either (route parity).
    enriched_by_id = {e.id: e for e in await scene_packet_pipeline.scene_outs_with_blockers(session, list(rows))}
    out: list[ScenePacketSummaryOut] = []
    for row in rows:
        enriched = enriched_by_id[row.id]
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
                approval_state=enriched.approval_state,
                approval_blockers=enriched.approval_blockers,
                blocked_reason=enriched.blocked_reason,
                blocker_source=enriched.blocker_source,
                body_valid=scene_packet_pipeline.valid_scene_packet_body(row.body),
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
    return await scene_packet_pipeline.scene_out_with_blockers(session, row)


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
            target_status = ScenePacketStatus(explicit_status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="status must be proposed|approved|blocked|stale|rate_limited"
            ) from exc
        if target_status == ScenePacketStatus.APPROVED:
            # Even a human PUT override must go through the centralized blocker gate — never raw-approve
            # past an active ApprovalBlocker (A1c). approve_scene_packet locks the row + checks it.
            try:
                await scene_packet_pipeline.approve_scene_packet(session, packet=row)
            except _blockers.ApprovalBlockerError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            row.status = target_status
    # A body edit or explicit status change can flip this packet out of (or into) the approved set, so
    # reconcile beats — the projection follows the packet's status. Beats-only: no scene packet is
    # re-derived here.
    await scene_packet_pipeline.reconcile_beats(session, chapter_id=row.chapter_id)
    await session.commit()
    await session.refresh(row)
    return await scene_packet_pipeline.scene_out_with_blockers(session, row)


@router.post("/scene-packets/{scene_packet_id}/qa", response_model=ScenePacketQaOut)
async def qa_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketQaOut:
    """Re-run QA against the current body. QA is advisory: any usable verdict leaves status alone
    (and releases a legacy QA-held block); only a malformed response fails closed (blocks)."""
    row = await _get(session, scene_packet_id)
    if not scene_packet_pipeline.valid_scene_packet_body(row.body):
        raise HTTPException(
            status_code=409,
            detail="Cannot rerun QA: packet has no valid scene contract — re-run derive instead.",
        )
    cp_body = None
    cp = await session.get(ChapterPacket, row.chapter_packet_id)
    if cp is not None:
        cp_body = cp.body
    try:
        result = await scene_packet_pipeline.qa_scene_packet(
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
    except PromptBudgetExceeded as exc:
        # Preflight refusal, not a provider error: the chapter packet + scene body exceed the QA
        # input guard. The packet is untouched. Surface the numbers instead of a raw 500 so the
        # human can slim the chapter packet or raise DOMINION_SCENE_PACKET_QA_PROMPT_BUDGET.
        raise HTTPException(
            status_code=422,
            detail=f"QA prompt exceeds the input budget — the chapter packet is too large. ({exc})",
        ) from exc
    scene_packet_pipeline.apply_qa_rerun(row, result)
    await session.commit()
    return ScenePacketQaOut(packet_id=row.id, verdict=str(row.qa_verdict), warnings=row.qa_warnings)


@router.post("/scene-packets/{scene_packet_id}/approve", response_model=ScenePacketOut)
async def approve_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketOut:
    """Approve one ScenePacket, then derive the chapter's beats. Refused when blocked or when QA
    blocks drafting."""
    row = await _get(session, scene_packet_id)
    if refusal := scene_packet_pipeline.can_approve(row):
        raise HTTPException(status_code=409, detail=refusal.detail)
    try:
        derived = await scene_packet_pipeline.approve_scene_packet(session, packet=row)
    except _blockers.ApprovalBlockerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(row)
    log.info("scene_packet.approved", packet=str(row.id), derived_beats=derived)
    return await scene_packet_pipeline.scene_out_with_blockers(session, row)


@router.post("/scene-packets/{scene_packet_id}/blockers", response_model=ApprovalBlockerOut)
async def raise_scene_packet_blocker(
    scene_packet_id: uuid.UUID, body: ApprovalBlockerRaiseIn, session: SessionDep
) -> ApprovalBlockerOut:
    """Raise a manual_command scene-tier ApprovalBlocker (idempotent per active source_key). If the packet
    is already approved it is demoted and its beats reconciled — no approved packet may carry an active
    blocker (A1c, ADR-0031 D9/D14)."""
    try:
        blocker = await _blockers.raise_blocker(
            session, scene_packet_id=scene_packet_id, source_key=body.source_key, question=body.question
        )
    except _blockers.ApprovalBlockerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(blocker)
    return ApprovalBlockerOut.model_validate(blocker)


@router.get("/scene-packets/{scene_packet_id}/blockers", response_model=list[ApprovalBlockerOut])
async def list_scene_packet_blockers(scene_packet_id: uuid.UUID, session: SessionDep) -> list[ApprovalBlockerOut]:
    """All ApprovalBlockers for a scene packet (active + resolved history), oldest first."""
    rows = (
        (
            await session.execute(
                select(ApprovalBlocker)
                .where(ApprovalBlocker.scene_packet_id == scene_packet_id)
                .order_by(ApprovalBlocker.raised_at)
            )
        )
        .scalars()
        .all()
    )
    return [ApprovalBlockerOut.model_validate(r) for r in rows]


@router.post("/scene-packet-blockers/{blocker_id}/resolve", response_model=ApprovalBlockerOut)
async def resolve_scene_packet_blocker(
    blocker_id: uuid.UUID, body: ApprovalBlockerResolveIn, session: SessionDep
) -> ApprovalBlockerOut:
    """Explicitly resolve an active blocker (requires a nonempty rationale + source). Approval is NOT a
    resolution — this is the only way to clear one."""
    try:
        blocker = await _blockers.resolve_blocker(
            session, blocker_id=blocker_id, rationale=body.rationale, resolution_source=body.resolution_source
        )
    except _blockers.ApprovalBlockerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(blocker)
    return ApprovalBlockerOut.model_validate(blocker)


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
    approved, derived = await scene_packet_pipeline.approve_scene_packets(
        session,
        chapter_id=chapter_id,
        rows=list(rows),
        packet_ids=body.packet_ids if body else None,
    )
    await session.commit()
    # Refresh each row so its server-side `updated_at` (onupdate) is loaded before enrich serializes it
    # — otherwise model_validate triggers a sync lazy-load on the async session (MissingGreenlet).
    # Mirrors the mark-stale fix below. (N1)
    for r in rows:
        await session.refresh(r)
    log.info("scene_packet.batch_approved", chapter=str(chapter_id), approved=approved, derived_beats=derived)
    return await scene_packet_pipeline.scene_outs_with_blockers(session, list(rows))


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
    # Marking approved packets stale drops them from the approved set, so reconcile beats to prune the
    # now-orphaned beats. Beats-only: no scene packet is re-derived here.
    await scene_packet_pipeline.reconcile_beats(session, chapter_id=chapter_id)
    await session.commit()
    # Refresh after commit so each STALE update's server-side `updated_at` (onupdate) is loaded before
    # enrich serializes it — otherwise model_validate triggers a sync lazy-load on the async session
    # (MissingGreenlet). Mirrors update_scene_packet's post-commit refresh.
    for row in rows:
        await session.refresh(row)
    return await scene_packet_pipeline.scene_outs_with_blockers(session, list(rows))


# --- SceneFidelity requirement author actions (ADR 0005/0006/0016/0024) ---------------------------
# The server mints identity; the author never activates a suggestion in place. Each action re-validates
# the resulting active contract and returns decision-ready feedback. A malformed result is a 422 and is
# never persisted; a successful mutation returns an approved packet to `proposed` (re-approval required).


def _fidelity_out(row: ScenePacket) -> ScenePacketFidelityOut:
    body = row.body or {}
    violations = [FidelityViolationOut(**v.as_dict_core()) for v in validate_active_requirements(body)]
    return ScenePacketFidelityOut(
        scene_packet_id=row.id,
        active_requirements=active_requirements(body),
        suggested_requirements=[s for s in (body.get("suggested_fidelity_requirements") or []) if isinstance(s, dict)],
        fingerprint=fidelity_contract_fingerprint(body),
        violations=violations,
    )


async def _apply_fidelity_mutation(
    session: AsyncSession, row: ScenePacket, new_body, violations
) -> ScenePacketFidelityOut:
    if violations:
        raise HTTPException(status_code=422, detail=[v.as_dict() for v in violations])
    row.body = new_body
    if row.status == ScenePacketStatus.APPROVED:
        row.status = ScenePacketStatus.PROPOSED
    await scene_packet_pipeline.reconcile_beats(session, chapter_id=row.chapter_id)
    await session.commit()
    await session.refresh(row)
    return _fidelity_out(row)


@router.get("/scene-packets/{scene_packet_id}/fidelity", response_model=ScenePacketFidelityOut)
async def get_scene_packet_fidelity(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketFidelityOut:
    return _fidelity_out(await _get(session, scene_packet_id))


@router.post("/scene-packets/{scene_packet_id}/fidelity/accept", response_model=ScenePacketFidelityOut)
async def accept_fidelity_suggestions(
    scene_packet_id: uuid.UUID, body: FidelityAcceptIn, session: SessionDep
) -> ScenePacketFidelityOut:
    """Promote suggested requirements into the active contract with freshly minted identities."""
    row = await _get(session, scene_packet_id)
    new_body, violations = scene_packet_pipeline.accept_suggestions(
        row.body or {}, requirement_ids=body.requirement_ids
    )
    return await _apply_fidelity_mutation(session, row, new_body, violations)


@router.post("/scene-packets/{scene_packet_id}/fidelity/refine", response_model=ScenePacketFidelityOut)
async def refine_fidelity_requirement(
    scene_packet_id: uuid.UUID, body: FidelityRequirementActionIn, session: SessionDep
) -> ScenePacketFidelityOut:
    """Refine an active requirement in place (identity preserved; non-semantic clarification only)."""
    row = await _get(session, scene_packet_id)
    new_body, violations = scene_packet_pipeline.refine_requirement(
        row.body or {}, body.requirement_id, body.requirement
    )
    return await _apply_fidelity_mutation(session, row, new_body, violations)


@router.post("/scene-packets/{scene_packet_id}/fidelity/replace", response_model=ScenePacketFidelityOut)
async def replace_fidelity_requirement(
    scene_packet_id: uuid.UUID, body: FidelityRequirementActionIn, session: SessionDep
) -> ScenePacketFidelityOut:
    """Replace an active requirement with a freshly minted identity (mode/policy/criterion change)."""
    row = await _get(session, scene_packet_id)
    new_body, violations = scene_packet_pipeline.replace_requirement(
        row.body or {}, body.requirement_id, body.requirement
    )
    return await _apply_fidelity_mutation(session, row, new_body, violations)
