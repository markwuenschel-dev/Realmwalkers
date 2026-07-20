"""Contract-first draft queue scheduler unit tests."""

from __future__ import annotations

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.shared.enums import BeatStatus, ScenePacketStatus
from dominion.shared.models import Beat, Book, Chapter, Job, ScenePacket
from dominion.workers.draft_queue import (
    DraftQueueBlocker,
    resolve_approved_scene_packet_for_beat,
    resolve_approved_scene_packet_for_beat_prefetched,
    schedule_contract_first_draft_jobs,
)


async def _chapter_with_beat(s):
    book = Book(title="DQ")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    return ch, beat


async def test_resolve_prefers_valid_beat_scene_packet_link(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat)
        assert isinstance(resolved, ScenePacket)
        assert resolved.id == sp.id


async def test_resolve_rejects_unapproved_scene_packet(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.PROPOSED
        await s.flush()
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason == "no_approved_scene_packet"


async def test_resolve_rejects_stale_scene_packet(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.STALE
        sp.stale_reason = "edited"
        await s.flush()
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason in ("scene_packet_stale", "no_approved_scene_packet")


async def test_resolve_rejects_duplicate_approved_scene_packets(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        await seed_scene_packet(s, chapter=ch, beat=None)
        sp2 = await seed_scene_packet(s, chapter=ch, beat=None)
        sp2.scene_no = 1
        await s.flush()
        beat.scene_packet_id = None
        resolved = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
        assert not isinstance(resolved, ScenePacket)
        assert resolved.reason == "duplicate_approved_scene_packets"


async def test_schedule_stamps_scene_packet_id_on_every_job(db_factory):
    async with db_factory() as s:
        ch, beat = await _chapter_with_beat(s)
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        result = await schedule_contract_first_draft_jobs(s, chapter=ch, beats=[beat], run=None)
        assert len(result.queued_job_ids) == 1
        job = await s.get(Job, result.queued_job_ids[0])
        assert job.scene_packet_id == sp.id
        assert job.beat_id == beat.id


# --- twin-parity: DB resolver vs its read-only prefetched twin --------------------------------------
# resolve_approved_scene_packet_for_beat (DB, draft_queue.py:139) and
# resolve_approved_scene_packet_for_beat_prefetched (draft_queue.py:198, "Same decision tree") are
# hand-duplicated with no parity guard, and the prefetched twin has no coverage of its own. These pin
# the two together across every decision branch — a divergence here is a real bug, not a flaky test.


def _outcome(result: ScenePacket | DraftQueueBlocker) -> tuple[str, object]:
    """Comparable 'packet-or-blocker(reason)' key for the twin-parity assertion below."""
    if isinstance(result, DraftQueueBlocker):
        return ("blocker", result.reason)
    return ("packet", result.id)


async def _prefetch_for_chapter(s, chapter_id):
    """Build the two prefetch dicts exactly as draft_readiness builds them for the live caller:
    compute_draft_readiness loads every ScenePacket for the chapter (any status), and
    derive_draft_readiness keys them by id and groups them by scene_no
    (draft_readiness.py:360-363). Mirror that construction so the twin sees production-shaped input."""
    sp_rows = list((await s.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars())
    packet_by_id = {p.id: p for p in sp_rows}
    packets_by_scene_no: dict[int, list[ScenePacket]] = {}
    for p in sp_rows:
        packets_by_scene_no.setdefault(p.scene_no, []).append(p)
    return packet_by_id, packets_by_scene_no


async def _assert_twins_agree(s, beat, chapter_id, *, expected):
    """The DB resolver (repair=False — the mode the prefetched twin mirrors, so it never mutates the
    beat) and the prefetched twin must return the same packet-or-blocker(reason)."""
    db = await resolve_approved_scene_packet_for_beat(s, beat=beat, repair=False)
    packet_by_id, packets_by_scene_no = await _prefetch_for_chapter(s, chapter_id)
    pref = resolve_approved_scene_packet_for_beat_prefetched(
        beat, packet_by_id=packet_by_id, packets_by_scene_no=packets_by_scene_no
    )
    assert _outcome(db) == _outcome(pref), f"twins diverged: DB {_outcome(db)} vs prefetched {_outcome(pref)}"
    assert _outcome(db) == expected


async def test_prefetched_twin_matches_db_resolver_across_branches(db_factory):
    """Head-to-head parity across the resolver's decision branches: valid link, missing scene_no,
    no-approved, duplicate, stale. Each branch uses its own chapter so the per-chapter prefetch dicts
    (built as draft_readiness builds them) stay scoped exactly as they are in production."""
    async with db_factory() as s:
        # 1) valid link — beat linked to its approved, non-stale packet; both return that packet.
        ch1, beat1 = await _chapter_with_beat(s)
        sp1 = await seed_scene_packet(s, chapter=ch1, beat=beat1)
        await _assert_twins_agree(s, beat1, ch1.id, expected=("packet", sp1.id))

        # 2) missing scene_no — short-circuits before any lookup. scene_no is NOT NULL in the schema,
        # so this beat stays transient (never added/flushed); the resolver reads it and returns first.
        ch2, _b2 = await _chapter_with_beat(s)
        no_scene_beat = Beat(chapter_id=ch2.id, scene_no=None, status=BeatStatus.APPROVED, beat_text="b")
        await _assert_twins_agree(s, no_scene_beat, ch2.id, expected=("blocker", "missing_scene_no"))

        # 3) no approved packet — a linked-but-unapproved (proposed) packet fails validation and falls
        # through to the scene_no lookup, which finds nothing approved.
        ch3, beat3 = await _chapter_with_beat(s)
        sp3 = await seed_scene_packet(s, chapter=ch3, beat=beat3)
        sp3.status = ScenePacketStatus.PROPOSED
        await s.flush()
        await _assert_twins_agree(s, beat3, ch3.id, expected=("blocker", "no_approved_scene_packet"))

        # 4) duplicate — two approved packets for the same scene, beat not linked, so both twins reach
        # the scene_no lookup and count > 1.
        ch4, beat4 = await _chapter_with_beat(s)
        await seed_scene_packet(s, chapter=ch4, beat=None)  # scene 1, approved
        sp4b = await seed_scene_packet(s, chapter=ch4, beat=None)  # scene 1, second approved
        assert beat4.scene_packet_id is None and sp4b.scene_no == 1
        await _assert_twins_agree(s, beat4, ch4.id, expected=("blocker", "duplicate_approved_scene_packets"))

        # 5) stale — a linked packet that went stale fails validation and, being non-approved, is not
        # rediscovered by the approved-only lookup, so BOTH twins report no_approved_scene_packet (the
        # DB twin's stale validation blocker is fall-through, not returned — draft_queue.py:157-174,
        # matching the hedge in test_resolve_rejects_stale_scene_packet above).
        ch5, beat5 = await _chapter_with_beat(s)
        sp5 = await seed_scene_packet(s, chapter=ch5, beat=beat5)
        sp5.status = ScenePacketStatus.STALE
        sp5.stale_reason = "edited"
        await s.flush()
        await _assert_twins_agree(s, beat5, ch5.id, expected=("blocker", "no_approved_scene_packet"))
