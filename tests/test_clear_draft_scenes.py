"""Clear non-approved draft scenes for a book or chapter."""

from __future__ import annotations

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.api.routers import books as books_router
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, RunStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Run, Scene


async def _seed_chapter(s, book, chapter_no: int, pov: str):
    ch = Chapter(book_id=book.id, chapter_no=chapter_no, pov=pov)
    s.add(ch)
    await s.flush()
    run = Run(
        book_id=book.id,
        scope_json={},
        gate_mode="pause_each",
        token_budget=40_000,
        status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    sp = await seed_scene_packet(s, chapter=ch, beat=beat)
    return ch, run, beat, sp


async def test_clear_draft_scenes_keeps_approved(db_factory):
    async with db_factory() as s:
        book = Book(title="Clear")
        s.add(book)
        await s.flush()
        ch, run, beat, sp = await _seed_chapter(s, book, 1, "A")
        draft = Scene(
            chapter_id=ch.id,
            scene_no=1,
            prose="draft",
            version=1,
            status=SceneStatus.DRAFT,
        )
        approved = Scene(
            chapter_id=ch.id,
            scene_no=2,
            prose="kept",
            version=1,
            status=SceneStatus.APPROVED,
        )
        s.add_all([draft, approved])
        await s.flush()
        s.add(
            Job(
                run_id=run.id,
                book_id=book.id,
                kind=JobKind.DRAFT,
                chapter_id=ch.id,
                beat_id=beat.id,
                scene_packet_id=sp.id,
                chapter_no=1,
                scene_no=1,
                status=JobStatus.FAILED,
                token_budget=40_000,
                last_error="x",
            )
        )
        await s.commit()

        out = await books_router.clear_draft_scenes(book.id, s)
        assert out.purged == 1
        assert out.jobs_purged == 1

        remaining = (await s.execute(select(Scene).where(Scene.chapter_id == ch.id))).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == approved.id
        assert remaining[0].status == SceneStatus.APPROVED


async def test_clear_draft_scenes_chapter_scope(db_factory):
    async with db_factory() as s:
        book = Book(title="Scope")
        s.add(book)
        await s.flush()
        ch1, _, _, _ = await _seed_chapter(s, book, 1, "A")
        ch2, _, _, _ = await _seed_chapter(s, book, 2, "B")
        s.add(
            Scene(
                chapter_id=ch1.id,
                scene_no=1,
                prose="c1",
                version=1,
                status=SceneStatus.PENDING_REVIEW,
            )
        )
        s.add(
            Scene(
                chapter_id=ch2.id,
                scene_no=1,
                prose="c2",
                version=1,
                status=SceneStatus.DRAFT,
            )
        )
        await s.commit()

        out = await books_router.clear_draft_scenes(book.id, s, chapter_id=ch1.id)
        assert out.purged == 1

        scenes = (await s.execute(select(Scene))).scalars().all()
        assert len(scenes) == 1
        assert scenes[0].chapter_id == ch2.id
