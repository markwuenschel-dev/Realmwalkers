"""ChapterPacket amendment mode under TORTURE — races, lock loss, replay, crash, drift, migration (#261).

`tests/test_amendment_mode.py` pins the HAPPY shape of amendment mode: which chapters are eligible, and
that the schema rejects two approved packets. This file attacks the transition itself. Every scenario here
is a way the ONE locked approve+supersede transaction (`workers/packet/amendment.py:363-484`) could leave
the chapter with two authorities, zero authorities, a doubled consequence set, or a half-applied
supersession — the states `uq_chapter_packets_active_chapter`,
`ck_chapter_packets_superseded_names_successor` and `ck_chapter_packets_amendment_names_predecessor` exist
to make unreachable rather than merely unlikely.

The load-bearing discipline in every test below: assertions are made from a FRESH session after the
operation, never from the in-memory objects the operation touched. A rolled-back transaction leaves
SQLAlchemy's identity map holding the mutated instances, so "the predecessor is still approved" asserted
on the same session would pass even if the rollback had never happened.

Needs Postgres (skips locally when it is down, runs under CI / `just test`).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from dominion.shared import migrations
from dominion.shared.chapter_lock import ChapterWorkflowBusy, acquire_chapter_workflow_lock
from dominion.shared.enums import (
    ChapterPacketApprovalSource,
    ImportAdoptionMode,
    PacketStatus,
    ScenePacketStatus,
    SceneStatus,
)
from dominion.shared.models import Book, Chapter, ChapterPacket, Scene, ScenePacket
from dominion.shared.prose_fingerprint import chapter_scene_rows, chapter_source_fingerprint
from dominion.workers.packet import amendment

# ----------------------------------------- seed helpers ------------------------------------------ #
# Same shape as tests/test_amendment_mode.py's helpers (tests here are self-contained by house style),
# extended with the amendment lineage columns and a ScenePacket child builder this file needs.


async def _seed_chapter(s, *, title: str = "Torture") -> tuple[Book, Chapter]:
    book = Book(title=title)
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline=None)
    s.add(ch)
    await s.flush()
    return book, ch


def _seed(seed_id: uuid.UUID, scene_no: int) -> dict:
    """A scene seed as the Packet Author mints it: a stable server-side `seed_id` plus display order."""
    return {"seed_id": str(seed_id), "scene_no": scene_no, "scene_job": f"Scene {scene_no} does its work."}


async def _packet(
    s,
    book: Book,
    ch: Chapter,
    *,
    status: PacketStatus,
    seeds: list[dict] | None = None,
    **extra,
) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        confidence="green",
        body={"scene_seeds": seeds if seeds is not None else []},
        open_questions={"items": []},
        **extra,
    )
    s.add(cp)
    await s.flush()
    return cp


async def _imported_scene(s, ch: Chapter, scene_no: int) -> Scene:
    sc = Scene(
        chapter_id=ch.id,
        scene_no=scene_no,
        version=1,
        prose=f"Imported prose for scene {scene_no}. The vault gate stands open.",
        status=SceneStatus.PENDING_REVIEW,
    )
    s.add(sc)
    await s.flush()
    return sc


async def _scene_packet(
    s,
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_packet_id: uuid.UUID,
    scene_no: int,
    status: ScenePacketStatus,
    stale_reason: str | None = None,
) -> ScenePacket:
    """A ScenePacket derived from `chapter_packet_id` — a CHILD of a chapter contract, which is what an
    approved amendment invalidates."""
    sp = ScenePacket(
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_packet_id=chapter_packet_id,
        scene_no=scene_no,
        status=status,
        qa_verdict="approve",
        body={"scene_no": scene_no},
        source_hash="torture",
        stale_reason=stale_reason,
    )
    s.add(sp)
    await s.flush()
    return sp


@dataclass(frozen=True)
class _World:
    """The identifiers of one amendable chapter, committed. Only ids cross the session boundary — an ORM
    instance carried out of its session is exactly the stale read these tests exist to catch."""

    book_id: uuid.UUID
    chapter_id: uuid.UUID
    predecessor_id: uuid.UUID
    amendment_id: uuid.UUID
    fingerprint: str


async def _world(s, *, title: str = "Torture") -> _World:
    """Commit the canonical amendable chapter: an APPROVED predecessor seeding scene 1 only, two imported
    scenes, and a PROPOSED amendment that copy-on-writes the predecessor's seed plus one for scene 2.

    The amendment's `source_fingerprint` is captured with the SAME production helpers the drift gate
    recomputes under the lock (`prose_fingerprint.chapter_scene_rows` / `chapter_source_fingerprint`) — a
    hand-rolled hash here would prove the gate compares two of the test's own numbers, not the chapter's.
    """
    book, ch = await _seed_chapter(s, title=title)
    kept = uuid.uuid4()
    predecessor = await _packet(
        s,
        book,
        ch,
        status=PacketStatus.APPROVED,
        seeds=[_seed(kept, 1)],
        origin_mode=ImportAdoptionMode.INITIAL,
        approval_source=ChapterPacketApprovalSource.MANUAL_COMMAND,
    )
    await _imported_scene(s, ch, 1)
    await _imported_scene(s, ch, 2)
    fingerprint = chapter_source_fingerprint(await chapter_scene_rows(s, ch.id))
    added = uuid.uuid4()
    amend = await _packet(
        s,
        book,
        ch,
        status=PacketStatus.PROPOSED,
        seeds=[_seed(kept, 1), _seed(added, 2)],
        origin_mode=ImportAdoptionMode.AMENDMENT,
        supersedes_packet_id=predecessor.id,
        source_fingerprint=fingerprint,
    )
    await s.commit()
    return _World(
        book_id=book.id,
        chapter_id=ch.id,
        predecessor_id=predecessor.id,
        amendment_id=amend.id,
        fingerprint=fingerprint,
    )


async def _approved_ids(s, chapter_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await s.execute(
            select(ChapterPacket.id).where(
                ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED
            )
        )
    ).all()
    return [r[0] for r in rows]


async def _count(s, model, *where) -> int:
    return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


_INDEX_PRESENT = "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_chapter_packets_active_chapter'"

_LEGACY_INSERT = """
    INSERT INTO chapter_packets (id, book_id, chapter_id, status, confidence, body, open_questions)
    VALUES (:i, :b, :c, 'approved', 'green', '{"scene_seeds": []}'::jsonb, '{"items": []}'::jsonb)
"""

_AMENDMENT_COLUMNS = """
    SELECT origin_mode, approval_source, approved_at, created_at, source_fingerprint,
           supersedes_packet_id, superseded_by_packet_id, amendment_scope
      FROM chapter_packets WHERE id = :i
"""


# ------------------------------------ 1. concurrent approvals ------------------------------------- #


async def test_two_concurrent_amendment_approvals_leave_exactly_one_authority(db_factory):
    """Invariant 2 under a REAL race: two independent sessions approve two different amendment packets for
    one chapter at the same time; exactly one may end up approved.

    Why it could regress: nothing in `_apply_authority_locked` is safe on its own — the guards read state
    the other transaction is mutating. Serialization comes entirely from `run_under_chapter_workflow`
    taking the per-chapter advisory lock BEFORE the body runs, with
    `uq_chapter_packets_active_chapter` as the backstop if that lock is ever dropped, scoped to the wrong
    key, or acquired after a row lock. Remove the lock and both bodies pass their predecessor check
    against the same `approved` row, then both try to enter the active slot.

    The rival branch is `blocked`, not `proposed`, and that is forced rather than chosen:
    `uq_chapter_packets_open_amendment` (migrations.py:333-335) makes TWO open amendment branches per
    chapter unreachable, so a second `proposed` amendment cannot be inserted at all. A `blocked` amendment
    is the only second amendment row that can coexist — and `_apply_authority_locked` accepts it as an
    approval target today (see the REPORT note on the missing `proposed` precondition), which is precisely
    why the race is expressible. If that precondition is later added, the rival simply loses
    deterministically and this test still holds: exactly one winner, exactly one approved packet.
    """
    async with db_factory() as s:
        w = await _world(s)
        book = await s.get(Book, w.book_id)
        ch = await s.get(Chapter, w.chapter_id)
        assert book is not None and ch is not None
        rival = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.BLOCKED,
            seeds=[_seed(uuid.uuid4(), 1), _seed(uuid.uuid4(), 2)],
            origin_mode=ImportAdoptionMode.AMENDMENT,
            supersedes_packet_id=w.predecessor_id,
            source_fingerprint=w.fingerprint,
        )
        await s.commit()
        rival_id = rival.id

    async def _approve(packet_id: uuid.UUID):
        async with db_factory() as s:
            return await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=packet_id)

    results = await asyncio.gather(_approve(w.amendment_id), _approve(rival_id), return_exceptions=True)

    wins = [r for r in results if isinstance(r, amendment.AuthorityOutcome)]
    losses = [r for r in results if isinstance(r, BaseException)]
    assert len(wins) == 1, f"exactly one approval may succeed; got {results!r}"
    assert len(losses) == 1, f"exactly one approval must fail; got {results!r}"
    # Asserted as a DISJUNCTION on purpose: which mechanism catches the loser depends on the interleaving
    # (lock wait -> predecessor already superseded, or the unique index if the lock is bypassed). Naming a
    # single type here would make the test pass or fail on scheduling luck rather than on the invariant.
    assert isinstance(losses[0], ChapterWorkflowBusy | amendment.AmendmentError | DBAPIError), repr(losses[0])
    winner = wins[0]
    assert winner.was_already_approved is False
    assert winner.superseded_packet_id == w.predecessor_id

    async with db_factory() as s:
        approved = await _approved_ids(s, w.chapter_id)
        assert approved == [winner.packet_id], f"expected exactly one approved packet, found {approved!r}"
        pred = await s.get(ChapterPacket, w.predecessor_id)
        assert pred is not None
        assert str(pred.status) == PacketStatus.SUPERSEDED.value
        assert pred.superseded_by_packet_id == winner.packet_id
        assert (
            await _count(
                s,
                ChapterPacket,
                ChapterPacket.chapter_id == w.chapter_id,
                ChapterPacket.status == PacketStatus.SUPERSEDED,
            )
            == 1
        ), "one race must produce ONE supersession, not two"


# ---------------------------------------- 2. lock timeout ----------------------------------------- #


async def test_lock_timeout_raises_busy_and_writes_nothing(db_factory):
    """The lock is acquired BEFORE the body, so a busy chapter never enters the transition at all: the
    approval must raise `ChapterWorkflowBusy` and leave every row exactly as it was.

    Why it could regress: `run_under_chapter_workflow` is what orders the advisory lock ahead of every row
    lock (`chapter_lock.py:134`). A refactor that moved the acquire inside the body, or that caught
    `ChapterWorkflowBusy` and continued "best effort", would turn a contended chapter into a partially
    applied supersession instead of a retryable 409.
    """
    async with db_factory() as s:
        w = await _world(s)

    async with db_factory() as holder, db_factory() as contender:
        # timeout_ms=None -> waits forever, and no commit, so the lock stays held for the whole test.
        await acquire_chapter_workflow_lock(holder, w.chapter_id, timeout_ms=None)
        with pytest.raises(ChapterWorkflowBusy):
            await amendment.approve_amendment(
                contender, chapter_id=w.chapter_id, packet_id=w.amendment_id, timeout_ms=250
            )
        await holder.rollback()  # release the transaction-scoped advisory lock

    async with db_factory() as s:
        pred = await s.get(ChapterPacket, w.predecessor_id)
        amend = await s.get(ChapterPacket, w.amendment_id)
        assert pred is not None and amend is not None
        assert str(pred.status) == PacketStatus.APPROVED.value, "the predecessor must still hold authority"
        assert pred.superseded_by_packet_id is None and pred.superseded_at is None
        assert str(amend.status) == PacketStatus.PROPOSED.value, "the amendment must still be reviewable"
        assert amend.approval_source is None and amend.approved_at is None
        assert amend.amendment_scope is None


# -------------------------------------- 3. idempotent replay -------------------------------------- #


async def test_replayed_approval_is_idempotent_and_supersedes_once(db_factory):
    """Invariant 5: a retried approval is a terminal SUCCESS, not a second supersession.

    Why it could regress: the only thing standing between a duplicate request and a doubled transition is
    the already-approved short circuit at `amendment.py:400-408`. Drop it and the second call re-enters the
    transition, re-stales the children (churning `stale_reason` and the consequence record), and either
    trips `ck_chapter_packets_superseded_names_successor` or leaves a second supersession behind.

    The first call's `staled_scene_packet_ids` is asserted NON-EMPTY first, deliberately: an empty tuple on
    the replay only means something once we have proved the same call on a fresh transition returns a
    populated one.
    """
    async with db_factory() as s:
        w = await _world(s)
        child = await _scene_packet(
            s,
            book_id=w.book_id,
            chapter_id=w.chapter_id,
            chapter_packet_id=w.predecessor_id,
            scene_no=1,
            status=ScenePacketStatus.APPROVED,
        )
        await s.commit()
        child_id = child.id

    async with db_factory() as s:
        first = await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)
    assert first.was_already_approved is False
    assert first.superseded_packet_id == w.predecessor_id
    assert first.staled_scene_packet_ids == (child_id,), "the first transition must have a real consequence set"

    async with db_factory() as s:
        second = await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)
    assert second.was_already_approved is True
    assert second.staled_scene_packet_ids == (), "a replay must claim no NEW consequences"
    assert second.superseded_packet_id == w.predecessor_id
    assert second.approval_source == ChapterPacketApprovalSource.MANUAL_COMMAND.value

    async with db_factory() as s:
        assert (
            await _count(
                s,
                ChapterPacket,
                ChapterPacket.chapter_id == w.chapter_id,
                ChapterPacket.status == PacketStatus.SUPERSEDED,
            )
            == 1
        ), "the replay must not create a second superseded row"
        assert await _approved_ids(s, w.chapter_id) == [w.amendment_id]
        amend = await s.get(ChapterPacket, w.amendment_id)
        assert amend is not None and amend.amendment_scope is not None
        assert amend.amendment_scope["staled_scene_packet_ids"] == [str(child_id)]
        assert amend.amendment_scope["predecessor_packet_id"] == str(w.predecessor_id)
        sp = await s.get(ScenePacket, child_id)
        assert sp is not None
        assert str(sp.status) == ScenePacketStatus.STALE.value
        assert sp.stale_reason == amendment.AMENDMENT_STALE_REASON


# --------------------------------------- 4. the drift gate ---------------------------------------- #


async def test_source_prose_drift_fails_closed_and_writes_nothing(db_factory):
    """Invariant 4: an amendment authored against prose that has since been hand-edited can NEVER be
    promoted to authority. `AmendmentSourceDrifted`, and nothing is written.

    The drift is produced by mutating `Scene.prose` in place — the real inbox hand-edit shape, and the
    exact mutation a version-based fingerprint would miss (`prose_fingerprint.py:3-7`). A bogus constant
    fingerprint would also make this test green while proving only that two unequal strings compare
    unequal; editing the prose proves the gate RECOMPUTES from the chapter's live rows under the lock.

    Why it could regress: the recompute at `amendment.py:414` is the only thing between a model-authored
    contract and prose it never saw. A pre-lock check, or a check against the packet's own stored
    fingerprint on both sides, would be worthless — the whole point is that prose can move while the
    authoring model call is in flight.
    """
    async with db_factory() as s:
        w = await _world(s)

    async with db_factory() as s:
        sc = (await s.execute(select(Scene).where(Scene.chapter_id == w.chapter_id, Scene.scene_no == 1))).scalar_one()
        sc.prose = f"{sc.prose} A later hand-edit: the vault gate slams shut."
        await s.commit()

    async with db_factory() as s:
        with pytest.raises(amendment.AmendmentSourceDrifted) as excinfo:
            await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)
    assert excinfo.value.expected == w.fingerprint
    assert excinfo.value.actual != w.fingerprint, "the recomputed fingerprint must reflect the prose edit"

    async with db_factory() as s:
        pred = await s.get(ChapterPacket, w.predecessor_id)
        amend = await s.get(ChapterPacket, w.amendment_id)
        assert pred is not None and amend is not None
        assert str(pred.status) == PacketStatus.APPROVED.value
        assert pred.superseded_by_packet_id is None and pred.superseded_at is None
        assert str(amend.status) == PacketStatus.PROPOSED.value
        assert amend.approval_source is None and amend.approved_at is None and amend.amendment_scope is None


# ------------------------------- 5. crash at the transition boundary ------------------------------ #


async def test_crash_after_the_authority_rows_moved_changes_nothing(db_factory, monkeypatch):
    """Invariant 6, and the single most important test in this file: a crash BEFORE the commit changes
    nothing at all — even when the crash lands after the authority rows have already been mutated and
    FLUSHED to the database inside the transaction.

    `_stale_children_of` is the injection point because it is called at `amendment.py:461`, i.e. inside the
    locked body and strictly AFTER the predecessor was demoted to `superseded` and the amendment promoted
    to `approved` (both flushed, `amendment.py:445` and `:454`). The stub PROVES that position rather than
    assuming it: it reads the predecessor's status back out of the live transaction and the test asserts it
    was already `superseded` at the moment of the crash. Without that check the test would pass equally
    well against a pre-flight refusal, which is a different and much weaker property.

    Why it could regress: atomicity here is owned entirely by `run_under_chapter_workflow`, which commits
    once at the end and rolls back on any exception (`chapter_lock.py:135-141`). Any `session.commit()`
    added inside the transition — a "checkpoint" commit between the supersede and the staling, say — would
    make this exact crash leave the chapter with a superseded predecessor, an approved amendment, and
    live authoritative children of a contract that no longer governs.
    """
    async with db_factory() as s:
        w = await _world(s)
        child = await _scene_packet(
            s,
            book_id=w.book_id,
            chapter_id=w.chapter_id,
            chapter_packet_id=w.predecessor_id,
            scene_no=1,
            status=ScenePacketStatus.APPROVED,
        )
        await s.commit()
        child_id = child.id

    seen: dict[str, object] = {}

    async def _boom(session, *, chapter_id, superseded_packet_id):
        rows = (
            await session.execute(
                text("SELECT id, status FROM chapter_packets WHERE chapter_id = :c ORDER BY status"),
                {"c": chapter_id},
            )
        ).all()
        seen["statuses"] = {r[0]: r[1] for r in rows}
        seen["superseded_packet_id"] = superseded_packet_id
        raise RuntimeError("staling the children exploded after the authority rows already moved")

    monkeypatch.setattr(amendment, "_stale_children_of", _boom)
    async with db_factory() as s:
        with pytest.raises(RuntimeError, match="exploded after the authority rows already moved"):
            await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)

    # The crash really was at the transition BOUNDARY: both halves had already hit the database.
    assert seen["superseded_packet_id"] == w.predecessor_id
    statuses = seen["statuses"]
    assert isinstance(statuses, dict)
    assert statuses[w.predecessor_id] == PacketStatus.SUPERSEDED.value
    assert statuses[w.amendment_id] == PacketStatus.APPROVED.value

    # …and the rollback erased both of them.
    async with db_factory() as s:
        pred = await s.get(ChapterPacket, w.predecessor_id)
        amend = await s.get(ChapterPacket, w.amendment_id)
        assert pred is not None and amend is not None
        assert str(pred.status) == PacketStatus.APPROVED.value, "the predecessor must still hold authority"
        assert pred.superseded_by_packet_id is None and pred.superseded_at is None
        assert str(amend.status) == PacketStatus.PROPOSED.value, "the amendment must still be reviewable"
        assert amend.approval_source is None and amend.approved_at is None and amend.amendment_scope is None
        assert await _approved_ids(s, w.chapter_id) == [w.predecessor_id]
        sp = await s.get(ScenePacket, child_id)
        assert sp is not None
        assert str(sp.status) == ScenePacketStatus.APPROVED.value, "no child may be staled by a crashed transition"
        assert sp.stale_reason is None
        assert await _count(s, ScenePacket, ScenePacket.stale_reason == amendment.AMENDMENT_STALE_REASON) == 0, (
            "a rolled-back transition must leave no amendment stale_reason anywhere"
        )


# --------------------------------- 6. predecessor changed underneath ------------------------------ #


async def test_predecessor_no_longer_the_authority_fails_closed(db_factory):
    """Invariant 3: an amendment whose predecessor stopped being the chapter's approved authority must
    fail closed with `AmendmentPredecessorMissing`, not proceed.

    The predecessor is demoted by ANOTHER path first (a rival packet supersedes it and takes the slot), so
    the amendment now names a `superseded` row. Approving it anyway would either produce two approved
    packets or supersede an already-superseded predecessor — the second of which would overwrite
    `superseded_by_packet_id` and silently rewrite the lineage chain.

    Why it could regress: the guard is the under-lock RELOAD at `amendment.py:426-435`, and it only works
    because `_reload_packet_locked` passes `populate_existing=True`. `session.get` alone returns the
    identity-mapped row without emitting SQL, so a caller that had already read the predecessor would have
    its PRE-LOCK copy handed back and the status check would pass against stale memory.
    """
    async with db_factory() as s:
        w = await _world(s)

    async with db_factory() as s:
        book = await s.get(Book, w.book_id)
        ch = await s.get(Chapter, w.chapter_id)
        pred = await s.get(ChapterPacket, w.predecessor_id)
        assert book is not None and ch is not None and pred is not None
        rival = await _packet(s, book, ch, status=PacketStatus.PROPOSED, origin_mode=ImportAdoptionMode.INITIAL)
        # The real transition order: the predecessor leaves `approved` (flush) before the rival enters it,
        # or uq_chapter_packets_active_chapter rejects the pair.
        pred.status = PacketStatus.SUPERSEDED
        pred.superseded_by_packet_id = rival.id
        await s.flush()
        rival.status = PacketStatus.APPROVED
        rival.supersedes_packet_id = pred.id
        await s.commit()
        rival_id = rival.id

    async with db_factory() as s:
        with pytest.raises(amendment.AmendmentPredecessorMissing):
            await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)

    async with db_factory() as s:
        amend = await s.get(ChapterPacket, w.amendment_id)
        pred = await s.get(ChapterPacket, w.predecessor_id)
        assert amend is not None and pred is not None
        assert str(amend.status) == PacketStatus.PROPOSED.value
        assert amend.approval_source is None and amend.approved_at is None and amend.amendment_scope is None
        assert str(pred.status) == PacketStatus.SUPERSEDED.value
        assert pred.superseded_by_packet_id == rival_id, "the refused approval must not rewrite the lineage"
        assert await _approved_ids(s, w.chapter_id) == [rival_id]


# ------------------------------- 7. supersession stales the children ------------------------------ #


async def test_supersession_stales_live_children_and_leaves_stale_ones_alone(db_factory):
    """Invariant 7: superseding a chapter contract invalidates the scene contracts derived from it, with a
    `stale_reason` that NAMES the cause — and the consequence set is recorded, not merely returned.

    Two children are seeded on the predecessor: one `approved` (must be staled) and one already `stale`
    carrying a DIFFERENT reason (must not be touched). The already-stale one is the discriminating case:
    `_stale_children_of`'s `status != STALE` filter is the only thing stopping a replay or a second
    supersession from overwriting an earlier, more specific diagnosis with the amendment string.

    Why it could regress: `AMENDMENT_STALE_REASON` is deliberately distinct from `staleness.py`'s generic
    "upstream inputs changed" message because the recovery differs and the reason is queryable
    (`amendment.py:54-58`). Collapsing the two, or widening the filter to every child row, would both be
    invisible without this test.
    """
    other_reason = "an earlier canon edit invalidated this contract"
    async with db_factory() as s:
        w = await _world(s)
        live = await _scene_packet(
            s,
            book_id=w.book_id,
            chapter_id=w.chapter_id,
            chapter_packet_id=w.predecessor_id,
            scene_no=1,
            status=ScenePacketStatus.APPROVED,
        )
        already = await _scene_packet(
            s,
            book_id=w.book_id,
            chapter_id=w.chapter_id,
            chapter_packet_id=w.predecessor_id,
            scene_no=2,
            status=ScenePacketStatus.STALE,
            stale_reason=other_reason,
        )
        await s.commit()
        live_id, already_id = live.id, already.id

    async with db_factory() as s:
        outcome = await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)
    assert outcome.staled_scene_packet_ids == (live_id,), "only the row that CHANGED may be reported"

    async with db_factory() as s:
        staled = await s.get(ScenePacket, live_id)
        untouched = await s.get(ScenePacket, already_id)
        amend = await s.get(ChapterPacket, w.amendment_id)
        assert staled is not None and untouched is not None and amend is not None
        assert str(staled.status) == ScenePacketStatus.STALE.value
        assert staled.stale_reason == amendment.AMENDMENT_STALE_REASON
        assert str(untouched.status) == ScenePacketStatus.STALE.value
        assert untouched.stale_reason == other_reason, "an already-stale child's diagnosis must survive"
        assert amend.amendment_scope is not None
        assert amend.amendment_scope["staled_scene_packet_ids"] == [str(live_id)], (
            "the persisted consequence record must match what the transition returned"
        )


# ----------------------- 8. no live authoritative child of a superseded packet -------------------- #


async def test_no_approved_scene_packet_survives_on_a_superseded_contract(db_factory):
    """Invariant 3's third clause: after a supersession, NO `approved` ScenePacket may still point at the
    superseded ChapterPacket. A live scene-local authority derived from a contract that no longer governs
    is the split-brain that lets a drafting agent write against a retired contract.

    Non-vacuity is proved first: the count of approved children on the predecessor is asserted to be 2
    BEFORE the approval, so the zero afterwards is a transition, not an empty table.

    Why it could regress: this is a consequence of `_stale_children_of` being called for EVERY
    supersession, and of it selecting on `chapter_packet_id` (not on `scene_no`, not on the new packet).
    An optimisation that staled only the children matching the amendment's changed seeds would satisfy
    test 7 and break this one.
    """
    async with db_factory() as s:
        w = await _world(s)
        for scene_no in (1, 2):
            await _scene_packet(
                s,
                book_id=w.book_id,
                chapter_id=w.chapter_id,
                chapter_packet_id=w.predecessor_id,
                scene_no=scene_no,
                status=ScenePacketStatus.APPROVED,
            )
        await s.commit()
        live_before = await _count(
            s,
            ScenePacket,
            ScenePacket.chapter_packet_id == w.predecessor_id,
            ScenePacket.status == ScenePacketStatus.APPROVED,
        )
    assert live_before == 2, "the predecessor must start with live authoritative children"

    async with db_factory() as s:
        outcome = await amendment.approve_amendment(s, chapter_id=w.chapter_id, packet_id=w.amendment_id)
    assert len(outcome.staled_scene_packet_ids) == 2

    async with db_factory() as s:
        pred = await s.get(ChapterPacket, w.predecessor_id)
        assert pred is not None and str(pred.status) == PacketStatus.SUPERSEDED.value
        live_after = await _count(
            s,
            ScenePacket,
            ScenePacket.chapter_packet_id == w.predecessor_id,
            ScenePacket.status == ScenePacketStatus.APPROVED,
        )
        assert live_after == 0, f"{live_after} approved ScenePacket(s) still claim authority on a retired contract"


# ------------------------------------ 9. migration over real data -------------------------------- #


async def test_lightweight_migration_classifies_a_legacy_approved_packet_idempotently(db_factory):
    """The #261 columns land on a POPULATED `chapter_packets` table, so the backfills — not the ORM
    defaults — are what a pre-amendment approved packet ends up with: `origin_mode='initial'` (every packet
    authored before amendment mode WAS an initial proposal) and `approval_source='legacy_unclassified'`
    (unproven provenance is not human provenance, `migrations.py:252-256`).

    The row is inserted with RAW SQL naming only the pre-amendment columns; going through the ORM would
    supply `origin_mode` and defeat the point. `apply_lightweight_migrations` is then run TWICE and the
    full amendment-column projection compared byte-for-byte, because these UPDATEs run on every boot: a
    backfill that is not self-gating would re-stamp `approved_at` on each restart, or (worse) relabel a
    packet a human really did approve as `legacy_unclassified`.
    """
    async with db_factory() as s:
        book, ch = await _seed_chapter(s, title="Legacy")
        await s.commit()
        book_id, chapter_id = book.id, ch.id

    engine = db_factory.kw["bind"]
    legacy_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(text(_LEGACY_INSERT), {"i": legacy_id, "b": book_id, "c": chapter_id})
        before = (await conn.execute(text(_AMENDMENT_COLUMNS), {"i": legacy_id})).mappings().one()
    # The legacy shape: approved, but never classified and never stamped.
    assert before["approval_source"] is None
    assert before["approved_at"] is None
    assert before["source_fingerprint"] is None
    assert before["amendment_scope"] is None
    assert before["origin_mode"] == ImportAdoptionMode.INITIAL.value

    async with engine.begin() as conn:
        await migrations.apply_lightweight_migrations(conn)
    async with engine.connect() as conn:
        first = dict((await conn.execute(text(_AMENDMENT_COLUMNS), {"i": legacy_id})).mappings().one())
    assert first["approval_source"] == ChapterPacketApprovalSource.LEGACY_UNCLASSIFIED.value
    assert first["origin_mode"] == ImportAdoptionMode.INITIAL.value
    assert first["approved_at"] == first["created_at"], "approved_at is stamped from created_at, not invented"
    assert first["supersedes_packet_id"] is None and first["superseded_by_packet_id"] is None

    async with engine.begin() as conn:
        await migrations.apply_lightweight_migrations(conn)
    async with engine.connect() as conn:
        second = dict((await conn.execute(text(_AMENDMENT_COLUMNS), {"i": legacy_id})).mappings().one())
    assert second == first, "the amendment backfills must be a no-op on every boot after the first"


# ----------------------------------- 10. the preflight fails closed ------------------------------- #


async def test_preflight_refuses_to_build_the_index_over_two_approved_packets(db_factory):
    """`_preflight_single_active_chapter_packet` must FAIL CLOSED — raise
    `DuplicateActiveChapterPacketError` and change nothing — when the table already holds two approved
    packets for one chapter, rather than letting `CREATE UNIQUE INDEX` fail cryptically or (in a future
    "helpful" version) picking a survivor. Which contract a book is written against is a human's decision.

    Reaching that state requires temporarily DROPPING `uq_chapter_packets_active_chapter`, because the
    index is exactly what makes it unreachable through the ORM. Everything happens inside one explicitly
    managed transaction that is ALWAYS rolled back: Postgres DDL is transactional, so the rollback restores
    the index along with erasing the rows. A belt-and-braces `apply_lightweight_migrations` then rebuilds
    it from the production DDL, and the test finishes by proving the index still FUNCTIONS — the fixture
    TRUNCATEs data but never rebuilds the schema, so a leaked drop here would silently disarm invariant 2
    for every later suite in the run.
    """
    async with db_factory() as s:
        book, ch = await _seed_chapter(s, title="Preflight")
        await s.commit()
        book_id, chapter_id = book.id, ch.id

    engine = db_factory.kw["bind"]
    async with engine.connect() as probe:
        assert (await probe.execute(text(_INDEX_PRESENT))).first() is not None, (
            "uq_chapter_packets_active_chapter must exist before this test drops it, or the drop proves nothing"
        )

    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.execute(text("DROP INDEX uq_chapter_packets_active_chapter"))
            for _ in range(2):
                await conn.execute(text(_LEGACY_INSERT), {"i": uuid.uuid4(), "b": book_id, "c": chapter_id})
            n = (
                await conn.execute(
                    text("SELECT count(*) FROM chapter_packets WHERE chapter_id = :c AND status = 'approved'"),
                    {"c": chapter_id},
                )
            ).scalar_one()
            assert n == 2, "the violating state must actually exist before the preflight is asked about it"

            with pytest.raises(migrations.DuplicateActiveChapterPacketError) as excinfo:
                await migrations._preflight_single_active_chapter_packet(conn)
            message = str(excinfo.value)
            assert "2 APPROVED chapter packets" in message
            assert str(chapter_id) in message, "the operator report must name the offending chapter"
            # Fails closed: it reports and refuses, it does not repair.
            assert (
                await conn.execute(
                    text("SELECT count(*) FROM chapter_packets WHERE chapter_id = :c AND status = 'approved'"),
                    {"c": chapter_id},
                )
            ).scalar_one() == 2
        finally:
            await trans.rollback()  # transactional DDL: undoes the inserts AND restores the dropped index
            async with engine.begin() as fix:
                await fix.execute(text("DELETE FROM chapter_packets WHERE chapter_id = :c"), {"c": chapter_id})
                await migrations.apply_lightweight_migrations(fix)

    async with engine.connect() as probe:
        assert (await probe.execute(text(_INDEX_PRESENT))).first() is not None, (
            "SCHEMA POISONED: uq_chapter_packets_active_chapter was not restored — every later DB suite in "
            "this run now has invariant 2 disarmed"
        )

    # A catalog row is not proof the index enforces anything; make it reject a real second approval.
    async with db_factory() as s:
        book = await s.get(Book, book_id)
        ch = await s.get(Chapter, chapter_id)
        assert book is not None and ch is not None
        await _packet(s, book, ch, status=PacketStatus.APPROVED)
        await s.commit()
    async with db_factory() as s:
        book = await s.get(Book, book_id)
        ch = await s.get(Chapter, chapter_id)
        assert book is not None and ch is not None
        with pytest.raises(IntegrityError):
            await _packet(s, book, ch, status=PacketStatus.APPROVED)
            await s.commit()
