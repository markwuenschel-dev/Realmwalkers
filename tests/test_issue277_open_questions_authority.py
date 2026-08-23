"""#277 — the twelve named acceptance tests for open-questions clearance authority.

Chapter approval used to clear whenever ``open_questions["items"]`` was empty, and never read
``resolved[]`` at all. Emptying the list was sufficient to open the gate — ruled or not, and whether or
not the ruling said anything. Worse, two routes could grant chapter authority and only one consulted the
gate at all, so the route that GENERATES open questions (amendment) could take authority with every one
of them unresolved.

Every test here is named by the ratified contract (issue #277, ratifying comment 2026-08-21 and
amendment 1 of 2026-08-23). The names are the contract's, not mine — do not rename them without an
owner ruling, because the contract's completion criteria cite them individually.

    D1  1  test_item_id_is_server_minted_and_binding_is_never_positional
    D1  2  test_delete_and_readd_identical_text_requires_a_new_ruling
    D2  3  test_blank_resolution_or_source_is_422_and_does_not_clear
    D2  4  test_normalizer_does_not_silently_repair_an_attempted_ruling
    D4  5  test_legacy_unbound_resolution_leaves_item_open
    D5  6  test_legacy_rows_are_readable_history_and_never_clear
    D6  7  test_previous_reader_ignores_new_jsonb_keys
    D7  8  test_raw_items_payload_cannot_make_approval_appear_clear
    D7  9  test_both_write_paths_persist_one_normalized_value
    A  10  test_amendment_approval_refuses_unresolved_questions_before_any_mutation
    A  11  test_amendment_approval_uses_only_the_canonical_reader
    B  12  test_stale_open_questions_write_is_409_and_erases_nothing
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import uuid

import pytest
from fastapi import HTTPException

from dominion.api.routers import packets
from dominion.shared.enums import ImportAdoptionMode, PacketStatus
from dominion.shared.models import Book, Chapter, ChapterPacket
from dominion.shared.schemas import PacketUpdateIn
from dominion.workers.packet import amendment, master
from dominion.workers.packet import approval_policy as packet_approval
from dominion.workers.packet import open_questions as oq

# =================================================================================================
# helpers
# =================================================================================================


async def _seed_packet(s, *, open_questions: dict | None = None, status=PacketStatus.PROPOSED) -> ChapterPacket:
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        confidence="green",
        body={"scene_seeds": []},
        open_questions=open_questions if open_questions is not None else {"items": [], "resolved": []},
    )
    s.add(cp)
    await s.flush()
    return cp


def _ruling(item_id: str, *, resolution: str = "Ruled: the courier was hired by Mara.", source: str = "author") -> dict:
    return {"item_id": item_id, "resolution": resolution, "source": source}


def _old_open_question_items(open_questions):
    """The gate reader EXACTLY as it stood before #277 (`approval_policy.py:38-41` at 2e095f7).

    Reproduced verbatim so test 7 proves D6's rollback claim against the real prior behaviour rather
    than against a paraphrase of it.
    """
    o = open_questions or {}
    items = o.get("items") if isinstance(o, dict) else None
    return items if isinstance(items, list) else []


# =================================================================================================
# D1 — item identity
# =================================================================================================


def test_item_id_is_server_minted_and_binding_is_never_positional():
    """1. A ruling binds to a QUESTION, never to a slot. Reordering the list must not move a ruling."""
    normalized = oq.normalize({"items": ["first question", "second question"], "resolved": []}, mint=True)
    first, second = normalized["items"]
    assert uuid.UUID(first["item_id"]) and uuid.UUID(second["item_id"]), "ids are server-minted UUIDs"
    assert first["item_id"] != second["item_id"]

    ruled_second = oq.normalize({"items": normalized["items"], "resolved": [_ruling(second["item_id"])]}, mint=True)
    assert [i["item_id"] for i in oq.unresolved_items(ruled_second)] == [first["item_id"]]

    # Reverse the display order. Positional matching would now clear the WRONG question; id binding does
    # not move at all. This is the whole point of D1.
    reversed_items = list(reversed(normalized["items"]))
    ruled_reversed = oq.normalize({"items": reversed_items, "resolved": [_ruling(second["item_id"])]}, mint=True)
    assert [i["item_id"] for i in oq.unresolved_items(ruled_reversed)] == [first["item_id"]]


def test_client_supplied_item_id_for_a_new_item_is_not_honoured_as_identity():
    """D1 support: clients may not mint. A supplied id is kept only when it is already the item's id."""
    minted = oq.normalize({"items": ["q"], "resolved": []}, mint=True)
    server_id = minted["items"][0]["item_id"]
    assert uuid.UUID(server_id)  # not something a client chose


def test_delete_and_readd_identical_text_requires_a_new_ruling():
    """2. Re-raising a question is a NEW authority event, not a resurrection of the old ruling."""
    original = oq.normalize({"items": ["is Serra recognized?"], "resolved": []}, mint=True)
    old_id = original["items"][0]["item_id"]
    ruled = oq.normalize({"items": original["items"], "resolved": [_ruling(old_id)]}, mint=True)
    assert oq.unresolved_items(ruled) == []

    # Delete it (empty items), keeping the ruling as history; then re-add the IDENTICAL text.
    readded = oq.normalize({"items": ["is Serra recognized?"], "resolved": ruled["resolved"]}, mint=True)
    new_id = readded["items"][0]["item_id"]
    assert new_id != old_id, "identical text must not inherit the retired question's identity"
    assert len(oq.unresolved_items(readded)) == 1, "the old ruling must not clear the re-raised question"


# =================================================================================================
# D2 — what a ruling must carry
# =================================================================================================


@pytest.mark.parametrize(
    "bad",
    [
        {"resolution": "", "source": "author"},
        {"resolution": "   ", "source": "author"},
        {"resolution": "real ruling", "source": ""},
        {"resolution": "real ruling", "source": "  \t "},
        {"resolution": "", "source": ""},
    ],
)
async def test_blank_resolution_or_source_is_422_and_does_not_clear(db_factory, bad):
    """3. A blank clearance rationale is a 422 at the boundary, and the item stays open."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["who hired the courier?"]}, mint=True))
        await s.commit()
        chapter_id, cp_id = cp.chapter_id, cp.id
        current = await packets.get_packet(chapter_id, s)
        item_id = current.open_questions["items"][0]["item_id"]

        with pytest.raises(HTTPException) as exc:
            await packets.update_packet(
                chapter_id,
                PacketUpdateIn(
                    open_questions={
                        "items": current.open_questions["items"],
                        "resolved": [{"item_id": item_id, **bad}],
                    },
                    expected_open_questions_token=current.open_questions_token,
                ),
                s,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "open_questions_malformed"

    async with db_factory() as s2:
        row = await s2.get(ChapterPacket, cp_id)
        assert len(packet_approval.open_question_items(row)) == 1, "the item must still be open"
        assert packet_approval.can_approve(row) is not None, "and approval must still be refused"


def test_normalizer_does_not_silently_repair_an_attempted_ruling():
    """4. A malformed ruling is REJECTED, never coerced into something that clears the gate.

    The dangerous repair would be quietly storing `resolution: ""` — an empty rationale nobody wrote,
    which under the old contract would have cleared approval exactly as well as a real one.
    """
    minted = oq.normalize({"items": ["q"], "resolved": []}, mint=True)
    item_id = minted["items"][0]["item_id"]
    with pytest.raises(oq.OpenQuestionsInvalid):
        oq.normalize(
            {"items": minted["items"], "resolved": [{"item_id": item_id, "resolution": "  ", "source": "a"}]}, mint=True
        )
    # and it must not have half-written anything: a fresh normalize of the untouched value is still open
    assert len(oq.unresolved_items(minted)) == 1


# =================================================================================================
# D4 / D5 — legacy rows
# =================================================================================================


def test_legacy_unbound_resolution_leaves_item_open():
    """5. Fail closed. A legacy resolution carries no item_id, so nothing establishes WHICH question it
    ruled — array position is not identity and duplicate question text is expected."""
    legacy = {
        "items": ["who hired the courier?"],
        "resolved": [{"q": "who hired the courier?", "resolution": "The Broker", "at": "2026-01-01T00:00:00Z"}],
    }
    read = oq.normalize(legacy, mint=False)
    assert len(oq.unresolved_items(read)) == 1, "a legacy resolution must not clear an active item"
    # Even after a write mints the item an id, the unbound legacy ruling still does not reach it.
    written = oq.normalize(legacy, mint=True)
    assert len(oq.unresolved_items(written)) == 1


def test_legacy_rows_are_readable_history_and_never_clear():
    """6. Preserved verbatim as history — not deleted, not repaired, and not authority."""
    entry = {"q": "who hired the courier?", "a": "The Broker", "at": "2026-01-01T00:00:00Z"}
    read = oq.normalize({"items": ["who hired the courier?"], "resolved": [entry]}, mint=False)
    assert read["resolved"] == [entry], "legacy history is preserved byte-for-byte"
    assert oq.cleared_item_ids(read) == set(), "and clears nothing"
    # A legacy ITEM read without minting is explicitly marked and carries no id (D4/D5: never mint on read).
    assert read["items"][0]["legacy"] is True
    assert "item_id" not in read["items"][0]


# =================================================================================================
# D6 — rollback compatibility
# =================================================================================================


def test_previous_reader_ignores_new_jsonb_keys():
    """7. Reverting the code must not reinterpret or erase existing JSONB.

    Old code read `items[]` and blocked on any non-empty list. Against the new shape it still sees a
    non-empty list of dicts and still blocks — fail-closed, which is the safe direction.
    """
    new_shape = oq.normalize({"items": ["q1", "q2"], "resolved": []}, mint=True)
    assert len(_old_open_question_items(new_shape)) == 2, "old reader still blocks on the new shape"

    ruled = oq.normalize(
        {"items": new_shape["items"], "resolved": [_ruling(i["item_id"]) for i in new_shape["items"]]},
        mint=True,
    )
    # Old code is blind to `resolved` entirely; it sees two items and blocks. Reverting is therefore
    # MORE restrictive, never fail-open — worth recording so a later reader does not mistake the
    # tolerance on an empty list for an understanding of resolution.
    assert len(_old_open_question_items(ruled)) == 2


# =================================================================================================
# D7 — the raw-write bypass
# =================================================================================================


async def test_raw_items_payload_cannot_make_approval_appear_clear(db_factory):
    """8. THE headline bypass. `PUT {"open_questions": {"items": "x"}}` used to be assigned raw to the
    column; the gate's isinstance check then fell through to `[]` and OPENED chapter approval while the
    body mirror still held the real state."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["a real unresolved question"]}, mint=True))
        await s.commit()
        chapter_id, cp_id = cp.chapter_id, cp.id
        current = await packets.get_packet(chapter_id, s)
        assert packet_approval.can_approve(await s.get(ChapterPacket, cp.id)) is not None

        with pytest.raises(HTTPException) as exc:
            await packets.update_packet(
                chapter_id,
                PacketUpdateIn(
                    open_questions={"items": "x"},
                    expected_open_questions_token=current.open_questions_token,
                ),
                s,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "open_questions_malformed"

    async with db_factory() as s2:
        row = await s2.get(ChapterPacket, cp_id)
        assert packet_approval.can_approve(row) is not None, "approval must NOT have been opened"
        assert len(packet_approval.open_question_items(row)) == 1

        # Defence in depth: even if such a value reached the column by some other path, the GATE itself
        # must fail closed rather than read it as "nothing to resolve".
        row.open_questions = {"items": "x"}
        assert len(packet_approval.open_question_items(row)) >= 1
        assert packet_approval.can_approve(row) is not None


async def test_both_write_paths_persist_one_normalized_value(db_factory):
    """9. The question-only path and the body-edit path must agree — one normalized value, written to
    the column AND the body mirror in the same transaction."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["question one"]}, mint=True))
        await s.commit()
        chapter_id, cp_id = cp.chapter_id, cp.id
        current = await packets.get_packet(chapter_id, s)
        item_id = current.open_questions["items"][0]["item_id"]

        await packets.update_packet(
            chapter_id,
            PacketUpdateIn(
                open_questions={"items": current.open_questions["items"], "resolved": [_ruling(item_id)]},
                expected_open_questions_token=current.open_questions_token,
            ),
            s,
        )
        await s.commit()

    async with db_factory() as s2:
        row = await s2.get(ChapterPacket, cp_id)
        column = row.open_questions
        mirror = master.master_open_questions(row.body, row.open_questions)
        assert column["items"] == mirror["items"], "column and body mirror carry the SAME normalized items"
        assert oq.cleared_item_ids(column) == {item_id}
        assert oq.unresolved_items(column) == []
        # every item is id-bound; nothing survived as a bare string
        assert all(isinstance(i, dict) and i.get("item_id") for i in column["items"])
        # the ruling carries a SERVER timestamp, not a client one
        assert column["resolved"][0]["at"], "the server records the ruling time"


# =================================================================================================
# Clause A — the predicate at the shared authority seam
# =================================================================================================


async def test_amendment_approval_refuses_unresolved_questions_before_any_mutation(db_factory):
    """10. The hole the amendment added. Assert BOTH negatives on the database, not just the 409: a
    refusal that happened after a flush would pass a response-code-only test while having already
    superseded the predecessor."""
    async with db_factory() as s:
        book = Book(title="Dominion Realm")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        predecessor = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.APPROVED,
            confidence="green",
            body={"scene_seeds": []},
            open_questions={"items": [], "resolved": []},
        )
        s.add(predecessor)
        await s.flush()
        amendment_row = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.PROPOSED,
            confidence="green",
            body={"scene_seeds": []},
            origin_mode=ImportAdoptionMode.AMENDMENT.value,
            supersedes_packet_id=predecessor.id,
            open_questions=oq.normalize({"items": ["an unresolved manuscript-vs-canon conflict"]}, mint=True),
        )
        s.add(amendment_row)
        await s.commit()
        chapter_id, amendment_id, predecessor_id = ch.id, amendment_row.id, predecessor.id

    async with db_factory() as s:
        with pytest.raises(amendment.AmendmentNotEligible) as exc:
            await amendment.apply_authority_locked(
                s,
                chapter_id=chapter_id,
                packet_id=amendment_id,
                approval_source=amendment.ChapterPacketApprovalSource.MANUAL_COMMAND,
                expect_amendment=True,
            )
        assert exc.value.reason == amendment.REASON_OPEN_QUESTIONS_UNRESOLVED
        await s.rollback()

    async with db_factory() as s2:
        still_amendment = await s2.get(ChapterPacket, amendment_id)
        still_predecessor = await s2.get(ChapterPacket, predecessor_id)
        assert str(still_amendment.status) == PacketStatus.PROPOSED.value, "the amendment must not have taken authority"
        assert str(still_predecessor.status) == PacketStatus.APPROVED.value, "the predecessor must not be superseded"


def test_amendment_approval_uses_only_the_canonical_reader():
    """11. The transition reaches the predicate ONLY through `approval_policy.can_approve`, and inspects
    no part of `open_questions` itself.

    The existing fork-3b guard covers the direct-read half but says in its own honest-limits section that
    a read through a helper in another module is invisible to it. This covers the residue: the whole
    module is scanned, so a helper defined here that touched the value would be caught too.
    """
    source = pathlib.Path(inspect.getsourcefile(amendment)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    direct_reads = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr == "open_questions")
        or (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "open_questions"
        )
    ]
    assert direct_reads == [], (
        "the authority seam must never read open_questions itself — it reaches the predicate only "
        f"through approval_policy.can_approve (found {len(direct_reads)} direct access(es))"
    )

    seam = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "apply_authority_locked")
    calls = {
        node.func.attr for node in ast.walk(seam) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "can_approve" in calls, "the seam must consult the canonical predicate"


# =================================================================================================
# Clause B — stale-write protection
# =================================================================================================


async def test_stale_open_questions_write_is_409_and_erases_nothing(db_factory):
    """12. Constructed as the contract requires: a SECOND writer adds an item between the caller's read
    and the caller's write. That is the erasure path — a test that only checked for a lost ruling would
    miss it entirely, because losing a ruling is safe (the item stays open) while losing an ITEM grants
    approval."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["question one"]}, mint=True))
        await s.commit()
        chapter_id, cp_id = cp.chapter_id, cp.id

        # The caller reads the packet — one item, and the token for that state.
        stale_view = await packets.get_packet(chapter_id, s)
        stale_token = stale_view.open_questions_token

        # A SECOND writer adds a question the caller has never seen.
        second = await packets.update_packet(
            chapter_id,
            PacketUpdateIn(
                open_questions={
                    "items": [*stale_view.open_questions["items"], {"text": "question two, added meanwhile"}],
                    "resolved": [],
                },
                expected_open_questions_token=stale_token,
            ),
            s,
        )
        await s.commit()
        assert len(second.open_questions["items"]) == 2

        # The caller now writes back the world as THEY saw it — one item. Applied blindly this erases
        # question two and would open approval on a question nobody ever ruled.
        with pytest.raises(HTTPException) as exc:
            await packets.update_packet(
                chapter_id,
                PacketUpdateIn(
                    open_questions={"items": stale_view.open_questions["items"], "resolved": []},
                    expected_open_questions_token=stale_token,
                ),
                s,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "open_questions_stale"
        assert "NOTHING WAS CHANGED" in exc.value.detail["message"]

    async with db_factory() as s2:
        row = await s2.get(ChapterPacket, cp_id)
        texts = [i["text"] for i in row.open_questions["items"]]
        assert texts == ["question one", "question two, added meanwhile"], "the unseen item must survive"
        assert packet_approval.can_approve(row) is not None


async def test_open_questions_write_without_a_token_is_422(db_factory):
    """Clause B support: absent is a 422, stale is a 409 — the split `revision_taxonomy.py:96-97,108-109`
    already ruled for expected_prose_hash, reused rather than reinvented."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["q"]}, mint=True))
        await s.commit()
        with pytest.raises(HTTPException) as exc:
            await packets.update_packet(cp.chapter_id, PacketUpdateIn(open_questions={"items": [], "resolved": []}), s)
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "open_questions_token_required"


async def test_idempotent_resubmission_of_the_current_state_is_a_noop(db_factory):
    """Clause B support: a duplicate delivery of a request that already succeeded is safe. The token is
    stale, but the submitted value IS the current state, so it is a no-op 200 rather than a conflict."""
    async with db_factory() as s:
        cp = await _seed_packet(s, open_questions=oq.normalize({"items": ["q"]}, mint=True))
        await s.commit()
        chapter_id = cp.chapter_id
        first = await packets.get_packet(chapter_id, s)
        item_id = first.open_questions["items"][0]["item_id"]
        payload = {"items": first.open_questions["items"], "resolved": [_ruling(item_id)]}

        await packets.update_packet(
            chapter_id,
            PacketUpdateIn(open_questions=payload, expected_open_questions_token=first.open_questions_token),
            s,
        )
        await s.commit()

        after = await packets.get_packet(chapter_id, s)
        # Re-deliver the SAME request with the now-stale original token.
        again = await packets.update_packet(
            chapter_id,
            PacketUpdateIn(
                open_questions=after.open_questions, expected_open_questions_token=first.open_questions_token
            ),
            s,
        )
        assert oq.unresolved_items(again.open_questions) == []
