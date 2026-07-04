"""Load approved ScenePacket rows and project contract fields."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import ChapterPacket, ChapterSequence, ScenePacket
from dominion.workers.context.types import ScenePacketFields, ScenePacketRequiredError
from dominion.workers.scene_packet import approval_policy
from dominion.workers.scene_packet.projections import project
from dominion.workers.scene_scope import beats_owned_by_later_scenes


async def load_scene_packet_fields(session: AsyncSession, scene_packet_id: uuid.UUID) -> ScenePacketFields:
    """Load an approved, non-stale ScenePacket and return consumer-facing contract fields."""
    sp = await session.get(ScenePacket, scene_packet_id)
    if sp is None:
        raise ScenePacketRequiredError(f"no scene packet {scene_packet_id} for this draft job")
    approval_policy.assert_draft_ready(sp)

    body = dict(sp.body or {})
    chapter_body = (
        await session.execute(select(ChapterPacket.body).where(ChapterPacket.id == sp.chapter_packet_id))
    ).scalar_one_or_none()
    chapter_body = chapter_body if isinstance(chapter_body, dict) else {}

    # Make ChapterSequence operational for the drafter: overlay sequence fields into the
    # effective packet body used by assemble_context / project. This ensures entry/exit,
    # owned_beats, must_not_repeat etc. from the production plan control drafting context,
    # not only the UI artifact.
    seq = (
        (
            await session.execute(
                select(ChapterSequence)
                .where(ChapterSequence.chapter_id == sp.chapter_id)
                .order_by(ChapterSequence.updated_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if seq and seq.body:
        seq_item = next(
            (
                it
                for it in (seq.body.get("scenes") or [])
                if isinstance(it, dict) and int(it.get("scene_no") or 0) == sp.scene_no
            ),
            None,
        )
        if seq_item:
            # Overlay key fields (do not overwrite author-provided if already present)
            for key in ("entry_state", "exit_state", "scene_function", "sequence_scene_function"):
                if seq_item.get(key) and key not in body:
                    body[key] = seq_item.get(key)
            for key in ("owned_beats", "required_beats", "forbidden_beats", "must_not_repeat"):
                val = seq_item.get(key) or seq_item.get("required_beats")
                if val and key not in body:
                    body[key] = val
            if seq_item.get("word_budget") and "word_budget" not in body:
                body["word_budget"] = seq_item["word_budget"]
            # reader state hints from sequence
            for k in ("reader_learns", "reader_must_not_know", "reader_knows_at_start"):
                if seq_item.get(k) and k not in body:
                    body[k] = seq_item.get(k)
        # Beat-ownership scope guard (recovery L2): tell the drafter which beats belong to LATER
        # scenes so it cannot stage them here (scene_scope_bleed). Formatted with the owning
        # scene number; project()/_contract_block render these as hard MUST-NOTs.
        later_owned = beats_owned_by_later_scenes(sp.scene_no, seq.body)
        if later_owned and "beats_owned_by_later_scenes" not in body:
            body["beats_owned_by_later_scenes"] = [f"(owned by scene {owner}) {beat}" for beat, owner in later_owned]

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
