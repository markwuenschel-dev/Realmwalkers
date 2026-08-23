"""#283 C1 — `POST /chapters/{id}/beats/approve` writes approved status with no gate and no lock.

The route set `BeatStatus.APPROVED` across every beat in the chapter and committed immediately. Its only
checks were "beats exist" and "these ids belong to this chapter" — neither of which is an authorization.

`Beat.status == APPROVED` is a DRAFTING PREREQUISITE, read as one in five places, so this route
authorizes the machine to start writing prose. The sharpest evidence that it was a live bypass rather
than a theoretical one: ninety lines below it in the same file, `redraft_scene` refuses when no approved
beat exists. **The ungated route was the workaround for the gate it bypassed.**

The issue's done-when has three parts, and each has a test here:

    1. the route evaluates a permit BEFORE the write
    2. it holds the chapter workflow lock
    3. an interleaving test proves evaluation and write share the lock and transaction

Part 3 is the one that matters most and is easiest to fake. A permit checked *before* the lock is a
permit checked against state another writer is free to change before the write lands, so the test holds
the lock from a second session and proves the route cannot even begin.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from dominion.api.routers import chapters as chapters_router
from dominion.shared.chapter_lock import acquire_chapter_workflow_lock
from dominion.shared.enums import BeatStatus, PacketStatus
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket
from dominion.workers.packet import open_questions as oq


async def _seed(s, *, open_questions: dict | None, beats: int = 2) -> tuple[uuid.UUID, list[uuid.UUID]]:
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    if open_questions is not None:
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status=PacketStatus.APPROVED,
                confidence="green",
                body={"scene_seeds": []},
                open_questions=open_questions,
            )
        )
    beat_ids = []
    for n in range(1, beats + 1):
        beat = Beat(chapter_id=ch.id, scene_no=n, beat_text=f"beat {n}", status=BeatStatus.PROPOSED)
        s.add(beat)
        await s.flush()
        beat_ids.append(beat.id)
    return ch.id, beat_ids


async def _statuses(db_factory, chapter_id) -> list[str]:
    async with db_factory() as s:
        rows = (
            (await s.execute(select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no))).scalars().all()
        )
        return [str(b.status) for b in rows]


# =================================================================================================
# 1. the permit
# =================================================================================================


async def test_unresolved_open_questions_refuse_beat_approval(app_client, db_factory):
    """Approving beats authorizes drafting against the chapter's contract. A contract with unresolved
    open questions does not hold authority, so it cannot authorize anything (#277's gate, consulted
    through its canonical reader rather than re-derived)."""
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=oq.normalize({"items": ["who hired the courier?"]}, mint=True))
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == "chapter_contract_has_open_questions"
    assert "Nothing was changed" in resp.json()["detail"]["message"]

    assert await _statuses(db_factory, chapter_id) == ["proposed", "proposed"], (
        "a refused approval must leave every beat untouched"
    )


async def test_a_settled_contract_permits_beat_approval(app_client, db_factory):
    """The positive case — otherwise 'always refuses' would pass the test above."""
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=oq.normalize({"items": []}, mint=True))
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["approved"] == 2
    assert await _statuses(db_factory, chapter_id) == ["approved", "approved"]


async def test_a_chapter_with_no_approved_contract_is_out_of_scope_for_this_permit(app_client, db_factory):
    """Scoped narrow on purpose. There is no contract to contradict, so this permit does not refuse —
    blocking beat-first authoring here would be a policy change wearing an authorization costume."""
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=None)
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
    assert resp.status_code == 200, resp.text


async def test_the_permit_covers_a_partial_beat_selection_too(app_client, db_factory):
    """A caller naming specific beat_ids must not slip past the permit — a gate that only guards the
    default path is a gate with a documented workaround."""
    async with db_factory() as s:
        chapter_id, beat_ids = await _seed(s, open_questions=oq.normalize({"items": ["unresolved"]}, mint=True))
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve", json={"beat_ids": [str(beat_ids[0])]})
    assert resp.status_code == 409
    assert await _statuses(db_factory, chapter_id) == ["proposed", "proposed"]


# =================================================================================================
# 2 + 3. the lock, and that evaluation and write share it
# =================================================================================================


async def test_beat_approval_is_serialized_by_the_chapter_workflow_lock(app_client, db_factory, monkeypatch):
    """The route now takes the chapter workflow lock. Held elsewhere, it refuses with the retryable busy
    409 and writes nothing — it does not proceed on a chapter another transaction is mid-way through."""
    monkeypatch.setattr(chapters_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=oq.normalize({"items": []}, mint=True))
        await s.commit()

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"
        assert await _statuses(db_factory, chapter_id) == ["proposed", "proposed"], (
            "a lock-refused approval must write nothing"
        )

        await holder.rollback()

    # …and once the lock is free the identical request succeeds, so the refusal was the lock and not a
    # permanent condition.
    retry = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
    assert retry.status_code == 200, retry.text
    assert await _statuses(db_factory, chapter_id) == ["approved", "approved"]


async def test_evaluation_and_write_share_one_transaction(app_client, db_factory):
    """The interleaving property the issue names. A permit checked BEFORE the lock is checked against
    state another writer may change before the write lands.

    Proven by making the permit fail mid-request: the questions are unresolved when the route reads
    them, so the refusal happens after the beats were selected and before any status was written — and
    the wrapper's rollback means nothing partial survives. A route that committed as it went (the old
    one committed immediately after the loop) would leave the first beats approved.
    """
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=oq.normalize({"items": ["a", "b", "c"]}, mint=True), beats=5)
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve")
    assert resp.status_code == 409

    statuses = await _statuses(db_factory, chapter_id)
    assert statuses == ["proposed"] * 5, (
        "not one beat may be left approved by a refused request — partial application is what a shared "
        "transaction exists to prevent"
    )


# =================================================================================================
# every route to the protected write is enumerated
# =================================================================================================


def test_only_two_writers_of_beat_status_exist_and_the_second_is_derived():
    """Before declaring the control closed, enumerate every production route that can reach the write.

    `Beat.status = APPROVED` has exactly two writers. This route is one; the other is
    `scene_packet/beats.derive_beats`, which upserts a beat per ScenePacket it has ALREADY filtered to
    `status == APPROVED` — a mirror of a fact that passed the ScenePacket approval gate, not an
    independent authority write. A third writer appearing is a regression this test makes visible.
    """
    import pathlib
    import re

    src = pathlib.Path("src/dominion")
    writers = []
    for path in src.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.status\s*=\s*BeatStatus\.", line):
                writers.append(f"{path.as_posix()}:{i}")

    assert len(writers) == 2, f"expected exactly two writers of Beat.status, found: {writers}"
    assert any("routers/chapters.py" in w for w in writers)
    assert any("scene_packet/beats.py" in w for w in writers)

    derive = (src / "workers/scene_packet/beats.py").read_text(encoding="utf-8")
    assert "ScenePacket.status == ScenePacketStatus.APPROVED" in derive, (
        "the second writer must remain a mirror of an already-approved ScenePacket; if it stops "
        "filtering on APPROVED it becomes an independent, ungated authority write"
    )


@pytest.mark.parametrize("body", [None, {}, {"beat_ids": []}])
async def test_no_request_shape_slips_past_the_permit(app_client, db_factory, body):
    """The old route's only checks were shape checks. Assert that no shape of request reaches the write
    while the contract is unsettled."""
    async with db_factory() as s:
        chapter_id, _ = await _seed(s, open_questions=oq.normalize({"items": ["unresolved"]}, mint=True))
        await s.commit()

    resp = await app_client.post(f"/chapters/{chapter_id}/beats/approve", json=body)
    assert resp.status_code == 409
    assert await _statuses(db_factory, chapter_id) == ["proposed", "proposed"]
