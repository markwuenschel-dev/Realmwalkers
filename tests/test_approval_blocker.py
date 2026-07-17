"""A1c slice 1 — the ApprovalBlocker boundary invariant (ADR-0031 D9/D14).

Invariant: No ScenePacket may remain APPROVED, or retain approved-derived beats, while it has an active
ApprovalBlocker. These pin the writer (demote-on-approved + beats reconcile), the fail-closed gate on
every approval path, the cross-table lock race, resolution semantics, idempotency/history, re-derive
survival, cascade purge, and the fail-closed projection overlay.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from dominion.shared.enums import ApprovalBlockerStatus, ScenePacketStatus
from dominion.shared.models import ApprovalBlocker, Beat, Book, Chapter, ChapterPacket, ScenePacket
from dominion.workers import scene_packet as sp_pipeline
from dominion.workers.scene_packet import approval_policy, blockers
from dominion.workers.scene_packet.blockers import ApprovalBlockerError


async def _seed(s, *, status: ScenePacketStatus = ScenePacketStatus.PROPOSED, scene_no: int = 1) -> ScenePacket:
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=3, pov="Mara")
    s.add(chapter)
    await s.flush()
    cp = ChapterPacket(book_id=book.id, chapter_id=chapter.id, status="approved", body={"scene_seeds": []})
    s.add(cp)
    await s.flush()
    packet = ScenePacket(
        book_id=book.id,
        chapter_id=chapter.id,
        chapter_packet_id=cp.id,
        scene_no=scene_no,
        status=status,
        body={"scene_no": scene_no},
    )
    s.add(packet)
    await s.flush()
    return packet


async def _count_active(s, scene_packet_id) -> int:
    return int(
        await s.scalar(
            select(func.count())
            .select_from(ApprovalBlocker)
            .where(
                ApprovalBlocker.scene_packet_id == scene_packet_id,
                ApprovalBlocker.status == ApprovalBlockerStatus.ACTIVE.value,
            )
        )
        or 0
    )


async def _beat_count(s, scene_packet_id) -> int:
    return int(
        await s.scalar(select(func.count()).select_from(Beat).where(Beat.scene_packet_id == scene_packet_id)) or 0
    )


async def test_raise_on_approved_demotes_and_reconciles_beats(db_factory):
    async with db_factory() as s:
        packet = await _seed(s, status=ScenePacketStatus.APPROVED)
        await sp_pipeline.reconcile_beats(s, chapter_id=packet.chapter_id)  # approved packet gets a beat
        await s.commit()
        assert await _beat_count(s, packet.id) == 1

        await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Whose blade?")
        await s.commit()

        got = await s.get(ScenePacket, packet.id)
        assert got.status == ScenePacketStatus.PROPOSED  # demoted — the blocker makes it un-approved
        assert await _count_active(s, packet.id) == 1
        assert await _beat_count(s, packet.id) == 0  # approved-derived beat pruned


async def test_approve_operation_refuses_when_active_blocker(db_factory):
    async with db_factory() as s:
        packet = await _seed(s)
        await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q?")
        await s.commit()
        with pytest.raises(ApprovalBlockerError):
            await sp_pipeline.approve_scene_packet(s, packet=packet)
        assert (await s.get(ScenePacket, packet.id)).status == ScenePacketStatus.PROPOSED


async def test_preloaded_approver_rereads_blocker_under_lock(db_factory):
    # NOT the F7 contention test (that is the concurrent pair below) — this pins the A1b populate_existing
    # lesson: an approver that PRE-LOADED the packet (a stale identity-map copy) before a blocker was
    # raised must, on locking, re-read the committed blocker and refuse — never approve off the stale copy.
    async with db_factory() as setup:
        packet = await _seed(setup)
        pid = packet.id
        await setup.commit()

    async with db_factory() as s1, db_factory() as s2:
        preloaded = await s1.get(ScenePacket, pid)  # sweeper/human-style pre-load (stale-able)
        assert preloaded.status == ScenePacketStatus.PROPOSED
        await blockers.raise_blocker(s2, scene_packet_id=pid, source_key="q1", question="Q?")
        await s2.commit()
        with pytest.raises(ApprovalBlockerError):
            await sp_pipeline.approve_scene_packet(s1, packet=preloaded)

    async with db_factory() as s:
        assert (await s.get(ScenePacket, pid)).status == ScenePacketStatus.PROPOSED
        assert await _count_active(s, pid) == 1


async def test_resolution_requires_rationale_and_source(db_factory):
    async with db_factory() as s:
        packet = await _seed(s)
        b = await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q?")
        await s.commit()
        with pytest.raises(ApprovalBlockerError):
            await blockers.resolve_blocker(s, blocker_id=b.id, rationale="", resolution_source="author")
        with pytest.raises(ApprovalBlockerError):
            await blockers.resolve_blocker(s, blocker_id=b.id, rationale="answered", resolution_source="   ")
        resolved = await blockers.resolve_blocker(s, blocker_id=b.id, rationale="answered", resolution_source="author")
        await s.commit()
        assert resolved.status == ApprovalBlockerStatus.RESOLVED.value
        assert resolved.resolved_at is not None
        assert resolved.resolution_rationale == "answered"
        assert resolved.resolution_source == "author"


async def test_idempotent_raise_then_new_history_after_resolve(db_factory):
    async with db_factory() as s:
        packet = await _seed(s)
        b1 = await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q?")
        b2 = await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q again?")
        assert b1.id == b2.id  # idempotent — one active row per (scene_packet_id, source, source_key)
        await blockers.resolve_blocker(s, blocker_id=b1.id, rationale="answered", resolution_source="author")
        b3 = await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Reopened?")
        await s.commit()
        assert b3.id != b1.id  # new history row after resolution
        total = await s.scalar(
            select(func.count()).select_from(ApprovalBlocker).where(ApprovalBlocker.scene_packet_id == packet.id)
        )
        assert total == 2
        assert await _count_active(s, packet.id) == 1


async def test_manual_blocker_survives_rederive(db_factory):
    async with db_factory() as s:
        packet = await _seed(s)
        await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q?")
        await s.commit()
        await sp_pipeline.reconcile_beats(s, chapter_id=packet.chapter_id)  # a re-derive/reconcile pass
        await s.commit()
        assert await _count_active(s, packet.id) == 1  # not superseded by re-derive (F4)


async def test_blocker_purged_on_scene_packet_delete(db_factory):
    async with db_factory() as s:
        packet = await _seed(s)
        await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Q?")
        await s.commit()
        assert await _count_active(s, packet.id) == 1
        await s.delete(await s.get(ScenePacket, packet.id))
        await s.commit()
        assert await _count_active(s, packet.id) == 0  # ON DELETE CASCADE = the explicit purge boundary


async def test_batch_approve_skips_blocked_packet(db_factory):
    async with db_factory() as s:
        p1 = await _seed(s)
        p2 = ScenePacket(
            book_id=p1.book_id,
            chapter_id=p1.chapter_id,
            chapter_packet_id=p1.chapter_packet_id,
            scene_no=2,
            status=ScenePacketStatus.PROPOSED,
            body={"scene_no": 2},
        )
        s.add(p2)
        await s.flush()
        await blockers.raise_blocker(s, scene_packet_id=p1.id, source_key="q1", question="Q?")
        await s.commit()

        approved, _ = await sp_pipeline.approve_scene_packets(s, chapter_id=p1.chapter_id, rows=[p1, p2])
        await s.commit()
        assert approved == 1  # only the unblocked p2
        assert (await s.get(ScenePacket, p1.id)).status == ScenePacketStatus.PROPOSED
        assert (await s.get(ScenePacket, p2.id)).status == ScenePacketStatus.APPROVED


async def test_projection_feeds_extra_gate_fail_closed_and_precedence(db_factory):
    # The projection feeds blocker facts into C2's extra_gate (it never overwrites C2's output), so
    # precedence stays held → blocker → approved → approvable, tri-state and fail closed.
    async with db_factory() as s:
        proposed = await _seed(s)
        # None = facts NOT loaded → fail closed (not approvable), never a silent "no blockers".
        unknown = approval_policy.project_scene_packet_out(proposed, None)
        assert unknown.can_approve is False
        assert unknown.approval_state == "blocker_unknown"
        # [] = loaded and none → base projection stands: a PROPOSED packet is approvable.
        clear = approval_policy.project_scene_packet_out(proposed, [])
        assert clear.can_approve is True
        assert clear.approval_state == "approvable"
        # A non-empty list surfaces EVERY open question (a list, not one joined string).
        b1 = ApprovalBlocker(
            scene_packet_id=proposed.id,
            chapter_id=proposed.chapter_id,
            source="manual_command",
            source_key="q1",
            question="Whose blade?",
            status="active",
        )
        b2 = ApprovalBlocker(
            scene_packet_id=proposed.id,
            chapter_id=proposed.chapter_id,
            source="manual_command",
            source_key="q2",
            question="Where is the ledger?",
            status="active",
        )
        blocked = approval_policy.project_scene_packet_out(proposed, [b1, b2])
        assert blocked.can_approve is False
        assert blocked.approval_state == "blocked_by_open_question"
        assert blocked.approval_blockers == ["Whose blade?", "Where is the ledger?"]

        # PRECEDENCE: a genuinely BLOCKED packet that ALSO has an active blocker keeps state "blocked"
        # (held wins) — it is never relabeled as an open-question hold.
        held_pkt = await _seed(s, status=ScenePacketStatus.BLOCKED)
        held_pkt.qa_warnings = {"blocked_reason": "author returned an incomplete body", "blocker_source": "author"}
        held = approval_policy.project_scene_packet_out(held_pkt, [b1])
        assert held.approval_state == "blocked"  # NOT "blocked_by_open_question"
        assert held.can_approve is False


async def test_f7_concurrent_raise_and_approve(db_factory):
    # F7 GENUINE contention (repair's gather spirit, not a sequential pre-load): raise_blocker and approve
    # race on the SAME packet, each in its own session + commit. The row's FOR UPDATE lock serializes them
    # and the invariant holds in BOTH interleavings — approve-first is demoted back by the raise;
    # raise-first makes approve refuse — so the packet is NEVER left APPROVED with an active blocker.
    async with db_factory() as setup:
        packet = await _seed(setup)
        pid = packet.id
        await setup.commit()

    async def _raise() -> str:
        async with db_factory() as s:
            await blockers.raise_blocker(s, scene_packet_id=pid, source_key="q1", question="Q?")
            await s.commit()
            return "raised"

    async def _approve() -> str:
        async with db_factory() as s:
            row = await s.get(ScenePacket, pid)
            try:
                await sp_pipeline.approve_scene_packet(s, packet=row)
                await s.commit()
                return "approved"
            except ApprovalBlockerError:
                return "approve-rejected"

    results = await asyncio.gather(_raise(), _approve())

    async with db_factory() as s:
        got = await s.get(ScenePacket, pid)
        active = await _count_active(s, pid)
        assert not (got.status == ScenePacketStatus.APPROVED and active > 0)  # the F7 invariant
        assert got.status == ScenePacketStatus.PROPOSED  # approve-first→demoted OR raise-first→never approved
        assert active == 1
    assert "raised" in results


async def test_f7_concurrent_resolve_and_approve(db_factory):
    # F7 GENUINE contention on the other seam: resolve (the ONLY way to clear a blocker) races an approve.
    # The row lock serializes them; the final state is consistent with who won — resolve-first → APPROVED;
    # approve-first refuses, then resolve clears it → PROPOSED — and never APPROVED with an active blocker.
    async with db_factory() as setup:
        packet = await _seed(setup)
        pid = packet.id
        b = await blockers.raise_blocker(setup, scene_packet_id=pid, source_key="q1", question="Q?")
        await setup.flush()  # apply the uuid PK default before capturing the id (raise doesn't flush here)
        bid = b.id
        await setup.commit()

    async def _resolve() -> str:
        async with db_factory() as s:
            await blockers.resolve_blocker(s, blocker_id=bid, rationale="answered", resolution_source="author")
            await s.commit()
            return "resolved"

    async def _approve() -> str:
        async with db_factory() as s:
            row = await s.get(ScenePacket, pid)
            try:
                await sp_pipeline.approve_scene_packet(s, packet=row)
                await s.commit()
                return "approved"
            except ApprovalBlockerError:
                return "approve-rejected"

    results = await asyncio.gather(_resolve(), _approve())

    async with db_factory() as s:
        got = await s.get(ScenePacket, pid)
        active = await _count_active(s, pid)
        assert active == 0  # resolve always clears the one blocker
        assert not (got.status == ScenePacketStatus.APPROVED and active > 0)
        if "approved" in results:
            assert got.status == ScenePacketStatus.APPROVED  # resolve won the lock first → approve saw none
        else:
            assert got.status == ScenePacketStatus.PROPOSED  # approve refused; resolve then cleared it
    assert "resolved" in results


async def test_route_parity_summary_and_detail_agree_on_blocker(db_factory):
    # Route parity: the Desk's two read paths (GET .../scene-packets/summary and GET /scene-packets/{id})
    # must agree on approval facts. Calling the real endpoint functions directly (they take an AsyncSession)
    # exercises the actual route code — including the summary's own field mapping — without an HTTP harness.
    from dominion.api.routers.scene_packets import get_scene_packet, list_scene_packet_summaries

    async with db_factory() as s:
        packet = await _seed(s)
        await blockers.raise_blocker(s, scene_packet_id=packet.id, source_key="q1", question="Whose blade?")
        await s.commit()

        summaries = await list_scene_packet_summaries(packet.chapter_id, s)
        detail = await get_scene_packet(packet.id, s)
        row = next(r for r in summaries if r.id == packet.id)

        # The shipped bug was the list/summary path advertising an active-blocker packet as approvable while
        # detail (also bypassed) did the same; both must now refuse and surface the same open question.
        assert row.can_approve is False
        assert detail.can_approve is False
        assert row.approval_state == "blocked_by_open_question"
        assert detail.approval_state == "blocked_by_open_question"
        assert row.approval_blockers == detail.approval_blockers == ["Whose blade?"]
