"""ScenePacket staleness detection (DESIGN: staleness detection).

A ScenePacket stores the `source_hash` of every input it was derived from. When an upstream input
changes — the chapter packet body, the scene seed, the chapter word budget, a prior approved scene —
the recomputed hash differs and the packet is marked STALE. A stale packet blocks new draft jobs
(enforced in context._load_scene_packet) until it is re-derived or re-approved.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers.length import planner as length_planner
from dominion.workers.scene_packet import hash as hash_mod
from dominion.workers.scene_packet import inputs as sp_inputs


async def recompute_and_mark(session: AsyncSession, *, chapter_id: uuid.UUID) -> int:
    """Recompute each non-blocked ScenePacket's source_hash for the chapter; mark drifted ones STALE.
    Returns the number newly marked stale. The caller commits."""
    packets = (await session.execute(
        select(ScenePacket).where(
            ScenePacket.chapter_id == chapter_id,
            ScenePacket.status.in_([ScenePacketStatus.PROPOSED, ScenePacketStatus.APPROVED]),
        )
    )).scalars().all()
    if not packets:
        return 0

    # Group by chapter packet so we plan word budgets once per chapter-packet body.
    cp_bodies: dict[uuid.UUID, dict[str, Any]] = {}
    cp_budgets: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}
    marked = 0
    for sp in packets:
        body = cp_bodies.get(sp.chapter_packet_id)
        if body is None:
            cp = await session.get(ChapterPacket, sp.chapter_packet_id)
            body = cp.body if cp and isinstance(cp.body, dict) else {}
            cp_bodies[sp.chapter_packet_id] = body
            seeds = [s for s in (body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
            target, cap = sp_inputs.chapter_targets(body, seeds)
            cp_budgets[sp.chapter_packet_id] = length_planner.plan_word_budgets(
                chapter_target_words=target, chapter_max_words=cap, scene_seeds=seeds,
                chapter_packet_body=body,
            )

        seed = next(
            (s for s in (body.get("scene_seeds") or [])
             if isinstance(s, dict) and str(s.get("seed_id")) == str(sp.scene_seed_id)),
            None,
        )
        word_budget = cp_budgets[sp.chapter_packet_id].get(str(sp.scene_seed_id), {})
        prior_keys = await sp_inputs.prior_scene_keys(
            session, chapter_id=chapter_id, scene_no=sp.scene_no
        )
        current = hash_mod.source_hash(
            chapter_packet_id=sp.chapter_packet_id, chapter_packet_body=body, scene_seed=seed,
            chapter_word_budget=word_budget, prior_scene_keys=prior_keys,
        )
        if current != sp.source_hash:
            sp.status = ScenePacketStatus.STALE
            sp.stale_reason = "upstream inputs changed since derivation"
            marked += 1
    return marked
