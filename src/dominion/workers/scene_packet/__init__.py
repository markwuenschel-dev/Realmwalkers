"""Scene-packet pipeline facade (mirrors `workers/packet/__init__.py`).

A ScenePacket localizes an approved ChapterPacket into one scene's reader/POV/reveal/word contract.
This package's submodules (`derive`, `beats`, `qa`, `approval_policy`, `parse`, …) own the individual
steps; this facade is the single seam the API router calls so callers never have to orchestrate the
submodules by hand.

CONTEXT — beats are a projection of scene packets. Every scene-packet MUTATION reconciles beats. The
mutations that change the approved SET localize this here: `derive_scene_packets`,
`approve_scene_packet`, and `approve_scene_packets` reconcile beats internally after mutating packets
(the derive → beats temporal coupling that used to be repeated at four router call sites now lives in
exactly ONE place). Other status mutations that can drop a packet from the approved set — a human edit
that flips APPROVED→PROPOSED, or marking packets stale — reconcile via the `reconcile_beats` seam at
their call site. Either way no beat is left orphaned to strand the Draft gate as "unlinked".
`reconcile_beats` is the beats-only recompute: it prunes/upserts beats to match the current approved
packets and mutates no scene packet (also the explicit "re-derive beats" escape-hatch endpoint).

The facade never commits and never raises HTTP: mirroring the chapter-packet facade, it mutates the
session and returns; the caller commits and maps gate refusals to responses.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers import packet as _packet_pipeline
from dominion.workers.budget import TokenBudget
from dominion.workers.gates import GateRefusal
from dominion.workers.packet import approval_policy as _packet_approval
from dominion.workers.scene_packet import beats as _beats
from dominion.workers.scene_packet import derive as _derive
from dominion.workers.scene_packet import qa as _qa
from dominion.workers.scene_packet.approval_policy import (
    apply_qa_rerun,
    can_approve,
    enrich_scene_packet_out,
    is_approvable_for_batch,
)
from dominion.workers.scene_packet.fidelity import (
    accept_suggestions,
    mint_identity,
    refine_requirement,
    replace_requirement,
)
from dominion.workers.scene_packet.parse import valid_scene_packet_body
from dominion.workers.scene_packet.projections import ScenePacketProjections, project

__all__ = [
    # projections (unchanged public surface)
    "ScenePacketProjections",
    "project",
    # derive preconditions (chapter-packet tier, surfaced here so the router has one seam)
    "latest_approved_chapter_packet",
    "can_derive_scene_packets",
    # scene-packet mutations — each re-derives beats internally
    "derive_scene_packets",
    "approve_scene_packet",
    "approve_scene_packets",
    # beats-only projection refresh (no scene-packet mutation)
    "reconcile_beats",
    # advisory QA (no mutation of scene-packet state on its own)
    "qa_scene_packet",
    # read/enrichment + validation passthroughs
    "enrich_scene_packet_out",
    "can_approve",
    "apply_qa_rerun",
    "is_approvable_for_batch",
    "valid_scene_packet_body",
    # SceneFidelity requirement author actions (server mints identity; ADR 0006/0024)
    "accept_suggestions",
    "refine_requirement",
    "replace_requirement",
    "mint_identity",
]


async def latest_approved_chapter_packet(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterPacket | None:
    """The approved ChapterPacket that scene-packet derivation localizes, or None. Thin passthrough to
    the chapter-packet facade so the scene-packet router has a single upstream seam."""
    return await _packet_pipeline.latest_approved(session, chapter_id)


def can_derive_scene_packets(chapter_packet: ChapterPacket | None) -> GateRefusal | None:
    """Derive precondition: refuse unless the chapter packet is approved. Surfaced here so the router
    checks the gate through the scene-packet facade instead of reaching into the packet tier."""
    return _packet_approval.can_derive_scene_packets(chapter_packet)


async def derive_scene_packets(session: AsyncSession, *, chapter_packet: ChapterPacket) -> dict:
    """Derive/refresh a ScenePacket per scene seed of the approved `chapter_packet`, THEN reconcile the
    chapter's beats. Returns the derive counts ({created, updated, blocked, stale, …}); the caller
    commits.

    Beats are a projection of scene packets, so reconciling them here (not at the call site) is the
    whole point of the facade: a re-derive that changes the approved set also prunes orphaned beats
    (legacy beat-first rows, beats of no-longer-approved packets) that would otherwise hold the Draft
    gate as 'unlinked' forever."""
    counts = await _derive.derive_scene_packets(session, packet=chapter_packet)
    await _beats.derive_beats(session, chapter_id=chapter_packet.chapter_id)
    return counts


async def approve_scene_packet(session: AsyncSession, *, packet: ScenePacket) -> int:
    """Approve one ScenePacket, THEN reconcile the chapter's beats. Returns the reconciled beat count;
    the caller has already checked `can_approve` and commits. Beats are a projection of scene packets,
    so approval re-derives them in the same breath."""
    packet.status = ScenePacketStatus.APPROVED
    return await _beats.derive_beats(session, chapter_id=packet.chapter_id)


async def approve_scene_packets(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    rows: list[ScenePacket],
    packet_ids: list[uuid.UUID] | None = None,
) -> tuple[int, int]:
    """Batch-approve the approvable packets in `rows` (optionally restricted to `packet_ids`), THEN
    reconcile the chapter's beats. Approves only packets that are not blocked and carry no blocking QA
    issues (`is_approvable_for_batch`). Returns (approved_count, beat_count); the caller commits.

    Beats are a projection of scene packets, so the batch re-derives them once after the set changes."""
    selected = set(packet_ids) if packet_ids else None
    approved = 0
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        if not is_approvable_for_batch(row):
            continue
        row.status = ScenePacketStatus.APPROVED
        approved += 1
    derived = await _beats.derive_beats(session, chapter_id=chapter_id)
    return approved, derived


async def reconcile_beats(session: AsyncSession, *, chapter_id: uuid.UUID) -> int:
    """Recompute the chapter's beats from the CURRENT approved scene packets, WITHOUT mutating any
    scene packet: upsert one beat per approved packet and prune orphans. Returns the beat count; the
    caller commits. This is the explicit beats-only escape hatch — safe to run any time because no
    approval state changes — as opposed to the mutation facades, which reconcile beats as a side effect
    of changing the approved set."""
    return await _beats.derive_beats(session, chapter_id=chapter_id)


async def qa_scene_packet(
    scene_packet: dict,
    *,
    chapter_packet_body: dict | None = None,
    chapter_open_questions: dict | None = None,
    budget: TokenBudget,
) -> dict | None:
    """Re-run advisory scene-packet QA against a body -> {verdict, residual_risks, issues}, or None on a
    malformed response (the caller fails closed). Advisory-only: QA on its own never mutates approval
    state, so it re-derives no beats — the caller applies the verdict via `apply_qa_rerun`."""
    return await _qa.qa_scene_packet(
        scene_packet,
        chapter_packet_body=chapter_packet_body,
        chapter_open_questions=chapter_open_questions,
        budget=budget,
    )
