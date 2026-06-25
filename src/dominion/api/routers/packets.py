"""Chapter knowledge packet endpoints (contract-first drafting, Phase 1).

The packet is authored + QA'd by agents, then adjudicated and approved by the human BEFORE any prose
is drafted. This router proposes a packet (synchronous, like the gate-1 plan-call), returns it for
review, accepts human edits, and gates approval: a blocked or red-confidence packet, or one with open
questions still outstanding, cannot be approved. (Later phases block drafting until approval.)
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.enums import PacketConfidence, PacketStatus
from dominion.shared.models import Chapter, ChapterPacket
from dominion.shared.schemas import PacketOut, PacketUpdateIn
from dominion.workers import packet as packet_pipeline
from dominion.workers.packet import derive as packet_derive

log = structlog.get_logger()
router = APIRouter(prefix="/chapters", tags=["packets"])


async def _latest(session: SessionDep, chapter_id: uuid.UUID) -> ChapterPacket | None:
    return (await session.execute(
        select(ChapterPacket)
        .where(ChapterPacket.chapter_id == chapter_id)
        .order_by(ChapterPacket.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


def _open_items(packet: ChapterPacket) -> list[object]:
    oq = packet.open_questions or {}
    items = oq.get("items") if isinstance(oq, dict) else None
    return items if isinstance(items, list) else []


@router.post("/{chapter_id}/packet", response_model=PacketOut)
async def propose_packet(chapter_id: uuid.UUID, session: SessionDep) -> ChapterPacket:
    """Run the Packet Author + Packet QA for this chapter and persist the result (proposed/blocked).

    Synchronous like the gate-1 plan-call; fail-closed internally (a malformed/timed-out agent yields
    a blocked packet rather than partial constraints)."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    row = await packet_pipeline.propose_packet(session, chapter=chapter)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/{chapter_id}/packet", response_model=PacketOut)
async def get_packet(chapter_id: uuid.UUID, session: SessionDep) -> ChapterPacket:
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    return row


@router.put("/{chapter_id}/packet", response_model=PacketOut)
async def update_packet(
    chapter_id: uuid.UUID, body: PacketUpdateIn, session: SessionDep
) -> ChapterPacket:
    """Human edit/adjudication: replace the body, clear open questions, and/or raise confidence after
    reviewing flags. A blocked packet can be edited but stays blocked until re-proposed."""
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    if body.body is not None:
        # Stamp ids on any seeds the human added so they stay linkable once derived; existing ids are
        # preserved (reassign, not in-place mutate, so SQLAlchemy flags the JSONB change).
        new_body = body.body
        packet_pipeline.mint_seed_ids(new_body)
        row.body = new_body
    if body.open_questions is not None:
        row.open_questions = body.open_questions
    if body.confidence is not None:
        try:
            row.confidence = PacketConfidence(body.confidence.strip().lower())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="confidence must be green|yellow|red") from exc
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/{chapter_id}/packet/approve", response_model=PacketOut)
async def approve_packet(chapter_id: uuid.UUID, session: SessionDep) -> ChapterPacket:
    """Approve the packet so drafting may proceed. Refused when blocked, red-confidence, or open
    questions remain (no auto-approve during tuning — even a green packet needs this human action)."""
    row = await _latest(session, chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no packet for this chapter yet")
    if row.status == PacketStatus.BLOCKED:
        raise HTTPException(status_code=409, detail="packet is blocked — re-propose or edit it first")
    if row.confidence == PacketConfidence.RED:
        raise HTTPException(status_code=409, detail="red-confidence packet — resolve before approving")
    if _open_items(row):
        raise HTTPException(status_code=409, detail="resolve the packet's open questions first")
    row.status = PacketStatus.APPROVED
    # Contract-first (Phase 2): approval is gate 1 — derive this chapter's beats from the packet's
    # scene_seeds so the writer drafts against the approved contract, not an unlinked plan-call.
    derived = await packet_derive.derive_beats(session, packet=row)
    await session.commit()
    await session.refresh(row)
    log.info("packet.approved", chapter=str(chapter_id), packet=str(row.id), derived_beats=derived)
    return row
