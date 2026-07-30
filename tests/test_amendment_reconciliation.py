"""Boot verification of the chapter-packet authority lineage (#261, invariant 6 second half).

The invariant in one sentence: "a crash BEFORE commit changes nothing; a crash AFTER commit leaves a
complete state that boot reconciliation VERIFIES without guessing." The first half is already structural —
the ONE locked authority transition in `packet/amendment.py` demotes the predecessor, promotes the
successor and stales the orphaned children inside ONE `run_under_chapter_workflow` transaction, so a torn
half-state is unreachable rather than merely unlikely. This suite pins the SECOND half: what boot does when
it sees one anyway.

WHY THE SWEEP MUST NOT REPAIR, and why that is the thing under test. Four of the five states below are
forbidden by a DB constraint (`shared/migrations.py:326-387`), so observing one means a constraint was
BYPASSED — an index dropped by hand, a direct UPDATE, a writer that skipped the seam. A repair would then
have to choose which packet holds authority, i.e. which contract a book is written against; that is a
human's decision, and picking one would also erase the only evidence of how the state arose. So every test
here asserts BOTH halves: the finding is detected and durably reported, AND no ChapterPacket row moved.

HOW THE IMPOSSIBLE STATES ARE REACHED. Categories 4 and 5 are reachable through the plain ORM — 4 has no
constraint at all (it is upheld by `_stale_children_of` at supersession time, so any later route that
approves a ScenePacket bound to the retired packet re-creates it), and 5 is a multi-row condition no
single-row CHECK can express. Categories 1-3 require their specific constraint to be lifted for the
duration of one test, which is what `_constraint_lifted` does — and it RESTORES it in a `finally`, because
the `db_factory` fixture TRUNCATEs rows but never rebuilds the schema, so a leaked DROP would silently
disarm the real guard for every later test in the run. `test_the_suite_leaves_the_schema_intact` is the
proof that the restore actually happened, rather than a comment claiming it did.

Needs Postgres (skips locally when it is down, runs under CI / `just test`).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from sqlalchemy import func, select, text

from dominion.shared.enums import (
    ImportAdoptionMode,
    IntegrityHoldReason,
    PacketStatus,
    ScenePacketStatus,
)
from dominion.shared.models import Activity, Book, Chapter, ChapterPacket, ScenePacket
from dominion.workers.boot_reconciliation import (
    AUTHORITY_HOLD_CODE,
    chapter_authority_findings,
    reconcile_chapter_packet_authority,
)

# `integrity_hold` is a SHARED Activity kind — the D7/D8 revise sweep and the lifespan's ADR-0027
# job-ownership probe both emit it — so every assertion is scoped to THIS producer by hold_code, not just
# by kind/source. Scoping loosely is how another producer's row contaminates a count.
_IS_AUTHORITY_HOLD = (
    (Activity.kind == "integrity_hold")
    & (Activity.source == "reconciliation")
    & (Activity.payload_json["hold_code"].astext == AUTHORITY_HOLD_CODE)
)


# ----------------------------------------- seed helpers ------------------------------------------- #


async def _seed_chapter(s, *, title: str = "Authority") -> tuple[Book, Chapter]:
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
    origin_mode: ImportAdoptionMode = ImportAdoptionMode.INITIAL,
    seeds: list[dict] | None = None,
    supersedes_packet_id: uuid.UUID | None = None,
    superseded_by_packet_id: uuid.UUID | None = None,
) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        origin_mode=origin_mode,
        confidence="green",
        body={"scene_seeds": seeds if seeds is not None else []},
        open_questions={"items": []},
        supersedes_packet_id=supersedes_packet_id,
        superseded_by_packet_id=superseded_by_packet_id,
    )
    s.add(cp)
    await s.flush()
    return cp


async def _scene_packet(
    s, book: Book, ch: Chapter, cp: ChapterPacket, *, scene_no: int, status: ScenePacketStatus
) -> ScenePacket:
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_no=scene_no,
        status=status,
        body={"scene_no": scene_no, "word_budget": {"target": 900}},
        source_hash="test",
    )
    s.add(sp)
    await s.flush()
    return sp


async def _supersede(s, old: ChapterPacket, new: ChapterPacket) -> None:
    """The REAL transition order, step (4) of `packet/amendment.py`'s locked authority move: the
    predecessor leaves `approved` naming its successor, a FLUSH lands that, and only then does the successor
    take the freed slot. The flush is load-bearing, not tidiness — without it SQLAlchemy may order the two
    UPDATEs the other way round and trip `uq_chapter_packets_active_chapter`."""
    old.status = PacketStatus.SUPERSEDED
    old.superseded_by_packet_id = new.id
    await s.flush()
    new.status = PacketStatus.APPROVED
    new.supersedes_packet_id = old.id
    await s.flush()


async def _authority_holds(s) -> list[Activity]:
    return list((await s.execute(select(Activity).where(_IS_AUTHORITY_HOLD).order_by(Activity.seq))).scalars().all())


# ------------------------------- lifting one constraint, then restoring it ------------------------- #
#
# The restore DDL below is copied verbatim from `shared/migrations.py` (the index at :326-328, the two
# CHECKs at :362-379) rather than imported, on purpose: a test that re-derived the constraint from the same
# string the migration uses could not notice the migration changing shape. If they ever diverge,
# `test_the_suite_leaves_the_schema_intact` fails, which is the intended alarm.
#
# Note the two ADD CONSTRAINTs keep the migration's `DO $$ ... IF NOT EXISTS` catalog guard. That is not
# copied for tidiness: Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so a bare ALTER raises
# DuplicateObjectError if anything re-added the constraint first (`_setup_schema_once` re-runs
# `apply_lightweight_migrations`) — and an exception inside the restore is precisely the leak this whole
# apparatus exists to prevent. Idempotent restore, or no restore guarantee at all.

_UQ_ACTIVE_DROP = "DROP INDEX IF EXISTS uq_chapter_packets_active_chapter"
_UQ_ACTIVE_RESTORE = """CREATE UNIQUE INDEX IF NOT EXISTS uq_chapter_packets_active_chapter
       ON chapter_packets (chapter_id)
       WHERE status = 'approved'"""

_CK_SUCCESSOR_DROP = (
    "ALTER TABLE chapter_packets DROP CONSTRAINT IF EXISTS ck_chapter_packets_superseded_names_successor"
)
_CK_SUCCESSOR_RESTORE = """DO $$ BEGIN
         IF NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'ck_chapter_packets_superseded_names_successor'
         ) THEN
           ALTER TABLE chapter_packets ADD CONSTRAINT ck_chapter_packets_superseded_names_successor
             CHECK (status <> 'superseded' OR superseded_by_packet_id IS NOT NULL);
         END IF;
       END $$"""

_CK_PREDECESSOR_DROP = (
    "ALTER TABLE chapter_packets DROP CONSTRAINT IF EXISTS ck_chapter_packets_amendment_names_predecessor"
)
_CK_PREDECESSOR_RESTORE = """DO $$ BEGIN
         IF NOT EXISTS (
           SELECT 1 FROM pg_constraint WHERE conname = 'ck_chapter_packets_amendment_names_predecessor'
         ) THEN
           ALTER TABLE chapter_packets ADD CONSTRAINT ck_chapter_packets_amendment_names_predecessor
             CHECK (origin_mode <> 'amendment' OR status <> 'approved' OR supersedes_packet_id IS NOT NULL);
         END IF;
       END $$"""


@asynccontextmanager
async def _constraint_lifted(db_factory, *, drop: str, restore: str):
    """Temporarily remove ONE chapter_packets guard so the state it forbids can be constructed.

    The `finally` is the whole point. `db_factory` TRUNCATEs rows per test but never rebuilds the SCHEMA,
    so a DROP that leaks out of one test silently disarms the real constraint for every later test in the
    run — the suite would still be green while proving nothing. The impossible rows are deleted BEFORE the
    constraint returns, because Postgres cannot add a CHECK or a UNIQUE index over data that violates it,
    and a half-restored schema is the same failure with an extra step.

    The restore is RETRIED, and a total failure raises rather than passing quietly. Both matter because DDL
    can lose a lock race (a deadlock against another connection's TRUNCATE aborts the whole restore
    transaction), and "the restore threw, so the guard is gone and nobody was told" is the single worst
    outcome available here — every later DB test in the run would be unsound while still reporting green.
    """
    async with db_factory() as s:
        await s.execute(text(drop))
        await s.commit()
    try:
        yield
    finally:
        failure: Exception | None = None
        restored = False
        for _attempt in range(3):
            try:
                async with db_factory() as s:
                    await s.execute(text("DELETE FROM scene_packets"))
                    await s.execute(text("DELETE FROM chapter_sequences"))
                    await s.execute(text("DELETE FROM chapter_packets"))
                    await s.execute(text(restore))
                    await s.commit()
                restored = True
                break
            except Exception as exc:  # noqa: BLE001 — retried below, and re-raised loudly if all fail
                failure = exc
        if not restored:
            raise AssertionError(
                "FAILED TO RESTORE a chapter_packets guard after 3 attempts. The schema is now missing a "
                "real constraint, so every later DB test in this run is unsound — rebuild the test "
                f"database before trusting any result. Last error: {failure!r}"
            )


# ------------------------------------- the correct lineage --------------------------------------- #


async def test_a_correct_supersession_produces_no_findings(db_factory):
    """The baseline that makes every other test meaningful: a chapter whose lineage is exactly what
    `_apply_authority_locked` leaves behind — predecessor `superseded` naming its successor, amendment
    `approved` naming its predecessor, the predecessor's scene contracts staled — is CLEAN.

    A verification sweep that cannot stay silent over correct data is a pager that fires on every boot,
    which is functionally the same as no sweep at all. The locator must not even flag the chapter."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        # The predecessor's derived scene contract, in the state `_stale_children_of` leaves it.
        await _scene_packet(s, book, ch, old, scene_no=1, status=ScenePacketStatus.STALE)
        new = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            origin_mode=ImportAdoptionMode.AMENDMENT,
            seeds=[_seed(uuid.uuid4(), 1), _seed(uuid.uuid4(), 2)],
        )
        await _supersede(s, old, new)
        await s.commit()
        chapter_id = ch.id

        assert await chapter_authority_findings(s, chapter_id=chapter_id) == []

    report = await reconcile_chapter_packet_authority(db_factory)
    assert report.findings_total == 0
    assert report.scanned_chapters == 0, "a correct chapter must not even be a candidate"
    assert report.holds_recorded == 0

    async with db_factory() as s:
        assert await _authority_holds(s) == []


# --------------- category 4: live authoritative children of a retired contract -------------------- #


async def test_superseded_packet_with_an_approved_scene_packet_child_is_reported(db_factory):
    """Category 4 — invariant 3's third clause, "never a superseded packet with authoritative live
    children". The ONE genuinely reachable case: nothing constrains it, because it is upheld by
    `_stale_children_of` running at supersession time, so any later route that approves a ScenePacket
    still bound to the retired packet re-creates it. A scene drafted against that contract would obey a
    contract the chapter has retired.

    Reachable through the plain ORM — no constraint is lifted here."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        new = await _packet(s, book, ch, status=PacketStatus.PROPOSED, origin_mode=ImportAdoptionMode.AMENDMENT)
        await _supersede(s, old, new)
        # Approved AFTER the supersession, through some other route: the state category 4 exists to catch.
        orphan = await _scene_packet(s, book, ch, old, scene_no=1, status=ScenePacketStatus.APPROVED)
        await s.commit()
        chapter_id, old_id, new_id, orphan_id = ch.id, old.id, new.id, orphan.id

        findings = await chapter_authority_findings(s, chapter_id=chapter_id)
        assert [f.reason for f in findings] == [IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN]
        assert findings[0].packet_ids == (old_id,)
        assert findings[0].evidence["live_scene_packet_ids"] == [str(orphan_id)]

    report = await reconcile_chapter_packet_authority(db_factory)
    assert report.superseded_with_live_children == 1
    assert report.findings_total == 1
    assert report.holds_recorded == 1
    assert report.scanned_chapters == 1

    async with db_factory() as s:
        holds = await _authority_holds(s)
        assert len(holds) == 1, f"expected exactly one durable hold, got {len(holds)}"
        hold = holds[0]
        assert hold.payload_json["reason_code"] == IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN.value
        assert hold.payload_json["packet_ids"] == [str(old_id)]
        assert hold.payload_json["evidence"]["live_scene_packet_ids"] == [str(orphan_id)]
        assert hold.payload_json["repaired"] is False
        assert hold.severity == "error"
        # Scoped to the chapter AND the book, so the Desk can surface it where the author is working.
        assert hold.chapter_id == chapter_id and hold.book_id == book.id

        # ...and nothing was repaired. The sweep verifies; a human decides.
        assert str((await s.get(ChapterPacket, old_id)).status) == PacketStatus.SUPERSEDED.value
        assert str((await s.get(ChapterPacket, new_id)).status) == PacketStatus.APPROVED.value
        assert str((await s.get(ScenePacket, orphan_id)).status) == ScenePacketStatus.APPROVED.value
        assert (await s.get(ScenePacket, orphan_id)).chapter_packet_id == old_id


# ------------------------- category 5: authority vacated and never re-taken ----------------------- #


async def test_a_chapter_with_only_a_superseded_packet_is_reported(db_factory):
    """Category 5 — the chapter has a `superseded` packet and NO approved one, so no contract governs it.

    Reachable without lifting anything, and that is the interesting part: the successor here EXISTS (so
    `ck_chapter_packets_superseded_names_successor` is satisfied) but was never promoted past `proposed`.
    Every single-row CHECK holds; the chapter is still ungoverned. Only a per-chapter aggregate can see it,
    which is why this category exists at all."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        stillborn = await _packet(s, book, ch, status=PacketStatus.PROPOSED, origin_mode=ImportAdoptionMode.AMENDMENT)
        old.status = PacketStatus.SUPERSEDED
        old.superseded_by_packet_id = stillborn.id  # a real row — so category 2 must NOT fire
        await s.commit()
        chapter_id, old_id, stillborn_id = ch.id, old.id, stillborn.id

        findings = await chapter_authority_findings(s, chapter_id=chapter_id)
        assert [f.reason for f in findings] == [IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED]
        assert findings[0].packet_ids == (old_id,)
        assert findings[0].evidence["approved_count"] == 0

    report = await reconcile_chapter_packet_authority(db_factory)
    assert report.authority_vacated == 1
    assert report.findings_total == 1
    assert report.holds_recorded == 1

    async with db_factory() as s:
        holds = await _authority_holds(s)
        assert len(holds) == 1
        assert holds[0].payload_json["reason_code"] == IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED.value
        # No repair: the sweep did not promote the proposed amendment into the empty authority slot.
        assert str((await s.get(ChapterPacket, old_id)).status) == PacketStatus.SUPERSEDED.value
        assert str((await s.get(ChapterPacket, stillborn_id)).status) == PacketStatus.PROPOSED.value


# --------------------- category 1: two approved packets (index dropped by hand) -------------------- #


async def test_two_approved_packets_are_reported_when_the_unique_index_is_gone(db_factory):
    """Category 1 — the split-brain `uq_chapter_packets_active_chapter` exists to prevent. Detected anyway
    because a partial index CAN be dropped by hand, and this is exactly the state that makes
    `draft_readiness.py`'s approved-packet query (no ORDER BY) resolve an arbitrary contract: two boots
    could disagree about which contract governs the same chapter.

    The index is lifted for this test only and restored in the `finally`."""
    async with _constraint_lifted(db_factory, drop=_UQ_ACTIVE_DROP, restore=_UQ_ACTIVE_RESTORE):
        async with db_factory() as s:
            book, ch = await _seed_chapter(s)
            first = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
            second = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 2)])
            await s.commit()
            chapter_id, first_id, second_id = ch.id, first.id, second.id

            findings = await chapter_authority_findings(s, chapter_id=chapter_id)
            assert [f.reason for f in findings] == [IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS]
            assert set(findings[0].packet_ids) == {first_id, second_id}
            assert findings[0].evidence["approved_count"] == 2

        report = await reconcile_chapter_packet_authority(db_factory)
        assert report.multiple_approved == 1
        assert report.findings_total == 1
        assert report.holds_recorded == 1

        async with db_factory() as s:
            holds = await _authority_holds(s)
            assert len(holds) == 1
            assert holds[0].payload_json["reason_code"] == (IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS.value)
            assert sorted(holds[0].payload_json["packet_ids"]) == sorted([str(first_id), str(second_id)])
            # No repair: BOTH rows survive untouched. Choosing which contract governs a book is the
            # human's call, and demoting one here would erase the evidence of how the split arose.
            assert str((await s.get(ChapterPacket, first_id)).status) == PacketStatus.APPROVED.value
            assert str((await s.get(ChapterPacket, second_id)).status) == PacketStatus.APPROVED.value


# ----------------------- category 2: a supersession that names no live successor ------------------- #


async def test_superseded_with_a_null_successor_is_reported_when_the_check_is_gone(db_factory):
    """Category 2, the NULL half — an orphaned supersession: a packet demoted out of authority with
    nothing recorded as holding authority in its place. `ck_chapter_packets_superseded_names_successor`
    forbids it, so the CHECK is lifted for this test only.

    A second, APPROVED packet is seeded deliberately: without it the chapter would also trip category 5,
    and the assertion below would no longer prove which predicate fired."""
    async with _constraint_lifted(db_factory, drop=_CK_SUCCESSOR_DROP, restore=_CK_SUCCESSOR_RESTORE):
        async with db_factory() as s:
            book, ch = await _seed_chapter(s)
            orphan = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
            orphan.status = PacketStatus.SUPERSEDED
            orphan.superseded_by_packet_id = None
            await s.flush()
            await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
            await s.commit()
            chapter_id, orphan_id = ch.id, orphan.id

            findings = await chapter_authority_findings(s, chapter_id=chapter_id)
            assert [f.reason for f in findings] == [IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING]
            assert findings[0].packet_ids == (orphan_id,)
            assert findings[0].evidence["superseded_by_packet_id"] is None

        report = await reconcile_chapter_packet_authority(db_factory)
        assert report.supersession_successor_missing == 1
        assert report.findings_total == 1

        async with db_factory() as s:
            holds = await _authority_holds(s)
            assert len(holds) == 1
            assert holds[0].payload_json["reason_code"] == IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING.value
            reloaded = await s.get(ChapterPacket, orphan_id)
            assert str(reloaded.status) == PacketStatus.SUPERSEDED.value
            assert reloaded.superseded_by_packet_id is None, "the sweep must not invent a successor"


async def test_superseded_pointing_at_a_nonexistent_packet_is_reported(db_factory):
    """Category 2, the DANGLING half — and the reason this category is not redundant with the CHECK.
    `migrations.py:399-406` deliberately declines a self-referential FOREIGN KEY on the lineage columns
    (a whole-chapter contract delete would otherwise make per-row delete order load-bearing) and names
    this sweep as the compensating control. So a `superseded_by_packet_id` pointing at nothing satisfies
    every constraint in the schema, and NO constraint is lifted here."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        orphan = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        vanished = uuid.uuid4()
        orphan.status = PacketStatus.SUPERSEDED
        orphan.superseded_by_packet_id = vanished  # a contract-delete could leave exactly this
        await s.flush()
        await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        await s.commit()
        chapter_id, orphan_id = ch.id, orphan.id

        findings = await chapter_authority_findings(s, chapter_id=chapter_id)
        assert [f.reason for f in findings] == [IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING]
        assert findings[0].evidence["superseded_by_packet_id"] == str(vanished)
        assert findings[0].evidence["successor_exists"] is False

    report = await reconcile_chapter_packet_authority(db_factory)
    assert report.supersession_successor_missing == 1
    assert report.holds_recorded == 1

    async with db_factory() as s:
        assert len(await _authority_holds(s)) == 1
        assert (await s.get(ChapterPacket, orphan_id)).superseded_by_packet_id == vanished


# -------------------- category 3: an approved amendment that superseded nothing -------------------- #


async def test_approved_amendment_without_a_predecessor_is_reported_when_the_check_is_gone(db_factory):
    """Category 3 — an amendment is copy-on-write FROM an approved contract, so an APPROVED one that
    names no predecessor superseded nothing: either the predecessor was never demoted (and the chapter
    briefly had two authorities) or the lineage record was lost.
    `ck_chapter_packets_amendment_names_predecessor` forbids it, so the CHECK is lifted here only."""
    async with _constraint_lifted(db_factory, drop=_CK_PREDECESSOR_DROP, restore=_CK_PREDECESSOR_RESTORE):
        async with db_factory() as s:
            book, ch = await _seed_chapter(s)
            rogue = await _packet(
                s,
                book,
                ch,
                status=PacketStatus.APPROVED,
                origin_mode=ImportAdoptionMode.AMENDMENT,
                seeds=[_seed(uuid.uuid4(), 1)],
            )
            assert rogue.supersedes_packet_id is None
            await s.commit()
            chapter_id, rogue_id = ch.id, rogue.id

            findings = await chapter_authority_findings(s, chapter_id=chapter_id)
            assert [f.reason for f in findings] == [IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR]
            assert findings[0].packet_ids == (rogue_id,)
            assert findings[0].evidence["origin_mode"] == ImportAdoptionMode.AMENDMENT.value

        report = await reconcile_chapter_packet_authority(db_factory)
        assert report.amendment_without_predecessor == 1
        assert report.findings_total == 1

        async with db_factory() as s:
            holds = await _authority_holds(s)
            assert len(holds) == 1
            assert holds[0].payload_json["reason_code"] == (
                IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR.value
            )
            reloaded = await s.get(ChapterPacket, rogue_id)
            assert str(reloaded.status) == PacketStatus.APPROVED.value
            assert reloaded.supersedes_packet_id is None, "the sweep must not invent a predecessor"


# ---------------------------------------- bounded growth ------------------------------------------ #


async def test_a_second_sweep_over_the_same_bad_state_appends_nothing(db_factory):
    """Idempotency, and the reason it matters: `Activity` is APPEND-ONLY and the Desk reads it, so a sweep
    that re-emitted on every boot would bury the feed under one row per redeploy for a condition a human
    has already been told about. The dedup key hashes the offending-row SNAPSHOT, so an unchanged condition
    is reported exactly once and the second pass reports it as `deduped` instead of silently doing nothing.
    """
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(uuid.uuid4(), 1)])
        new = await _packet(s, book, ch, status=PacketStatus.PROPOSED, origin_mode=ImportAdoptionMode.AMENDMENT)
        await _supersede(s, old, new)
        await _scene_packet(s, book, ch, old, scene_no=1, status=ScenePacketStatus.APPROVED)
        await s.commit()

    first = await reconcile_chapter_packet_authority(db_factory)
    assert first.holds_recorded == 1 and first.holds_deduped == 0

    async with db_factory() as s:
        before = (await s.execute(select(func.count()).select_from(Activity).where(_IS_AUTHORITY_HOLD))).scalar_one()
    assert before == 1

    second = await reconcile_chapter_packet_authority(db_factory)
    # The condition is still FOUND — the finding count is unchanged — but it is not re-reported.
    assert second.findings_total == 1
    assert second.holds_recorded == 0
    assert second.holds_deduped == 1

    async with db_factory() as s:
        after = (await s.execute(select(func.count()).select_from(Activity).where(_IS_AUTHORITY_HOLD))).scalar_one()
    assert after == before, f"a repeated sweep appended {after - before} new Activity row(s)"


# ---------------------------- the restore actually happened (schema guard) ------------------------ #


async def test_the_suite_leaves_the_schema_intact(db_factory):
    """The tests above DROP real constraints. `db_factory` TRUNCATEs rows per test but never rebuilds the
    schema, so a leaked DROP would disarm the guard for every later test in the run — including
    `tests/test_amendment_mode.py`, whose whole point is that the DB rejects these states. This asserts the
    `finally` clauses put all four back. It runs last because pytest executes tests in file order."""
    async with db_factory() as s:
        index = (
            await s.execute(
                text("SELECT indexname FROM pg_indexes WHERE indexname = 'uq_chapter_packets_active_chapter'")
            )
        ).scalar_one_or_none()
        assert index == "uq_chapter_packets_active_chapter", "the single-authority unique index is MISSING"

        names = set(
            (
                await s.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'chapter_packets'::regclass "
                        "AND contype = 'c'"
                    )
                )
            )
            .scalars()
            .all()
        )
        for required in (
            "ck_chapter_packets_superseded_names_successor",
            "ck_chapter_packets_amendment_names_predecessor",
            "ck_chapter_packets_status",
        ):
            assert required in names, f"CHECK {required} is MISSING — a test dropped it and never restored it"
