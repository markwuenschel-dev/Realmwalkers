"""Load approved ScenePacket rows and project contract fields."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers.context.types import ScenePacketFields, ScenePacketRequiredError
from dominion.workers.scene_packet.projections import project


async def load_scene_packet_fields(
    session: AsyncSession, scene_packet_id: uuid.UUID
) -> ScenePacketFields:
    """Load an approved, non-stale ScenePacket and return consumer-facing contract fields."""
    sp = await session.get(ScenePacket, scene_packet_id)
    if sp is None:
        raise ScenePacketRequiredError(f"no scene packet {scene_packet_id} for this draft job")
    if sp.status == ScenePacketStatus.STALE:
        raise ScenePacketRequiredError(
            f"scene packet {scene_packet_id} is stale ({sp.stale_reason or 'inputs changed'}) — "
            "re-derive or re-approve it before drafting"
        )
    if sp.status != ScenePacketStatus.APPROVED:
        raise ScenePacketRequiredError(
            f"scene packet {scene_packet_id} is {sp.status}, not approved — approve it before drafting"
        )

    body = sp.body or {}
    chapter_body = (await session.execute(
        select(ChapterPacket.body).where(ChapterPacket.id == sp.chapter_packet_id)
    )).scalar_one_or_none()
    chapter_body = chapter_body if isinstance(chapter_body, dict) else {}

    p = project(body, chapter_body)
    return ScenePacketFields(
        scene_packet_id=sp.id,
        scene_contract=p.scene_body,
        chapter_contract=p.chapter_body,
        word_budget=p.word_budget,
        reader_state_contract=p.reader_state,
        reviewer_contract=p.reviewer,
        contract=p.drafter_flat or None,
    )
