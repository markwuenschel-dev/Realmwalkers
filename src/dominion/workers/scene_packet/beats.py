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

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, JobStatus, ScenePacketStatus
from dominion.shared.models import Beat, ChapterPacket, Job, Scene, ScenePacket
from dominion.shared.text_match import binding_replacements, project_text

_LANE_TAGS: tuple[str, ...] = ("combat", "dialogue", "sensory")


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _tags_for(body: dict[str, Any]) -> list[str]:
    haystack = " ".join([str(body.get("scene_type") or ""), *_as_str_list(body.get("required_beats"))]).lower()
    return [tag for tag in _LANE_TAGS if tag in haystack]


def _beat_text(body: dict[str, Any]) -> str | None:
    # beat_text is the drafter-facing string (drafter.py injects it verbatim), so it is the last chokepoint
    # to scrub forbidden canonical names to their surface labels. derive already projects the scene body,
    # but re-projecting here from the body's own entity_bindings guarantees beat_text is surface-safe even
    # if the body was authored/edited outside that path. No bindings → an inert no-op.
    reps = binding_replacements(body.get("entity_bindings"))
    parts: list[str] = []
    if job := project_text(str(body.get("scene_job") or "").strip(), reps):
        parts.append(job)
    if required := [project_text(b, reps) for b in _as_str_list(body.get("required_beats"))]:
        parts.append("Required beats:\n" + "\n".join(f"- {b}" for b in required))
    if exit_state := project_text(str(body.get("exit_state") or "").strip(), reps):
        parts.append(f"Exit state: {exit_state}")
    return "\n\n".join(parts) or None


def _target_words(body: dict[str, Any]) -> int | None:
    wb = body.get("word_budget")
    target = wb.get("target") if isinstance(wb, dict) else None
    return target if isinstance(target, int) else None


async def _chapter_cast(session: AsyncSession, chapter_packet_id: uuid.UUID) -> list[str] | None:
    """Cast for a chapter's beats = the chapter packet's present characters minus the absent ones."""
    body = (
        await session.execute(select(ChapterPacket.body).where(ChapterPacket.id == chapter_packet_id))
    ).scalar_one_or_none()
    if not isinstance(body, dict):
        return None
    absent = set(_as_str_list(body.get("characters_absent")))
    cast = [c for c in _as_str_list(body.get("characters_present")) if c not in absent]
    return cast or None


async def derive_beats(session: AsyncSession, *, chapter_id: uuid.UUID) -> int:
    """Upsert one Beat per APPROVED ScenePacket of this chapter (keyed by scene_packet_id), prune
    stale un-drafted derived beats, AND prune legacy beat-first rows (scene_packet_id IS NULL).
    Returns the count of scene-packet-linked beats. The caller commits.

    Legacy pruning: beat-first drafting is disabled, so an approved beat with no packet link can never
    draft — but draft_readiness counts it as "unlinked" and hard-blocks the Draft gate FOREVER (the
    observed 'beats_linked 4/8' dead-end: 4 packet-linked beats + 4 legacy orphans). A legacy beat is
    never the beat of record for a drafted scene (the packet-linked beat is), so the drafted-scene
    guard below deliberately does not spare it; only a beat still referenced by an ACTIVE job is kept.
    """
    packets = (
        (
            await session.execute(
                select(ScenePacket)
                .where(
                    ScenePacket.chapter_id == chapter_id,
                    ScenePacket.status == ScenePacketStatus.APPROVED,
                )
                .order_by(ScenePacket.scene_no)
            )
        )
        .scalars()
        .all()
    )

    all_beats = list((await session.execute(select(Beat).where(Beat.chapter_id == chapter_id))).scalars())
    existing: dict[uuid.UUID, Beat] = {b.scene_packet_id: b for b in all_beats if b.scene_packet_id is not None}
    legacy = [b for b in all_beats if b.scene_packet_id is None]

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
        sn for (sn,) in (await session.execute(select(Scene.scene_no).where(Scene.chapter_id == chapter_id))).all()
    }
    for sp_id, beat in existing.items():
        if sp_id not in seen and beat.scene_no not in drafted:
            await session.delete(beat)

    # Prune legacy beat-first rows (see docstring). Jobs FK beats without cascade, so historical
    # (failed/done) jobs are detached first; a beat still held by a QUEUED/RUNNING job is left alone.
    if legacy:
        legacy_ids = [b.id for b in legacy]
        active_beat_ids = {
            bid
            for (bid,) in (
                await session.execute(
                    select(Job.beat_id).where(
                        Job.beat_id.in_(legacy_ids),
                        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                    )
                )
            ).all()
        }
        removable = [b for b in legacy if b.id not in active_beat_ids]
        if removable:
            removable_ids = [b.id for b in removable]
            await session.execute(update(Job).where(Job.beat_id.in_(removable_ids)).values(beat_id=None))
            for beat in removable:
                await session.delete(beat)

    return len(seen)
