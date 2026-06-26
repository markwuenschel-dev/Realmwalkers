"""Derive per-scene Beats from an approved ChapterPacket (contract-first drafting, Phase 2).

The packet's scene_seeds become the chapter's Beats: one Beat per seed, linked by the seed's stable
`seed_id` so re-deriving after a packet edit updates in place instead of duplicating. The packet is
the source of truth — these Beats carry only the display/routing projection (job, length, cast, lane
tags); the hard constraints (allowed/forbidden knowledge, reveals, locks, forbidden beats) are read
straight from the packet at draft time (workers/context.assemble_context), never copied here, so a
packet edit + re-approve takes effect without re-deriving.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus
from dominion.shared.models import Beat, ChapterPacket, Scene

# Tags the router fans out to specialist enrichment + review lanes; a seed routes to one when its
# type or required beats name it. Empty tags -> the Phase-1 default (drafter + continuity only).
_LANE_TAGS: tuple[str, ...] = ("combat", "dialogue", "sensory")


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _tags_for_seed(seed: dict[str, Any]) -> list[str]:
    """Route the scene to specialist lanes by matching lane keywords in its type + required beats."""
    haystack = " ".join(
        [str(seed.get("scene_type") or ""), *_as_str_list(seed.get("required_beats"))]
    ).lower()
    return [tag for tag in _LANE_TAGS if tag in haystack]


def _beat_text(seed: dict[str, Any]) -> str | None:
    """The human/drafter-facing 'what happens': the seed's job + required beats + exit state. The
    forbidden beats / reveals / locks live in the packet contract (enforced at draft time), not here."""
    parts: list[str] = []
    if job := str(seed.get("scene_job") or "").strip():
        parts.append(job)
    if required := _as_str_list(seed.get("required_beats")):
        parts.append("Required beats:\n" + "\n".join(f"- {b}" for b in required))
    if exit_state := str(seed.get("exit_state") or "").strip():
        parts.append(f"Exit state: {exit_state}")
    return "\n\n".join(parts) or None


def _target_words(seed: dict[str, Any]) -> int | None:
    wb = seed.get("word_budget")
    target = wb.get("target") if isinstance(wb, dict) else None
    return target if isinstance(target, int) else None


async def derive_beats(session: AsyncSession, *, packet: ChapterPacket) -> int:
    """Upsert one Beat per scene_seed (keyed by seed_id) and prune stale, un-drafted derived beats.

    Returns the count of seed-linked beats after derivation. Idempotent: re-approving the same packet
    updates beats in place rather than duplicating. A derived beat whose scene has already been drafted
    is never pruned, so editing a packet can't destroy committed prose. The caller commits.
    """
    body: dict[str, Any] = packet.body or {}
    seeds = [s for s in (body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    absent = set(_as_str_list(body.get("characters_absent")))
    cast = [c for c in _as_str_list(body.get("characters_present")) if c not in absent]

    existing: dict[uuid.UUID, Beat] = {
        b.scene_seed_id: b
        for b in (await session.execute(
            select(Beat).where(Beat.chapter_id == packet.chapter_id, Beat.scene_seed_id.isnot(None))
        )).scalars()
        if b.scene_seed_id is not None
    }

    seen: set[uuid.UUID] = set()
    for seed in seeds:
        try:
            seed_id = uuid.UUID(str(seed["seed_id"]))
        except (ValueError, AttributeError, TypeError):
            continue  # a malformed id is skipped, never fatal
        seen.add(seed_id)
        scene_no = seed.get("scene_no")
        beat = existing.get(seed_id)
        if beat is None:
            beat = Beat(
                chapter_id=packet.chapter_id, scene_seed_id=seed_id,
                scene_no=scene_no if isinstance(scene_no, int) else 0,
            )
            session.add(beat)
        if isinstance(scene_no, int):
            beat.scene_no = scene_no
        beat.beat_text = _beat_text(seed)
        beat.target_words = _target_words(seed)
        beat.tags = _tags_for_seed(seed)
        beat.characters_present = cast or None
        beat.status = BeatStatus.APPROVED

    # Prune derived beats whose seed vanished from the packet — but only when no scene has been drafted
    # for them yet (a drafted scene shares chapter_id + scene_no), so a re-derive can't orphan prose.
    drafted = {
        sn for (sn,) in (await session.execute(
            select(Scene.scene_no).where(Scene.chapter_id == packet.chapter_id)
        )).all()
    }
    for seed_id, beat in existing.items():
        if seed_id not in seen and beat.scene_no not in drafted:
            await session.delete(beat)

    return len(seen)
