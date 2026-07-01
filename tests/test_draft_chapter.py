"""draft_chapter: contract-first draft queueing after ScenePacket approval."""

from __future__ import annotations

import pytest
from conftest import seed_scene_packet
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.shared.enums import BeatStatus, JobStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Scene
from dominion.workers.draft_readiness import compute_draft_readiness


async def _book_chapter(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return ch


async def test_draft_chapter_queues_only_undrafted_approved_beats(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        b2 = Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2")
        b3 = Beat(chapter_id=ch.id, scene_no=3, status=BeatStatus.PROPOSED, beat_text="b3")
        s.add_all([b1, b2, b3])
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        await seed_scene_packet(s, chapter=ch, beat=b2)
        s.add(Scene(chapter_id=ch.id, scene_no=2, prose="done", version=1, status=SceneStatus.PENDING_REVIEW))
        await s.flush()

        out = await chapters.draft_chapter(ch.id, s)
        assert out.queued == 1
        jobs = (await s.execute(select(Job).where(Job.chapter_no == 1, Job.status == JobStatus.QUEUED))).scalars().all()
        assert [j.scene_no for j in jobs] == [1]
        assert all(j.scene_packet_id is not None for j in jobs)


async def test_draft_chapter_409_when_no_approved_scene_packets(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        s.add(Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1"))
        await s.flush()
        with pytest.raises(HTTPException) as exc:
            await chapters.draft_chapter(ch.id, s)
        assert exc.value.status_code == 409
        jobs = (await s.execute(select(Job))).scalars().all()
        assert jobs == []


async def test_draft_chapter_409_detail_is_json_renderable(db_factory):
    """Regression: the 409's blocker detail must actually survive turning into an HTTP response.

    DraftQueueBlockerOut.model_dump() (no mode="json") leaves chapter_id/beat_id/scene_packet_id as
    raw uuid.UUID objects. HTTPException.detail is NOT run through FastAPI's jsonable_encoder (that
    only applies to response_model-validated return values), so it goes straight into Starlette's
    JSONResponse, which calls stdlib json.dumps() and cannot serialize a UUID. Before the fix, this
    turned an intended 409 (no approved beats — approve ScenePackets first / blockers) into an
    unhandled 500 for any chapter with approved beats whose ScenePackets aren't approved yet — the
    everyday case for the current contract-first flow (books created via POST /chapters never get a
    Run row, and beats commonly sit APPROVED-but-unlinked between packet derive/approve steps)."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        s.add(Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1"))
        await s.flush()
        with pytest.raises(HTTPException) as exc:
            await chapters.draft_chapter(ch.id, s)
        assert exc.value.status_code == 409
        # Reproduces exactly what FastAPI's default HTTPException handler does to build the response
        # (fastapi.exception_handlers.http_exception_handler) — must not raise TypeError.
        response = JSONResponse(content=exc.value.detail, status_code=exc.value.status_code)
        assert response.status_code == 409


async def test_draft_chapter_already_drafted_409_is_json_renderable(db_factory):
    """Regression + backstop: a fully-drafted chapter must still return a *renderable* 409 if
    draft_chapter is called directly. compute_draft_readiness now excludes already-drafted scenes, so
    the Desk's "Draft chapter" button is disabled here rather than enabled-then-409 (see
    test_readiness_not_draftable_when_all_scenes_already_drafted). But draft_chapter stays a backstop:
    a stale UI or a direct API call still hits the already_drafted blocker for every beat, and that
    409 detail must serialize — raw uuid.UUID values would 500 via Starlette's json.dumps()."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(b1)
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="already drafted", version=1, status=SceneStatus.APPROVED))
        await s.flush()

        with pytest.raises(HTTPException) as exc:
            await chapters.draft_chapter(ch.id, s)
        assert exc.value.status_code == 409
        response = JSONResponse(content=exc.value.detail, status_code=exc.value.status_code)
        assert response.status_code == 409


async def test_readiness_not_draftable_when_all_scenes_already_drafted(db_factory):
    """A fully-drafted chapter reports draftable=False so the Desk's "Draft chapter" button is disabled
    instead of enabled-then-409. Mirrors schedule_undrafted_beats(skip_drafted=True): a beat whose scene
    already has prose is not queueable (redraft is the path for those) — and it is NOT a blocker, just
    nothing left to draft."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        s.add(b1)
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="already drafted", version=1, status=SceneStatus.APPROVED))
        await s.flush()

        readiness = await compute_draft_readiness(s, ch.id)
        assert readiness.draftable is False
        assert readiness.blockers == []  # already-drafted is not a blocker — there's simply nothing to queue


async def test_readiness_draftable_when_an_approved_beat_is_still_undrafted(db_factory):
    """Partial-draft chapters stay draftable: scene 1 is drafted but approved beat 2 has no scene yet,
    so draftable=True and clicking queues only the remaining beat."""
    async with db_factory() as s:
        ch = await _book_chapter(s)
        b1 = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b1")
        b2 = Beat(chapter_id=ch.id, scene_no=2, status=BeatStatus.APPROVED, beat_text="b2")
        s.add_all([b1, b2])
        await s.flush()
        await seed_scene_packet(s, chapter=ch, beat=b1)
        await seed_scene_packet(s, chapter=ch, beat=b2)
        s.add(Scene(chapter_id=ch.id, scene_no=1, prose="done", version=1, status=SceneStatus.APPROVED))
        await s.flush()

        readiness = await compute_draft_readiness(s, ch.id)
        assert readiness.draftable is True
