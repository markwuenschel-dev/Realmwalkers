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
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.enums import (
    PacketStatus,
    ScenePacketStatus,
    ScenePacketVerdict,
)
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.shared.schemas import (
    ScenePacketApproveIn,
    ScenePacketDeriveOut,
    ScenePacketOut,
    ScenePacketQaOut,
    ScenePacketUpdateIn,
)
from dominion.workers.budget import TokenBudget
from dominion.workers.scene_packet import beats as beats_mod
from dominion.workers.scene_packet import derive as derive_mod
from dominion.workers.scene_packet import qa as qa_mod

log = structlog.get_logger()
router = APIRouter(tags=["scene-packets"])

_BLOCKING_VERDICTS = {ScenePacketVerdict.BLOCK_DRAFTING, ScenePacketVerdict.REVISE_REQUIRED}


def _has_blocking_qa(packet: ScenePacket) -> bool:
    if packet.qa_verdict in {v.value for v in _BLOCKING_VERDICTS}:
        return True
    issues = (packet.qa_warnings or {}).get("issues") if isinstance(packet.qa_warnings, dict) else None
    if isinstance(issues, list):
        return any(isinstance(i, dict) and i.get("severity") == "block" for i in issues)
    return False


async def _get(session: AsyncSession, packet_id: uuid.UUID) -> ScenePacket:
    row = await session.get(ScenePacket, packet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="scene packet not found")
    return row


async def _latest_approved_chapter_packet(
    session: AsyncSession, chapter_id: uuid.UUID
) -> ChapterPacket | None:
    return (await session.execute(
        select(ChapterPacket)
        .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED)
        .order_by(ChapterPacket.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


@router.post("/chapters/{chapter_id}/scene-packets/derive", response_model=ScenePacketDeriveOut)
async def derive_scene_packets(chapter_id: uuid.UUID, session: SessionDep) -> ScenePacketDeriveOut:
    """Build/refresh one ScenePacket per scene seed from the chapter's APPROVED ChapterPacket.
    Requires an approved chapter packet (a blocked/absent one is a 409 — never derive from an
    unapproved contract)."""
    cp = await _latest_approved_chapter_packet(session, chapter_id)
    if cp is None:
        raise HTTPException(
            status_code=409, detail="no approved chapter packet — approve the chapter packet first"
        )
    counts = await derive_mod.derive_scene_packets(session, packet=cp)
    await session.commit()
    rows = (await session.execute(
        select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
    )).scalars().all()
    return ScenePacketDeriveOut(
        created=counts["created"], updated=counts["updated"],
        blocked=counts["blocked"], stale=counts["stale"],
        packets=[ScenePacketOut.model_validate(r) for r in rows],
    )


@router.get("/chapters/{chapter_id}/scene-packets", response_model=list[ScenePacketOut])
async def list_scene_packets(chapter_id: uuid.UUID, session: SessionDep) -> list[ScenePacket]:
    rows = (await session.execute(
        select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
    )).scalars().all()
    return list(rows)


@router.get("/scene-packets/{scene_packet_id}", response_model=ScenePacketOut)
async def get_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacket:
    return await _get(session, scene_packet_id)


@router.put("/scene-packets/{scene_packet_id}", response_model=ScenePacketOut)
async def update_scene_packet(
    scene_packet_id: uuid.UUID, body: ScenePacketUpdateIn, session: SessionDep
) -> ScenePacket:
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
                status_code=422, detail="status must be proposed|approved|blocked|stale"
            ) from exc
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/scene-packets/{scene_packet_id}/qa", response_model=ScenePacketQaOut)
async def qa_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacketQaOut:
    """Re-run QA against the current body. A BLOCK_DRAFTING verdict blocks the packet; a malformed
    response fails closed (also blocks)."""
    row = await _get(session, scene_packet_id)
    cp_body = None
    cp = await session.get(ChapterPacket, row.chapter_packet_id)
    if cp is not None:
        cp_body = cp.body
    result = await qa_mod.qa_scene_packet(
        row.body or {}, chapter_packet_body=cp_body, budget=TokenBudget(max_tokens=20000)
    )
    if result is None:
        row.qa_verdict = ScenePacketVerdict.BLOCK_DRAFTING
        row.qa_warnings = {"residual_risks": [], "blocked_reason": "QA returned no usable verdict"}
        row.status = ScenePacketStatus.BLOCKED
        await session.commit()
        return ScenePacketQaOut(
            packet_id=row.id, verdict=ScenePacketVerdict.BLOCK_DRAFTING, warnings=row.qa_warnings
        )
    row.qa_verdict = result["verdict"]
    row.qa_warnings = {"residual_risks": result["residual_risks"], "issues": result["issues"]}
    if result["verdict"] == ScenePacketVerdict.BLOCK_DRAFTING:
        row.status = ScenePacketStatus.BLOCKED
    await session.commit()
    return ScenePacketQaOut(packet_id=row.id, verdict=str(row.qa_verdict), warnings=row.qa_warnings)


@router.post("/scene-packets/{scene_packet_id}/approve", response_model=ScenePacketOut)
async def approve_scene_packet(scene_packet_id: uuid.UUID, session: SessionDep) -> ScenePacket:
    """Approve one ScenePacket, then derive the chapter's beats. Refused when blocked or when QA
    blocks drafting."""
    row = await _get(session, scene_packet_id)
    if row.status == ScenePacketStatus.BLOCKED:
        raise HTTPException(status_code=409, detail="scene packet is blocked — re-derive or edit first")
    if _has_blocking_qa(row):
        raise HTTPException(status_code=409, detail="scene packet QA blocks drafting — resolve first")
    row.status = ScenePacketStatus.APPROVED
    derived = await beats_mod.derive_beats(session, chapter_id=row.chapter_id)
    await session.commit()
    await session.refresh(row)
    log.info("scene_packet.approved", packet=str(row.id), derived_beats=derived)
    return row


@router.post("/chapters/{chapter_id}/scene-packets/approve", response_model=list[ScenePacketOut])
async def approve_scene_packets(
    chapter_id: uuid.UUID, session: SessionDep, body: ScenePacketApproveIn | None = None
) -> list[ScenePacket]:
    """Batch approve. Approves only packets that are not blocked and have no blocking QA issues, then
    derives the chapter's beats from the approved set."""
    rows = (await session.execute(
        select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
    )).scalars().all()
    if not rows:
        raise HTTPException(status_code=400, detail="no scene packets to approve for this chapter")
    selected = set(body.packet_ids) if body and body.packet_ids else None
    approved = 0
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        if row.status == ScenePacketStatus.BLOCKED or _has_blocking_qa(row):
            continue
        row.status = ScenePacketStatus.APPROVED
        approved += 1
    derived = await beats_mod.derive_beats(session, chapter_id=chapter_id)
    await session.commit()
    log.info("scene_packet.batch_approved", chapter=str(chapter_id), approved=approved,
             derived_beats=derived)
    return list(rows)


@router.post("/chapters/{chapter_id}/scene-packets/mark-stale", response_model=list[ScenePacketOut])
async def mark_scene_packets_stale(
    chapter_id: uuid.UUID, session: SessionDep, body: ScenePacketApproveIn | None = None
) -> list[ScenePacket]:
    """Mark scene packets stale (optionally a subset) so they block new draft jobs until refreshed."""
    rows = (await session.execute(
        select(ScenePacket).where(ScenePacket.chapter_id == chapter_id).order_by(ScenePacket.scene_no)
    )).scalars().all()
    selected = set(body.packet_ids) if body and body.packet_ids else None
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        if row.status != ScenePacketStatus.BLOCKED:
            row.status = ScenePacketStatus.STALE
            row.stale_reason = "manually marked stale"
    await session.commit()
    return list(rows)
