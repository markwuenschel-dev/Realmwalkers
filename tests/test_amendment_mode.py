"""ChapterPacket amendment mode — the no-seed case (#261, ADR-0028 §38).

Amendment mode is the one repair normal re-derivation cannot perform: imported prose exists, the
chapter has an APPROVED ChapterPacket, and an affected scene has NO seed in that packet's body. A
merely-stale seed is a normal re-derive; a valid seed needs nothing at all. Only the genuine no-seed
case may enter amendment.

Why the eligibility predicate here is STRUCTURAL (seed present / absent) and never `ScenePacket.status
== STALE`: `source_hash` is computed from DIFFERENT payloads at derive vs recompute — `derive.py:576-584`
passes `canon_chunk_hashes` and `scene_pov`, `staleness.py:111-117` passes neither — so a packet derived
against populated canon is marked STALE on the next recompute whether or not anything drifted. STALE
therefore cannot distinguish "stale seed" from "no seed". Seed presence in
`ChapterPacket.body["scene_seeds"]` can, and is immune to that defect.

Needs Postgres (skips locally when it is down, runs under CI / `just test`).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dominion.shared.enums import PacketStatus, SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, Scene

# ----------------------------------------- seed helpers ------------------------------------------ #


async def _seed_chapter(s, *, title: str = "Amendment") -> tuple[Book, Chapter]:
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
) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        confidence="green",
        body={"scene_seeds": seeds if seeds is not None else []},
        open_questions={"items": []},
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


# --------------------------------- I2: DB-enforced single authority -------------------------------- #


async def test_second_approved_chapter_packet_is_rejected_by_the_database(db_factory):
    """Invariant 2: at most ONE active (approved) ChapterPacket per chapter, enforced by the DATABASE.

    RED before the partial unique index exists. Today the invariant is application-only — enforced
    solely by `packet._persist`'s delete-then-insert under the chapter lock — so any path that bypasses
    that seam (a direct insert, a migration slip, a future writer) creates two approved rows with no
    complaint. `draft_readiness.py:514-523` then resolves "the approved packet" with NO `ORDER BY`, so
    `GET /draft/readiness` silently picks an arbitrary one. That is the split-brain this closes.
    """
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        await _packet(s, book, ch, status=PacketStatus.APPROVED)
        await s.commit()
        book_id, ch_id = book.id, ch.id

    async with db_factory() as s:
        book = await s.get(Book, book_id)
        ch = await s.get(Chapter, ch_id)
        assert book is not None and ch is not None
        with pytest.raises(IntegrityError):
            await _packet(s, book, ch, status=PacketStatus.APPROVED)
            await s.commit()

    # The first one survives — a rejected second write must not damage the existing authority.
    async with db_factory() as s:
        n = (
            await s.execute(
                select(func.count()).select_from(ChapterPacket).where(ChapterPacket.status == PacketStatus.APPROVED)
            )
        ).scalar_one()
        assert n == 1, f"expected exactly one approved packet to survive, found {n}"


async def test_a_superseded_packet_frees_the_active_slot(db_factory):
    """Supersession must FREE the authority slot: the successor can only become approved because the
    predecessor left `approved`. Were `superseded` still covered by the index, amendment approval could
    never commit — so this pins the index's WHERE clause, not merely the enum value."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old = await _packet(s, book, ch, status=PacketStatus.APPROVED)
        new = await _packet(s, book, ch, status=PacketStatus.PROPOSED)
        # The real transition order, and the FLUSH between the two halves is load-bearing, not tidiness:
        # without it SQLAlchemy picks the UPDATE order itself and may promote the successor before
        # demoting the predecessor, which trips uq_chapter_packets_active_chapter. That the index
        # *catches* that ordering mistake is the invariant working — `_apply_authority_locked` flushes at
        # exactly this point for exactly this reason.
        old.status = PacketStatus.SUPERSEDED
        old.superseded_by_packet_id = new.id
        await s.flush()
        new.status = PacketStatus.APPROVED
        new.supersedes_packet_id = old.id
        await s.commit()
        old_id, new_id = old.id, new.id

    async with db_factory() as s:
        superseded = await s.get(ChapterPacket, old_id)
        active = await s.get(ChapterPacket, new_id)
        assert superseded is not None and active is not None
        assert str(superseded.status) == PacketStatus.SUPERSEDED.value
        assert str(active.status) == PacketStatus.APPROVED.value
        assert superseded.superseded_by_packet_id == new_id, "supersession must name its successor"
        assert active.supersedes_packet_id == old_id, "an approved amendment must name its predecessor"


async def test_superseded_without_a_successor_is_rejected(db_factory):
    """Invariant 3, the DB half: a `superseded` packet with no `superseded_by_packet_id` is an orphaned
    supersession — a packet demoted out of authority with nothing holding authority in its place. The
    CHECK constraint makes that state unreachable rather than merely unlikely."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        cp = await _packet(s, book, ch, status=PacketStatus.APPROVED)
        with pytest.raises(IntegrityError):
            cp.status = PacketStatus.SUPERSEDED
            cp.superseded_by_packet_id = None
            await s.commit()


async def test_model_output_may_not_be_recorded_as_a_chapter_approval_source(db_factory):
    """Invariant 8, structurally. At the SCENE tier `autonomous_policy` is a legal approval source
    (ADR-0030). At the CHAPTER tier it is not: no model output may approve, supersede, or select
    authority. The CHECK omits the value entirely, so adding an autonomous chapter approver needs a
    MIGRATION, not just a code edit — which is the enforcement, not a naming convention."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        cp = await _packet(s, book, ch, status=PacketStatus.APPROVED)
        with pytest.raises(IntegrityError):
            cp.approval_source = "autonomous_policy"
            await s.commit()


# ------------------------------ I1: eligibility — the genuine no-seed case ------------------------ #


async def test_eligibility_absent_seed_is_the_only_amendment_case(db_factory):
    """Invariant 1, the three-way split, keyed on seed PRESENCE (never on STALE):

    * a scene whose seed is present in the approved body -> NOT eligible (valid, or merely stale)
    * a scene whose seed is absent from the approved body -> eligible (the genuine no-seed case)
    """
    from dominion.workers.packet import amendment

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        kept = uuid.uuid4()
        await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(kept, 1)])
        await _imported_scene(s, ch, 1)
        orphan_scene = await _imported_scene(s, ch, 2)
        await s.commit()

        verdict = await amendment.assess_chapter(s, chapter_id=ch.id)
        assert verdict.eligible is True
        assert [str(x) for x in verdict.unseeded_scene_ids] == [str(orphan_scene.id)]
        assert verdict.reason == "unseeded_scenes_present"


async def test_eligibility_refuses_when_every_scene_has_a_seed(db_factory):
    """A chapter whose every imported scene resolves to a seed needs no adoption at all — amendment
    must refuse with a typed reason rather than proceed and burn a model call."""
    from dominion.workers.packet import amendment

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a, b = uuid.uuid4(), uuid.uuid4()
        await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1), _seed(b, 2)])
        await _imported_scene(s, ch, 1)
        await _imported_scene(s, ch, 2)
        await s.commit()

        verdict = await amendment.assess_chapter(s, chapter_id=ch.id)
        assert verdict.eligible is False
        assert verdict.reason == "all_scenes_seeded"


async def test_eligibility_refuses_without_an_approved_packet(db_factory):
    """No approved packet means this is the INITIAL adoption case, not amendment. Amendment is
    copy-on-write FROM an approved authority; with none, there is nothing to copy or to supersede."""
    from dominion.workers.packet import amendment

    async with db_factory() as s:
        _book, ch = await _seed_chapter(s)
        await _imported_scene(s, ch, 1)
        await s.commit()

        verdict = await amendment.assess_chapter(s, chapter_id=ch.id)
        assert verdict.eligible is False
        assert verdict.reason == "no_approved_packet"
