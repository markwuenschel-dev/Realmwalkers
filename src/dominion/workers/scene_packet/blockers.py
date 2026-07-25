"""Approval Blocker boundary — the scene-tier hold that gates automated approval (A1c).

ADR-0031 D9/D14. A durable, scene-packet-scoped ApprovalBlocker holds approval; an ACTIVE blocker blocks
EVERY approval (human and autonomous) until an explicit resolver closes it with a rationale + source. The
write seam and the approval gate both `SELECT ... FOR UPDATE` the owning ScenePacket row, so a blocker can
never appear immediately after approval (the cross-table race, F7). Beats are a projection of approved
packets, so demoting an approved packet reconciles them.

Two sources raise one (`enums.ApprovalBlockerSource`):

* ``manual_command`` (slice 1) — a deliberate command through an explicit route.
* ``canon_conflict`` (slice 2) — raised AUTOMATICALLY by the derive path when scene-packet QA reports a
  canon-conflict finding on the freshly derived contract. Slice 1 shipped the channel with only a manual
  producer, which meant the hold was reachable only after a human had already spotted the problem — an
  escalation channel installed and unwired. `automatic_hold_for_qa` below is the trigger, and it is the
  ONLY place the trigger predicate lives.

Scope: this is the blocker boundary only. It does NOT deliver D9's durable Execution Authorization record
(the repair tier's authorization axis is `shared/authorization.py`; a scene-tier grant record is separate
and unbuilt), and does not touch draft/revise scheduling or downstream provenance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import ApprovalBlockerSource, ApprovalBlockerStatus, ScenePacketStatus
from dominion.shared.models import ApprovalBlocker, ScenePacket
from dominion.shared.risk_scorer import CANON_CONFLICT_KINDS, score_qa_result, should_semantic_escalate
from dominion.shared.schemas import ScenePacketOut

MANUAL_COMMAND = ApprovalBlockerSource.MANUAL_COMMAND.value
CANON_CONFLICT = ApprovalBlockerSource.CANON_CONFLICT.value

#: The `source_key` every automatic canon-conflict blocker uses. It is a CONSTANT on purpose: the
#: active partial-unique index is on `(scene_packet_id, source, source_key)`, so a stable key means one
#: active automatic hold per packet, and a re-derive that scores risky again is idempotent rather than
#: piling up a second row. A content-derived key (prose hash, issue set) would accumulate stale active
#: holds every re-derive, since nothing auto-resolves them (approval is not a resolution).
CANON_CONFLICT_KEY = "qa_canon_conflict"


class ApprovalBlockerError(ValueError):
    """A blocker operation refused (the router maps it to 409/422)."""


def automatic_hold_for_qa(qa: dict[str, Any] | None) -> str | None:
    """THE trigger for an automatic scene-tier hold (A1c slice 2). Returns the open question to raise, or
    None when the derived contract needs no human. The single place this predicate lives.

    **The line is canon conflict, not quality.** Issue #217's ratified policy (recorded on map #213) is
    that ADR-0029 claim-source precedence is the ONLY day-one escalation trigger — "ambiguities /
    confidence / qa do NOT gate; that is what Layer 2 learns". ADR-0030 says the same in its Layer 1
    paragraph: the objective floor is drawn by claim strength, not by a quality verdict. So this fires on
    a QA finding whose `kind` is in `risk_scorer.CANON_CONFLICT_KINDS`, and on nothing else.

    Relationship to the wider risk score, stated so the narrowing is legible rather than accidental:
    `score_qa_result` returns MEDIUM whenever `canon_count >= 1`, so every hold raised here would also
    satisfy `should_semantic_escalate` — this predicate is a strict SUBSET of that signal. It deliberately
    does NOT fire on a bare REVISE_REQUIRED verdict or on five-plus residual risks, both of which would
    hold approval on ordinary editorial noise. Whether to widen to the full risk score is an open item in
    ADR-0033 and is one line here.

    One consequence worth stating plainly: an ACTIVE blocker holds the HUMAN Approve button too, not only
    an automated approver. That is slice 1's landed semantics (`blockers.raise_blocker` demotes an already
    APPROVED packet), and the remedy is to resolve the hold with a rationale.
    """
    if not isinstance(qa, dict):
        return None
    issues = qa.get("issues")
    if not isinstance(issues, list):
        return None
    kinds = sorted(
        {
            str(item.get("kind", "")).lower()
            for item in issues
            if isinstance(item, dict) and str(item.get("kind", "")).lower() in CANON_CONFLICT_KINDS
        }
    )
    if not kinds:
        return None
    level = score_qa_result(qa)
    assert should_semantic_escalate(level)  # a canon conflict always scores MEDIUM or higher
    verdict = qa.get("verdict")
    verdict_s = str(getattr(verdict, "value", verdict) or "none")
    return (
        f"Automated QA found a canon conflict in this derived scene contract: {', '.join(kinds)} "
        f"(verdict: {verdict_s}; risk: {level.value}). Rule on the conflict before this packet is "
        "approved, then resolve this hold with a rationale."
    )


async def lock_packet(session: AsyncSession, scene_packet_id: uuid.UUID) -> ScenePacket:
    """The shared approval-lock seam. `with_for_update` + `populate_existing`: acquire the row lock AND
    refresh a possibly-stale identity-map copy (the A1b lesson). Every writer/approver/resolver locks the
    SAME row, so raise ↔ approve ↔ resolve serialize on it (A1c F7)."""
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
    active blocker, or retain approved-derived beats. `source` is provenance from
    `enums.ApprovalBlockerSource`: `manual_command` (a deliberate route, not an authenticated actor) or
    `canon_conflict` (the derive path's automatic hold). The caller commits."""
    if not source_key or not source_key.strip():
        raise ApprovalBlockerError("source_key is required")
    if not question or not question.strip():
        raise ApprovalBlockerError("question is required")
    packet = await lock_packet(session, scene_packet_id)
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
    # F7: lock the owning ScenePacket row before mutating a blocker, so resolve serializes with a
    # concurrent raise/approve on the same packet (all three take the same row lock); re-read the blocker
    # under that lock so a concurrent resolve can't double-close it.
    await lock_packet(session, blocker.scene_packet_id)
    await session.refresh(blocker)
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


async def scene_out_with_blockers(session: AsyncSession, row: ScenePacket) -> ScenePacketOut:
    """Blocker-aware projection for a SINGLE packet-bearing response (detail, approve, PUT). Loading is
    trivial here (one id), but the point is that this path ALWAYS loads the facts, so approval fields are
    never 'missing' (F6). Precedence lives in the scene projection's `extra_gate`, not here."""
    from dominion.workers.scene_packet.approval_policy import project_scene_packet_out

    facts = await active_blockers_for(session, [row.id])
    return project_scene_packet_out(row, facts.get(row.id, []))


async def scene_outs_with_blockers(session: AsyncSession, rows: list[ScenePacket]) -> list[ScenePacketOut]:
    """Blocker-aware projection for every LIST / batch response (list, summary, batch approve, mark-stale,
    derive). Bulk-load active blockers ONCE, then project each row through the scene policy's `extra_gate`
    — one query, facts never missing (A1c F6). The single router-facing scene-packet read path."""
    from dominion.workers.scene_packet.approval_policy import project_scene_packet_out

    facts = await active_blockers_for(session, [r.id for r in rows])
    return [project_scene_packet_out(r, facts.get(r.id, [])) for r in rows]
