"""Manuscript bulk-import endpoint: upserts chapters and lands scenes in review, refusing collisions
unless overwrite is set. Router function called directly with a session (see tests/conftest.py)."""

from __future__ import annotations

from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import manuscript
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, Scene
from dominion.shared.schemas import (
    ManuscriptImportChapterIn,
    ManuscriptImportIn,
    ManuscriptImportSceneIn,
)


async def _book(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    return book


def _chapter(chapter_no, scenes, **kw):
    return ManuscriptImportChapterIn(
        chapter_no=chapter_no,
        scenes=[ManuscriptImportSceneIn(scene_no=n, prose=p) for n, p in scenes],
        **kw,
    )


async def test_import_lands_pending_review_imported_and_creates_chapter(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        bg = BackgroundTasks()
        report = await manuscript.import_manuscript(
            book.id,
            ManuscriptImportIn(
                chapters=[_chapter(1, [(1, "Alpha prose."), (2, "Beta prose.")], pov="Marcus", title="Opening")]
            ),
            s,
            bg,
        )
        assert report.chapters_created == 1
        assert report.scenes_imported == 2
        assert len(bg.tasks) == 0  # review path defers the summary fold to inbox approval

        ch = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalar_one()
        assert ch.pov == "Marcus"
        assert ch.title == "Opening"
        scenes = (
            (await s.execute(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.scene_no))).scalars().all()
        )
        assert [sc.status for sc in scenes] == [SceneStatus.PENDING_REVIEW, SceneStatus.PENDING_REVIEW]
        assert all(sc.prose_source == "imported" for sc in scenes)


async def test_import_supersedes_prior_scene_when_overwriting(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await manuscript.import_manuscript(
            book.id, ManuscriptImportIn(chapters=[_chapter(1, [(1, "first")], pov="M")]), s, BackgroundTasks()
        )
        await manuscript.import_manuscript(
            book.id,
            ManuscriptImportIn(chapters=[_chapter(1, [(1, "second")], pov="M", overwrite=True)]),
            s,
            BackgroundTasks(),
        )
        ch = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalar_one()
        scenes = (
            (await s.execute(select(Scene).where(Scene.chapter_id == ch.id).order_by(Scene.version))).scalars().all()
        )
        assert len(scenes) == 2
        assert scenes[0].status == SceneStatus.SUPERSEDED and "first" in scenes[0].prose
        assert scenes[1].version == 2
        assert scenes[1].parent_scene_id == scenes[0].id
        assert scenes[1].status == SceneStatus.PENDING_REVIEW


async def test_import_refuses_conflict_without_overwrite(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add(Chapter(book_id=book.id, chapter_no=1, pov="Existing"))
        await s.flush()
        report = await manuscript.import_manuscript(
            book.id, ManuscriptImportIn(chapters=[_chapter(1, [(1, "new")], pov="New")]), s, BackgroundTasks()
        )
        assert report.skipped_conflicts == [1]
        assert report.scenes_imported == 0
        ch = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalar_one()
        assert ch.pov == "Existing"  # untouched — the conflicting chapter was left alone


async def test_import_accepts_empty_pov(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        report = await manuscript.import_manuscript(
            book.id, ManuscriptImportIn(chapters=[_chapter(1, [(1, "prose")])]), s, BackgroundTasks()
        )
        assert report.chapters_created == 1
        ch = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalar_one()
        assert ch.pov == ""


async def test_import_auto_title_schedules_batch_for_untitled_only(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        bg = BackgroundTasks()
        await manuscript.import_manuscript(
            book.id,
            ManuscriptImportIn(
                chapters=[
                    _chapter(1, [(1, "untitled chapter prose")], pov="M"),  # no title -> gets auto-title
                    _chapter(2, [(1, "titled chapter prose")], pov="M", title="Named"),  # titled -> skip
                ],
                auto_title=True,
            ),
            s,
            bg,
        )
        # exactly one auto-title task — only the untitled chapter; no fold tasks on the review path
        assert len(bg.tasks) == 1


async def test_import_approve_directly_lands_approved_and_schedules_fold(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        bg = BackgroundTasks()
        await manuscript.import_manuscript(
            book.id,
            ManuscriptImportIn(chapters=[_chapter(1, [(1, "prose")], pov="M")], approve_directly=True),
            s,
            bg,
        )
        ch = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalar_one()
        scene = (await s.execute(select(Scene).where(Scene.chapter_id == ch.id))).scalar_one()
        assert scene.status == SceneStatus.APPROVED
        assert len(bg.tasks) == 1  # directly-approved scene schedules a summary fold
