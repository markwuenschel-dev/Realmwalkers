"""redraft_scene: one-click re-draft of a single deleted/undrafted scene.

Deleting a scene keeps its beat but marks the slot's ScenePacket STALE ("scene deleted"), and
contract-first drafting is fail-closed on an approved, non-stale packet — so the beat is undrafted
yet unqueueable. The endpoint re-approves that STALE packet and queues a draft for JUST that scene.

The route now runs inside `run_under_chapter_workflow` (#278 task C), which owns the transaction
boundary and ROLLS BACK on any exception — including the 409 refusals below. The two refusal tests
therefore COMMIT their seed before calling the route: a rollback would otherwise discard rows that
were only flushed, and "the packet is untouched" would be asserted against a row that no longer
exists. Committing first makes that assertion stronger, not weaker — it now checks durable state.
Production is unaffected either way: `deps.db_session` already rolls back on an HTTPException, and a
refusal writes nothing in both the old and new shapes.
"""

from __future__ import annotations

import pytest
from conftest import seed_scene_packet
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.api.scene_delete import hard_delete_scene
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, ScenePacketStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Scene, ScenePacket


async def _packet_status(s, packet_id):
    """Read the packet status as a COLUMN, not via a held ORM instance. After the route's locked body
    rolls back, every instance the test holds is expired, and a plain attribute read on an expired
    async instance raises `MissingGreenlet` (the hazard `packet/__init__.py:_persist` documents). A
    scalar select does its IO inside the await, so it reads durable state with no lazy load."""
    return (await s.execute(select(ScenePacket.status).where(ScenePacket.id == packet_id))).scalar_one()


async def _book_chapter(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return ch


async def test_redraft_scene_reapproves_stale_packet_and_queues_draft(db_factory):
    """The happy path: a scene deleted by accident leaves an APPROVED beat + a STALE packet. Re-draft
    flips the packet back to APPROVED (clearing stale_reason) and queues a draft for that scene only."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        scene = Scene(chapter_id=ch.id, scene_no=1, prose="drafted prose", version=1, status=SceneStatus.APPROVED)
        s.add(scene)
        await s.flush()

        # Delete the scene the way the Inbox/clear-draft path does: prose gone, packet marked STALE.
        await hard_delete_scene(s, scene.id)
        stale = await s.get(ScenePacket, sp.id)
        assert stale is not None
        assert stale.status == ScenePacketStatus.STALE
        assert stale.stale_reason == "scene deleted"

        out = await chapters.redraft_scene(ch.id, 1, s, BackgroundTasks())
        assert out.queued == 1

        # The packet is re-approved and no longer stale.
        refreshed = await s.get(ScenePacket, sp.id)
        assert refreshed.status == ScenePacketStatus.APPROVED
        assert refreshed.stale_reason is None

        # Exactly one draft job, for scene 1, carrying the packet contract.
        jobs = (
            (await s.execute(select(Job).where(Job.kind == JobKind.DRAFT, Job.status == JobStatus.QUEUED)))
            .scalars()
            .all()
        )
        assert [j.scene_no for j in jobs] == [1]
        assert jobs[0].scene_packet_id == sp.id


async def test_redraft_scene_scoped_to_one_scene_no(db_factory):
    """Only the named scene is queued — a sibling undrafted, stale scene is left untouched."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        b2 = Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2")
        s.add_all([b1, b2])
        await s.flush()
        sp1 = await seed_scene_packet(s, chapter=ch, beat=b1)
        sp2 = await seed_scene_packet(s, chapter=ch, beat=b2)
        # Both slots are stale (as if both scenes were deleted); we re-draft only scene 1.
        for sp in (sp1, sp2):
            sp.status = ScenePacketStatus.STALE
            sp.stale_reason = "scene deleted"
        await s.flush()

        out = await chapters.redraft_scene(ch.id, 1, s, BackgroundTasks())
        assert out.queued == 1

        assert (await s.get(ScenePacket, sp1.id)).status == ScenePacketStatus.APPROVED
        # Sibling packet stays stale — not force-approved by a scoped re-draft.
        assert (await s.get(ScenePacket, sp2.id)).status == ScenePacketStatus.STALE

        jobs = (await s.execute(select(Job).where(Job.kind == JobKind.DRAFT))).scalars().all()
        assert [j.scene_no for j in jobs] == [1]


async def test_redraft_scene_refuses_when_scene_already_has_prose(db_factory):
    """Guard: the action is for a MISSING scene. If the slot already has drafted prose, refuse with a
    clear 409 pointing at the supersede-in-place redraft path instead of silently re-queuing."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.STALE
        sp.stale_reason = "upstream inputs changed since derivation"
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="still here", version=1, status=SceneStatus.APPROVED))
        await s.commit()  # survive the locked body's rollback-on-refusal (see module docstring)
        packet_id = sp.id

        with pytest.raises(HTTPException) as exc:
            await chapters.redraft_scene(ch.id, 1, s, BackgroundTasks())
        assert exc.value.status_code == 409
        assert "already has prose" in str(exc.value.detail)
        # Nothing queued, packet untouched.
        assert (await s.execute(select(Job))).scalars().all() == []
        assert await _packet_status(s, packet_id) == ScenePacketStatus.STALE


async def test_redraft_scene_refuses_blocked_packet(db_factory):
    """Guard: never force-approve a BLOCKED packet — return the same refusal can_approve() gives."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(beat)
        await s.flush()
        sp = await seed_scene_packet(s, chapter=ch, beat=beat)
        sp.status = ScenePacketStatus.BLOCKED
        sp.stale_reason = None
        await s.commit()  # survive the locked body's rollback-on-refusal (see module docstring)
        packet_id = sp.id

        with pytest.raises(HTTPException) as exc:
            await chapters.redraft_scene(ch.id, 1, s, BackgroundTasks())
        assert exc.value.status_code == 409
        assert "blocked" in str(exc.value.detail).lower()
        assert (await s.execute(select(Job))).scalars().all() == []
        # Still blocked — not flipped to approved.
        assert await _packet_status(s, packet_id) == ScenePacketStatus.BLOCKED


async def test_redraft_scene_404_when_chapter_missing(db_factory):
    import uuid

    async with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await chapters.redraft_scene(uuid.uuid4(), 1, s, BackgroundTasks())
        assert exc.value.status_code == 404


async def test_redraft_scene_409_when_no_packet(db_factory):
    """No scene packet at all for the slot → a clear 409, not a crash."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        s.add(Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1"))
        await s.flush()

        with pytest.raises(HTTPException) as exc:
            await chapters.redraft_scene(ch.id, 1, s, BackgroundTasks())
        assert exc.value.status_code == 409
        assert "no scene packet" in str(exc.value.detail)
