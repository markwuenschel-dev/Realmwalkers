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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers import packet as _packet_pipeline
from dominion.workers.budget import TokenBudget
from dominion.workers.gates import GateRefusal
from dominion.workers.packet import approval_policy as _packet_approval
from dominion.workers.scene_packet import beats as _beats
from dominion.workers.scene_packet import blockers as _blockers
from dominion.workers.scene_packet import derive as _derive
from dominion.workers.scene_packet import qa as _qa
from dominion.workers.scene_packet.approval_policy import (
    apply_qa_rerun,
    can_approve,
    is_approvable_for_batch,
)
from dominion.workers.scene_packet.blockers import scene_out_with_blockers, scene_outs_with_blockers
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
    # read/enrichment + validation passthroughs — the blocker-aware bulk readers are the router-facing
    # projection; bare `enrich_scene_packet_out` is intentionally NOT re-exported (A1c: no bypass seam).
    "scene_out_with_blockers",
    "scene_outs_with_blockers",
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


@dataclass(frozen=True)
class ApprovalOutcome:
    """Result of the one locked approval op. When `approved` is False the reason is explicit and mutually
    exclusive: `refusal` (base status — BLOCKED/RATE_LIMITED — non-approvable on the locked row) or
    `blocked_by_open_question` (an active ApprovalBlocker). `packet` is always the locked, refreshed row."""

    approved: bool
    packet: ScenePacket
    refusal: GateRefusal | None = None
    blocked_by_open_question: bool = False


async def _apply_approval_locked(session: AsyncSession, scene_packet_id: uuid.UUID) -> ApprovalOutcome:
    """The single locked approval op every path funnels through (ADR-0031 D6/D9). Lock + refresh the row,
    then RE-EVALUATE both gates on that fresh row — base status eligibility (`can_approve`) AND active
    blocker — so a status or blocker change that landed between a caller's stale read and this lock can
    never slip an APPROVED past it (A1c F7). Sets APPROVED and clears `stale_reason` (re-approving a STALE
    packet is legitimate) only when both gates pass. This op does NOT reconcile beats and does NOT raise:
    the public facade ops around it own raise-vs-skip and reconcile beats — once for single approve, once
    per batch — so the derive→beats coupling still lives in exactly one place."""
    packet = await _blockers.lock_packet(session, scene_packet_id)
    if refusal := can_approve(packet):
        return ApprovalOutcome(False, packet, refusal=refusal)
    if await _blockers.has_active_blocker(session, packet.id):
        return ApprovalOutcome(False, packet, blocked_by_open_question=True)
    packet.status = ScenePacketStatus.APPROVED
    packet.stale_reason = None
    return ApprovalOutcome(True, packet)


async def approve_scene_packet(session: AsyncSession, *, packet: ScenePacket) -> int:
    """Approve ONE ScenePacket through the locked op, THEN reconcile the chapter's beats (returns the beat
    count; the caller commits). Raises `ApprovalBlockerError` when an active ApprovalBlocker holds it, OR
    when its base status is non-approvable on the locked row (a demotion that raced the caller's
    pre-check) — the router maps both to 409. Beats are a projection of approved packets, so approval
    re-derives them in the same breath; the facade owns that coupling (ADR-0031 D6)."""
    outcome = await _apply_approval_locked(session, packet.id)
    if not outcome.approved:
        if outcome.blocked_by_open_question:
            raise _blockers.ApprovalBlockerError("scene packet has an unresolved approval blocker — resolve it first")
        assert outcome.refusal is not None  # not approved and not blocker ⇒ a base-status refusal
        raise _blockers.ApprovalBlockerError(outcome.refusal.detail)
    return await _beats.derive_beats(session, chapter_id=outcome.packet.chapter_id)


async def approve_scene_packets(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    rows: list[ScenePacket],
    packet_ids: list[uuid.UUID] | None = None,
) -> tuple[int, int]:
    """Batch-approve the approvable packets in `rows` (optionally restricted to `packet_ids`), THEN
    reconcile the chapter's beats ONCE. Every candidate goes through the SAME locked op as single approve,
    so base status AND active blocker are re-checked on the locked, refreshed row — a packet a concurrent
    transaction demoted or blocked is SKIPPED, never approved off a stale pre-lock read (A1c F7, D6).
    Returns (approved_count, beat_count); the caller commits."""
    selected = set(packet_ids) if packet_ids else None
    approved = 0
    for row in rows:
        if selected is not None and row.id not in selected:
            continue
        outcome = await _apply_approval_locked(session, row.id)
        if outcome.approved:
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
