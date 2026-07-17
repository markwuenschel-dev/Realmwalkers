"""A1c slice 1 — the ApprovalBlocker boundary invariant (ADR-0031 D9/D14).

Invariant: No ScenePacket may remain APPROVED, or retain approved-derived beats, while it has an active
ApprovalBlocker. These pin the writer (demote-on-approved + beats reconcile), the fail-closed gate on
every approval path, the cross-table lock race, resolution semantics, idempotency/history, re-derive
survival, cascade purge, and the fail-closed projection overlay.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from dominion.shared.enums import ApprovalBlockerStatus, ScenePacketStatus
from dominion.shared.models import ApprovalBlocker, Beat, Book, Chapter, ChapterPacket, ScenePacket
from dominion.workers import scene_packet as sp_pipeline
from dominion.workers.scene_packet import blockers
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


async def test_cross_table_race_never_approved_with_active_blocker(db_factory):
    # F7: approver and writer both FOR UPDATE the ScenePacket row. A pre-loaded approver must re-read the
    # blocker committed under the lock (the A1b populate_existing lesson) and refuse — never leave the
    # packet APPROVED with an active blocker.
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


def test_projection_overlay_fails_closed():
    from dominion.shared.schemas import ScenePacketOut

    base = ScenePacketOut.model_construct(
        id=uuid.uuid4(), can_approve=True, approval_state="approvable", approval_blockers=[]
    )
    assert blockers.scene_packet_out_with_blocker(base, None).can_approve is False  # unknown → fail closed
    assert blockers.scene_packet_out_with_blocker(base, []).can_approve is True  # loaded, none → base
    b = ApprovalBlocker(
        scene_packet_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        source="manual_command",
        source_key="q1",
        question="Whose blade?",
        status="active",
    )
    out = blockers.scene_packet_out_with_blocker(base, [b])
    assert out.can_approve is False
    assert out.approval_state == "blocked_by_open_question"
    assert out.approval_blockers == ["Whose blade?"]
