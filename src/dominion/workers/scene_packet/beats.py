"""Derive per-scene Beats from APPROVED ScenePackets (scene-packet contract system).

This replaces deriving beats straight from ChapterPacket scene seeds. The new chain is:

    ChapterPacket approved → ScenePackets derived → ScenePackets approved → Beats derived here

A Beat is now the display/routing PROJECTION of an approved ScenePacket: scene_no, cast, lane tags,
a human-facing beat_text, target_words, and the scene_packet_id link. The hard constraints
(reader/POV knowledge, reveals, mysteries, traps, word budget) stay in the ScenePacket and are read
at draft time — never copied into the Beat. Keyed by scene_packet_id so re-deriving updates in place.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, ScenePacketStatus
from dominion.shared.models import Beat, ChapterPacket, Scene, ScenePacket

_LANE_TAGS: tuple[str, ...] = ("combat", "dialogue", "sensory")


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _tags_for(body: dict[str, Any]) -> list[str]:
    haystack = " ".join(
        [str(body.get("scene_type") or ""), *_as_str_list(body.get("required_beats"))]
    ).lower()
    return [tag for tag in _LANE_TAGS if tag in haystack]


def _beat_text(body: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if job := str(body.get("scene_job") or "").strip():
        parts.append(job)
    if required := _as_str_list(body.get("required_beats")):
        parts.append("Required beats:\n" + "\n".join(f"- {b}" for b in required))
    if exit_state := str(body.get("exit_state") or "").strip():
        parts.append(f"Exit state: {exit_state}")
    return "\n\n".join(parts) or None


def _target_words(body: dict[str, Any]) -> int | None:
    wb = body.get("word_budget")
    target = wb.get("target") if isinstance(wb, dict) else None
    return target if isinstance(target, int) else None


async def _chapter_cast(session: AsyncSession, chapter_packet_id: uuid.UUID) -> list[str] | None:
    """Cast for a chapter's beats = the chapter packet's present characters minus the absent ones."""
    body = (await session.execute(
        select(ChapterPacket.body).where(ChapterPacket.id == chapter_packet_id)
    )).scalar_one_or_none()
    if not isinstance(body, dict):
        return None
    absent = set(_as_str_list(body.get("characters_absent")))
    cast = [c for c in _as_str_list(body.get("characters_present")) if c not in absent]
    return cast or None


async def derive_beats(session: AsyncSession, *, chapter_id: uuid.UUID) -> int:
    """Upsert one Beat per APPROVED ScenePacket of this chapter (keyed by scene_packet_id) and prune
    stale, un-drafted derived beats. Returns the count of scene-packet-linked beats. The caller commits.
    """
    packets = (await session.execute(
        select(ScenePacket).where(
            ScenePacket.chapter_id == chapter_id,
            ScenePacket.status == ScenePacketStatus.APPROVED,
        ).order_by(ScenePacket.scene_no)
    )).scalars().all()

    existing: dict[uuid.UUID, Beat] = {
        b.scene_packet_id: b
        for b in (await session.execute(
            select(Beat).where(Beat.chapter_id == chapter_id, Beat.scene_packet_id.isnot(None))
        )).scalars()
        if b.scene_packet_id is not None
    }

    seen: set[uuid.UUID] = set()
    for sp in packets:
        seen.add(sp.id)
        body = sp.body or {}
        cast = await _chapter_cast(session, sp.chapter_packet_id)
        beat = existing.get(sp.id)
        if beat is None:
            beat = Beat(chapter_id=chapter_id, scene_packet_id=sp.id, scene_no=sp.scene_no)
            session.add(beat)
        beat.scene_seed_id = sp.scene_seed_id
        beat.scene_no = sp.scene_no
        beat.beat_text = _beat_text(body)
        beat.target_words = _target_words(body)
        beat.tags = _tags_for(body)
        beat.characters_present = cast
        beat.status = BeatStatus.APPROVED

    # Prune derived beats whose packet is no longer approved — but never one whose scene was drafted.
    drafted = {
        sn for (sn,) in (await session.execute(
            select(Scene.scene_no).where(Scene.chapter_id == chapter_id)
        )).all()
    }
    for sp_id, beat in existing.items():
        if sp_id not in seen and beat.scene_no not in drafted:
            await session.delete(beat)

    return len(seen)
