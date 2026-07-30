"""Production readers that dereference a STORED `chapter_packet_id` must not read a SUPERSEDED contract.

#261 added `PacketStatus.SUPERSEDED`, which made a previously-impossible state reachable: a
`chapter_packets` row that WAS the chapter's authority, is now terminal history, and is still pointed at
by rows written while it governed. `ScenePacket.chapter_packet_id` and `ChapterSequence.chapter_packet_id`
are plain stored pointers — nothing re-points them when an amendment lands — so every reader that
dereferences one without a status filter silently reads the REPLACED contract. No error, no log line, a
plausible-looking answer built from the body the author just discarded.

`amendment._stale_children_of` marks the superseded packet's ScenePackets STALE, but that does not close
the path: STALE is re-approvable by design, and re-approval clears `stale_reason` WITHOUT re-pointing
`chapter_packet_id`. A re-approved scene packet is therefore a live, approved row holding a dead pointer.

Each test below is RED without its fix, and the four fixes deliberately do NOT all make the same choice —
a drafting-authority reader FAILS CLOSED, a display projection RESOLVES, a one-click plan mutation
REFUSES, a metric EXCLUDES. The per-fix docstrings carry the reasoning; these tests pin the behaviour.

Needs Postgres (skips locally when it is down, runs under CI / `just test`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from dominion.shared.enums import ImportAdoptionMode, PacketStatus, ScenePacketStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket, ChapterSequence, Scene, ScenePacket

# ----------------------------------------- seed helpers ------------------------------------------ #
# Shape copied from tests/test_amendment_mode.py so the two files agree on what a packet/scene IS.


async def _seed_chapter(s, *, title: str = "Superseded readers") -> tuple[Book, Chapter]:
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
    body_extra: dict | None = None,
    origin_mode: ImportAdoptionMode = ImportAdoptionMode.INITIAL,
    qa_verdict: str | None = None,
) -> ChapterPacket:
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        confidence="green",
        qa_verdict=qa_verdict,
        body={"scene_seeds": seeds if seeds is not None else [], **(body_extra or {})},
        open_questions={"items": []},
        origin_mode=origin_mode.value,
    )
    s.add(cp)
    await s.flush()
    return cp


async def _supersede(s, predecessor: ChapterPacket, successor: ChapterPacket) -> None:
    """Hand the authority slot over exactly as `amendment._apply_authority_locked` does.

    The FLUSH between the two halves is REQUIRED, not tidiness: `uq_chapter_packets_active_chapter` is
    partial over `status = 'approved'`, so the successor cannot enter until the predecessor has left. Without
    the flush SQLAlchemy picks the UPDATE order itself and may promote before it demotes, which the index
    rejects.
    """
    predecessor.status = PacketStatus.SUPERSEDED
    predecessor.superseded_by_packet_id = successor.id
    predecessor.superseded_at = datetime.now(UTC)
    await s.flush()
    successor.status = PacketStatus.APPROVED
    successor.supersedes_packet_id = predecessor.id
    await s.flush()


async def _scene_packet(
    s, book: Book, ch: Chapter, packet: ChapterPacket, *, scene_no: int = 1, status: ScenePacketStatus
) -> ScenePacket:
    """A scene contract still pointing at `packet` — the row a STALE->re-approve cycle leaves behind."""
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=packet.id,
        scene_no=scene_no,
        status=status,
        qa_verdict="approve",
        body={"scene_no": scene_no, "scene_job": f"Scene {scene_no}", "required_beats": ["hold the line"]},
        source_hash=f"seed-{scene_no}",
    )
    s.add(sp)
    await s.flush()
    return sp


# ------------------- fix 1: beats.py — the cast comes from the CURRENT authority ------------------ #


async def test_beat_cast_resolves_the_chapters_current_approved_packet(db_factory):
    """`derive_beats` -> `_chapter_cast` used to dereference `ScenePacket.chapter_packet_id` with no status
    filter, so a scene packet still pointing at a superseded predecessor projected the PRE-amendment cast
    onto `beat.characters_present`. RED before the fix: the beat came out with the old ally in the cast and
    the new one missing, silently, with no error anywhere.

    The fix RESOLVES (does not refuse): a Beat is a display/routing projection that `derive_beats`
    re-reconciles in place, and the drafting path already fails closed in `workers/context/contracts.py`.
    """
    from dominion.workers.scene_packet.beats import derive_beats

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        old_seed, new_seed = uuid.uuid4(), uuid.uuid4()
        predecessor = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.APPROVED,
            seeds=[_seed(old_seed, 1)],
            body_extra={"characters_present": ["Marcus", "Old Ally"], "characters_absent": []},
        )
        amendment = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            seeds=[_seed(old_seed, 1), _seed(new_seed, 2)],
            body_extra={
                "characters_present": ["Marcus", "New Ally", "Ghost"],
                "characters_absent": ["Ghost"],
            },
            origin_mode=ImportAdoptionMode.AMENDMENT,
        )
        await _supersede(s, predecessor, amendment)

        # The reachable row: a scene contract that was staled by the supersession and then RE-APPROVED,
        # which clears stale_reason but leaves `chapter_packet_id` on the dead predecessor.
        await _scene_packet(s, book, ch, predecessor, scene_no=1, status=ScenePacketStatus.APPROVED)
        await s.commit()

        assert await derive_beats(s, chapter_id=ch.id) == 1
        await s.commit()

        beat = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalar_one()
        assert beat.characters_present == ["Marcus", "New Ally"], (
            "the beat cast must come from the chapter's CURRENT approved packet (minus its absent list), "
            f"not from the superseded predecessor it still points at; got {beat.characters_present!r}"
        )
        assert "Old Ally" not in (beat.characters_present or []), "pre-amendment cast leaked into the beat"


async def test_beat_cast_is_unknown_when_the_chapter_has_no_approved_packet(db_factory):
    """Authority vacated (every packet superseded, none re-taken): the honest answer is NO cast, not the
    last cast that happened to be on disk. Guards the fallback direction of the fix — resolving must not
    quietly degrade into "use whatever the pointer names"."""
    from dominion.workers.scene_packet.beats import derive_beats

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        seed_id = uuid.uuid4()
        predecessor = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.APPROVED,
            seeds=[_seed(seed_id, 1)],
            body_extra={"characters_present": ["Marcus", "Old Ally"]},
        )
        successor = await _packet(s, book, ch, status=PacketStatus.PROPOSED, seeds=[_seed(seed_id, 1)])
        await _supersede(s, predecessor, successor)
        # Give the slot up again: the successor is itself superseded and nothing is approved.
        orphan = await _packet(s, book, ch, status=PacketStatus.PROPOSED)
        successor.status = PacketStatus.SUPERSEDED
        successor.superseded_by_packet_id = orphan.id
        await s.flush()

        await _scene_packet(s, book, ch, predecessor, scene_no=1, status=ScenePacketStatus.APPROVED)
        await s.commit()

        assert await derive_beats(s, chapter_id=ch.id) == 1
        await s.commit()

        beat = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalar_one()
        assert beat.characters_present is None, (
            f"with no approved packet the cast is unknown, not stale; got {beat.characters_present!r}"
        )


# --------------- fix 2: production_sequence.py — align refuses a replaced contract ---------------- #


async def _sequence(s, book: Book, ch: Chapter, packet: ChapterPacket, *, target: int) -> ChapterSequence:
    seq = ChapterSequence(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=packet.id,
        status="proposed",
        target_scene_count=target,
        hard_max_scene_count=target,
        body={
            "scenes": [
                {"scene_no": 1, "scene_function": "open", "entry_state": "a", "exit_state": "b"},
                {"scene_no": 2, "scene_function": "turn", "entry_state": "b", "exit_state": "c"},
            ],
            "target_scene_count": target,
        },
    )
    s.add(seq)
    await s.flush()
    return seq


async def test_align_sequence_scene_count_refuses_a_superseded_contract(db_factory):
    """`align_sequence_scene_count` dereferenced `sequence.chapter_packet_id` with no status filter, then
    wrote `target_scene_count` from the superseded body and — via `update_chapter_sequence` ->
    `chapter_sequence_qa` — re-marked the sequence APPROVED. RED before the fix: the call SUCCEEDED, stamped
    the dead contract's seed count, and left the plan approved against a contract that no longer governs.

    It REFUSES rather than resolving the current approved packet: `scenes[]` is one-per-seed of the OLD
    body, so re-pointing only the scalar target would leave the plan internally inconsistent.
    """
    from dominion.workers.production_sequence import align_sequence_scene_count

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        predecessor = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1), _seed(b, 2)])
        amendment = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            seeds=[_seed(a, 1), _seed(b, 2), _seed(c, 3)],
            origin_mode=ImportAdoptionMode.AMENDMENT,
        )
        await _supersede(s, predecessor, amendment)
        seq = await _sequence(s, book, ch, predecessor, target=9)
        await s.commit()
        seq_id = seq.id

        with pytest.raises(ValueError) as err:
            await align_sequence_scene_count(s, seq_id)
        message = str(err.value)
        assert "approved contract" in message, message
        # The message must name the recovery, not just the refusal.
        assert "derive_chapter_sequence_for_chapter" in message, message

    # Nothing was written: not the scalar, not the status.
    async with db_factory() as s:
        reloaded = await s.get(ChapterSequence, seq_id)
        assert reloaded is not None
        assert reloaded.target_scene_count == 9, "a refused align must not stamp the superseded seed count"
        assert str(reloaded.status) == "proposed", "a refused align must not re-approve the sequence"


async def test_align_sequence_scene_count_still_aligns_against_the_approved_contract(db_factory):
    """The positive control: the guard must refuse a REPLACED contract only. A sequence pointing at the
    chapter's live approved packet still aligns, or the fix would have broken the feature it protects."""
    from dominion.workers.production_sequence import align_sequence_scene_count

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a, b = uuid.uuid4(), uuid.uuid4()
        approved = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1), _seed(b, 2)])
        seq = await _sequence(s, book, ch, approved, target=9)
        await s.commit()

        aligned = await align_sequence_scene_count(s, seq.id)
        assert aligned.target_scene_count == 2, "align must set the planning target to the packet's seed count"


# ------------- fix 3: books.py — the overview's authority is the APPROVED packet ------------------ #


async def test_chapters_overview_reports_the_approved_packet_and_the_amendment_separately(db_factory, app_client):
    """The overview took the NEWEST packet per chapter with no status filter, so the moment an amendment was
    PROPOSED the overview reported the amendment as the chapter's packet: `packet_status` flipped
    "approved" -> "proposed" and `packet_approval_state` was projected from the amendment, on a chapter that
    was still fully approved. RED before the fix on both fields.

    Driven through the real route so routing, `Depends` injection, and response-model serialization are all
    exercised — the DTO field is new, so serialization is part of what is under test.
    """
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a, b = uuid.uuid4(), uuid.uuid4()
        approved = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1), _seed(b, 2)])
        # A proposed amendment COEXISTS with its approved predecessor — that is the review state, and
        # `uq_chapter_packets_active_chapter` permits it because the index is partial over `approved`.
        amendment = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            seeds=[_seed(a, 1), _seed(b, 2)],
            origin_mode=ImportAdoptionMode.AMENDMENT,
        )
        amendment.supersedes_packet_id = approved.id
        await s.commit()
        book_id, approved_id, amendment_id = book.id, approved.id, amendment.id

    resp = await app_client.get(f"/books/{book_id}/chapters/overview")
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]

    assert row["packet_status"] == "approved", (
        f"a proposed amendment must not overwrite the chapter's authority; got {row['packet_status']!r}"
    )
    assert row["packet_approval_state"] == "already_approved", row["packet_approval_state"]
    assert row["open_amendment_packet_id"] == str(amendment_id), (
        "the open amendment must be reported as a SEPARATE sibling field"
    )
    assert str(approved_id) != row["open_amendment_packet_id"]


async def test_chapters_overview_follows_authority_to_the_approved_amendment(db_factory, app_client):
    """Once the amendment is APPROVED it IS the authority, and the superseded predecessor must vanish from
    the overview entirely (terminal history, never a chapter's packet) — including from the open-amendment
    field, which reports only a `proposed` branch."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a = uuid.uuid4()
        predecessor = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1)])
        amendment = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            seeds=[_seed(a, 1)],
            origin_mode=ImportAdoptionMode.AMENDMENT,
        )
        await _supersede(s, predecessor, amendment)
        await s.commit()
        book_id, amendment_id = book.id, amendment.id

    resp = await app_client.get(f"/books/{book_id}/chapters/overview")
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert row["packet_status"] == "approved"
    assert row["packet_approval_state"] == "already_approved"
    assert row["open_amendment_packet_id"] is None, "an APPROVED amendment is the authority, not an open branch"
    # And the authority really is the successor: it is the only approved row left.
    async with db_factory() as s:
        live = (
            await s.execute(select(ChapterPacket.id).where(ChapterPacket.status == PacketStatus.APPROVED))
        ).scalar_one()
        assert live == amendment_id


async def test_chapters_overview_still_reports_a_proposed_packet_as_proposed(db_factory, app_client):
    """The documented behaviour the approved-only authority must not lose: a chapter with NO approved packet
    still reads as `proposed`/`blocked` rather than as "no packet". Guards the reviewable fallback."""
    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        await _packet(s, book, ch, status=PacketStatus.PROPOSED, seeds=[_seed(uuid.uuid4(), 1)])
        await s.commit()
        book_id = book.id

    resp = await app_client.get(f"/books/{book_id}/chapters/overview")
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert row["packet_status"] == "proposed"
    assert row["packet_approval_state"] is not None
    assert row["open_amendment_packet_id"] is None


# ------------------ fix 4: agent_ops.py — one QA sample per LIVE chapter contract ----------------- #


async def test_packet_qa_pass_rate_counts_each_live_contract_once(db_factory):
    """`_qa_pass_rates` counted every ChapterPacket row in the 7-day window, so an amended chapter fed the
    packet-QA model TWO verdicts for largely the same material (amendment is copy-on-write from its
    predecessor). RED before the fix: predecessor `approve` + successor `revise_required` scored "50%".
    After it, only the live contract counts: 0 of 1 -> "0%"."""
    from dominion.shared import agent_ops

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a = uuid.uuid4()
        predecessor = await _packet(
            s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1)], qa_verdict="approve"
        )
        amendment = await _packet(
            s,
            book,
            ch,
            status=PacketStatus.PROPOSED,
            seeds=[_seed(a, 1)],
            origin_mode=ImportAdoptionMode.AMENDMENT,
            qa_verdict="revise_required",
        )
        await _supersede(s, predecessor, amendment)
        await s.commit()

        rates = await agent_ops._qa_pass_rates(s, datetime.now(UTC) - timedelta(days=7))
        assert rates["packet_qa_model"] == "0%", (
            "the superseded predecessor's verdict must not be counted a second time alongside its "
            f"successor's; got {rates['packet_qa_model']!r}"
        )


async def test_packet_qa_pass_rate_still_counts_live_packets(db_factory):
    """Positive control: excluding superseded rows must not empty the metric. Two live contracts, one
    passing verdict -> "50%"."""
    from dominion.shared import agent_ops

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        await _packet(s, book, ch, status=PacketStatus.APPROVED, qa_verdict="approve")
        await _packet(s, book, ch, status=PacketStatus.PROPOSED, qa_verdict="revise_required")
        await s.commit()

        rates = await agent_ops._qa_pass_rates(s, datetime.now(UTC) - timedelta(days=7))
        assert rates["packet_qa_model"] == "50%", rates["packet_qa_model"]


# ------------- fix 5: migrations.py — the #261 backfills reach an approved-then-superseded row ---- #


async def test_the_261_backfills_reach_a_superseded_packet(db_factory):
    """Both #261 backfills were gated on `status = 'approved'`, which can never match a row that WAS
    approved and is now `superseded` — leaving an approved-then-superseded contract with
    `approval_source IS NULL` ("never approved") and `approved_at IS NULL` forever.

    Unreachable on the intended upgrade path (a pre-#261 DB has no `superseded` rows, the status did not
    exist) but reachable whenever the migration meets a DB that already carries them: a restored dump from a
    post-#261 cluster, or a `create_all`-provisioned DB seeded before the backfills run — which is exactly
    the shape this test builds. Runs the REAL `_BACKFILLS` constant, not a copy of the SQL.
    """
    from dominion.shared.migrations import _BACKFILLS

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a = uuid.uuid4()
        predecessor = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1)])
        successor = await _packet(
            s, book, ch, status=PacketStatus.PROPOSED, seeds=[_seed(a, 1)], origin_mode=ImportAdoptionMode.AMENDMENT
        )
        await _supersede(s, predecessor, successor)
        # The pre-migration state: neither row has provenance yet (the real transition stamps it; a restored
        # dump predating the column, or a create_all-provisioned DB, does not).
        for row in (predecessor, successor):
            row.approval_source = None
            row.approved_at = None
        await s.commit()
        superseded_id = predecessor.id

        for sql in _BACKFILLS:
            await s.execute(text(sql))
        await s.commit()

        stamped = await s.get(ChapterPacket, superseded_id, populate_existing=True)
        assert stamped is not None
        assert str(stamped.status) == PacketStatus.SUPERSEDED.value
        assert stamped.approval_source == "legacy_unclassified", (
            "a superseded packet WAS approved — its provenance must not read 'never approved'; got "
            f"{stamped.approval_source!r}"
        )
        assert stamped.approved_at is not None, "a superseded packet WAS approved — it needs an approved_at"


async def test_the_261_backfills_still_skip_a_never_approved_packet(db_factory):
    """The widening must add `superseded` and nothing else: a `proposed` packet has never been approved, so
    stamping it would fabricate provenance the reviewer-trust split then treats as real."""
    from dominion.shared.migrations import _BACKFILLS

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        proposed = await _packet(s, book, ch, status=PacketStatus.PROPOSED)
        proposed.approval_source = None
        proposed.approved_at = None
        await s.commit()
        proposed_id = proposed.id

        for sql in _BACKFILLS:
            await s.execute(text(sql))
        await s.commit()

        row = await s.get(ChapterPacket, proposed_id, populate_existing=True)
        assert row is not None
        assert row.approval_source is None, f"a never-approved packet must stay unstamped; got {row.approval_source!r}"
        assert row.approved_at is None


# ----------------------- the drafting reader (already fixed) stays fixed -------------------------- #


async def test_the_drafting_contract_reader_still_fails_closed(db_factory):
    """`workers/context/contracts.load_scene_packet_fields` is the reader whose choice the others are
    calibrated against: it FAILS CLOSED on a superseded chapter contract because the scene contract was
    DERIVED from the replaced body. Pinned here so the softer choices made in beats.py can never be
    generalised onto the drafting path by a later reader-wide refactor."""
    from dominion.workers.context.contracts import load_scene_packet_fields
    from dominion.workers.context.types import ScenePacketRequiredError

    async with db_factory() as s:
        book, ch = await _seed_chapter(s)
        a = uuid.uuid4()
        predecessor = await _packet(s, book, ch, status=PacketStatus.APPROVED, seeds=[_seed(a, 1)])
        successor = await _packet(
            s, book, ch, status=PacketStatus.PROPOSED, seeds=[_seed(a, 1)], origin_mode=ImportAdoptionMode.AMENDMENT
        )
        await _supersede(s, predecessor, successor)
        sp = await _scene_packet(s, book, ch, predecessor, scene_no=1, status=ScenePacketStatus.APPROVED)
        # A drafted scene is not needed; the refusal happens before any prose is read.
        s.add(Scene(chapter_id=ch.id, scene_no=1, version=1, status=SceneStatus.DRAFT, prose="x"))
        await s.commit()

        with pytest.raises(ScenePacketRequiredError) as err:
            await load_scene_packet_fields(s, sp.id)
        assert "approved contract" in str(err.value), str(err.value)
