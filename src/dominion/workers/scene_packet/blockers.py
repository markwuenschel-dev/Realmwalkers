"""Approval Blocker boundary — the scene-tier hold that gates automated approval (A1c slice 1).

ADR-0031 D9/D14. A `manual_command` raises a durable, scene-packet-scoped ApprovalBlocker; an ACTIVE
blocker blocks EVERY approval (human and autonomous) until an explicit resolver closes it with a
rationale + source. The write seam and the approval gate both `SELECT ... FOR UPDATE` the owning
ScenePacket row, so a blocker can never appear immediately after approval (the cross-table race, F7).
Beats are a projection of approved packets, so demoting an approved packet reconciles them.

Scope: this is the blocker boundary only. It does NOT deliver D9's durable Execution Authorization
replacement (that remains `human_approved`/`human_approved_at`/`autonomous`, deferred) and does not touch
draft/revise scheduling, the auto-verify selector, or downstream provenance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ApprovalBlockerStatus, ScenePacketStatus
from dominion.shared.models import ApprovalBlocker, ScenePacket
from dominion.shared.schemas import ScenePacketOut

MANUAL_COMMAND = "manual_command"


class ApprovalBlockerError(ValueError):
    """A blocker operation refused (the router maps it to 409/422)."""


async def _lock_packet(session: AsyncSession, scene_packet_id: uuid.UUID) -> ScenePacket:
    # with_for_update + populate_existing: acquire the row lock AND refresh a possibly-stale identity-map
    # copy (the A1b lesson). Every writer/approver locks the SAME row, so they serialize.
    packet = await session.get(ScenePacket, scene_packet_id, with_for_update=True, populate_existing=True)
    if packet is None:
        raise ApprovalBlockerError("scene packet not found")
    return packet


async def _active(
    session: AsyncSession, scene_packet_id: uuid.UUID, source: str, source_key: str
) -> ApprovalBlocker | None:
    return (
        await session.execute(
            select(ApprovalBlocker).where(
                ApprovalBlocker.scene_packet_id == scene_packet_id,
                ApprovalBlocker.source == source,
                ApprovalBlocker.source_key == source_key,
                ApprovalBlocker.status == ApprovalBlockerStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()


async def has_active_blocker(session: AsyncSession, scene_packet_id: uuid.UUID) -> bool:
    """The write-seam gate: are there active blockers for ONE packet (the caller already holds the lock)."""
    row = (
        await session.execute(
            select(ApprovalBlocker.id)
            .where(
                ApprovalBlocker.scene_packet_id == scene_packet_id,
                ApprovalBlocker.status == ApprovalBlockerStatus.ACTIVE.value,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def lock_packet_if_unblocked(session: AsyncSession, scene_packet_id: uuid.UUID) -> ScenePacket | None:
    """Lock the ScenePacket row; return it only if it has no active blocker (else None). Batch-friendly."""
    packet = await _lock_packet(session, scene_packet_id)
    if await has_active_blocker(session, scene_packet_id):
        return None
    return packet


async def lock_and_guard(session: AsyncSession, scene_packet_id: uuid.UUID) -> ScenePacket:
    """The single approval-gate seam (D6/D9): lock the ScenePacket row and REFUSE if it has an active
    blocker. The approval operation calls this before writing APPROVED, so no raw path approves past a
    blocker and a concurrent `raise_blocker` serializes on the same lock. Returns the locked packet."""
    packet = await lock_packet_if_unblocked(session, scene_packet_id)
    if packet is None:
        raise ApprovalBlockerError("scene packet has an unresolved approval blocker — resolve it first")
    return packet


async def raise_blocker(
    session: AsyncSession,
    *,
    scene_packet_id: uuid.UUID,
    source_key: str,
    question: str,
    source: str = MANUAL_COMMAND,
) -> ApprovalBlocker:
    """Raise a scene-tier ApprovalBlocker. Idempotent per active `(scene_packet_id, source, source_key)`:
    a retry returns the existing active row. Locks the owning ScenePacket row FIRST so a concurrent
    approve can't slip in; if the (now-locked) packet is already APPROVED, DEMOTE it to PROPOSED and
    reconcile beats in the same transaction — the invariant is that no APPROVED packet may carry an
    active blocker, or retain approved-derived beats. Provenance is `manual_command` (a deliberate route,
    not an authenticated actor). The caller commits."""
    if not source_key or not source_key.strip():
        raise ApprovalBlockerError("source_key is required")
    if not question or not question.strip():
        raise ApprovalBlockerError("question is required")
    packet = await _lock_packet(session, scene_packet_id)
    existing = await _active(session, packet.id, source, source_key)
    if existing is not None:
        return existing  # idempotent retry — one active row per (scene_packet_id, source, source_key)
    blocker = ApprovalBlocker(
        scene_packet_id=packet.id,
        chapter_id=packet.chapter_id,
        source=source,
        source_key=source_key,
        question=question.strip(),
        status=ApprovalBlockerStatus.ACTIVE.value,
    )
    session.add(blocker)
    if packet.status == ScenePacketStatus.APPROVED:
        packet.status = ScenePacketStatus.PROPOSED  # demote: the blocker makes it un-approved
        await session.flush()
        # beats are a projection of APPROVED packets — reconcile to prune the demoted packet's beat.
        from dominion.workers.scene_packet import beats as _beats  # lazy: avoid a package import cycle

        await _beats.derive_beats(session, chapter_id=packet.chapter_id)
    return blocker


async def resolve_blocker(
    session: AsyncSession, *, blocker_id: uuid.UUID, rationale: str, resolution_source: str
) -> ApprovalBlocker:
    """Close an active blocker. Approval is NOT a resolution — this is the only way to clear one, and it
    requires an explicit nonempty rationale + source. Re-raising the same key after resolution is new
    history (a fresh row). The caller commits."""
    if not (rationale and rationale.strip()) or not (resolution_source and resolution_source.strip()):
        raise ApprovalBlockerError("resolution requires a nonempty rationale and source")
    blocker = await session.get(ApprovalBlocker, blocker_id)
    if blocker is None:
        raise ApprovalBlockerError("approval blocker not found")
    if blocker.status != ApprovalBlockerStatus.ACTIVE.value:
        raise ApprovalBlockerError(f"blocker is {blocker.status}; only an active blocker can be resolved")
    blocker.status = ApprovalBlockerStatus.RESOLVED.value
    blocker.resolved_at = datetime.now(UTC)
    blocker.resolution_rationale = rationale.strip()
    blocker.resolution_source = resolution_source.strip()
    return blocker


async def active_blockers_for(
    session: AsyncSession, scene_packet_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[ApprovalBlocker]]:
    """Bulk reader for the projection: active blockers grouped by scene_packet_id. A packet ABSENT from
    the result has no active blocker; a caller that never loads this must fail closed (missing facts are
    not 'no blockers' — see the scene enrich)."""
    ids = list(scene_packet_ids)
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(ApprovalBlocker).where(
                    ApprovalBlocker.scene_packet_id.in_(ids),
                    ApprovalBlocker.status == ApprovalBlockerStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, list[ApprovalBlocker]] = {}
    for r in rows:
        out.setdefault(r.scene_packet_id, []).append(r)
    return out


def scene_packet_out_with_blocker(
    base: ScenePacketOut, active_blockers: list[ApprovalBlocker] | None
) -> ScenePacketOut:
    """Overlay active-blocker facts onto a base scene-packet projection. FAIL CLOSED (A1c F6/F7): `None`
    means the facts were NOT loaded (unknown) → not approvable, never silently 'no blockers'. `[]` means
    loaded and none → the base projection stands. A non-empty list surfaces the blocked_by_open_question
    gate state. This is display only; the authoritative gate is the locked approval operation."""
    if active_blockers is None:
        return base.model_copy(
            update={
                "can_approve": False,
                "approval_state": "blocker_unknown",
                "approval_blockers": ["approval-blocker facts were not loaded"],
            }
        )
    if not active_blockers:
        return base
    return base.model_copy(
        update={
            "can_approve": False,
            "approval_state": "blocked_by_open_question",
            "approval_blockers": [b.question for b in active_blockers],
        }
    )


async def enrich_scene_packets_with_blockers(session: AsyncSession, rows: list[ScenePacket]) -> list[ScenePacketOut]:
    """Router-facing reader: bulk-load active blockers ONCE, then overlay them onto each row's projection,
    so the facts are never 'missing' on this path (F6). Single semantic policy, async read boundary."""
    from dominion.workers.scene_packet.approval_policy import enrich_scene_packet_out

    facts = await active_blockers_for(session, [r.id for r in rows])
    return [scene_packet_out_with_blocker(enrich_scene_packet_out(r), facts.get(r.id, [])) for r in rows]
