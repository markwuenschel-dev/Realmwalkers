"""A chapter's display number follows its kind.

A numberless kind (prologue/interlude/epilogue/front-/back-matter) carries no chapter_no, so marking a
chapter as one clears the number, and nothing that looks a chapter up by number can land on the section.
Router functions called directly with a session (see tests/conftest.py)."""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from dominion.api.routers import books, chapters
from dominion.shared.chapter_order import chapter_position
from dominion.shared.enums import ChapterKind
from dominion.shared.models import Book, Chapter
from dominion.shared.schemas import ChapterCreateIn, ChapterUpdateIn, HumanSceneIn
from dominion.workers import planner as planner_mod
from dominion.workers.memory import seed


async def _book(s) -> Book:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    return book


async def _chapter(s, book: Book, chapter_no: int | None, kind: str = "chapter", **kw) -> Chapter:
    ch = Chapter(
        book_id=book.id,
        chapter_no=chapter_no,
        kind=kind,
        pov="",
        position=chapter_position(kind, chapter_no, seq=chapter_no or 0),
        **kw,
    )
    s.add(ch)
    await s.flush()
    return ch


async def test_marking_a_numbered_chapter_as_prologue_clears_its_number_and_moves_it_first(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        one = await _chapter(s, book, 1)
        pro = await _chapter(s, book, 10, title="Prologue")

        out = await chapters.update_chapter(pro.id, ChapterUpdateIn(kind=ChapterKind.PROLOGUE), s)

        assert out.kind == "prologue"
        assert out.chapter_no is None
        assert out.title == "Prologue"
        assert out.position is not None and one.position is not None
        assert out.position < one.position


async def test_re_marking_a_prologue_that_kept_a_number_clears_it(db_factory):
    # A row can already be numberless in kind but still carry a number from before this rule.
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, 10, kind="prologue")

        out = await chapters.update_chapter(pro.id, ChapterUpdateIn(kind=ChapterKind.PROLOGUE), s)

        assert out.chapter_no is None


async def test_turning_a_numberless_section_into_a_chapter_requires_a_number(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, None, kind="prologue")

        with pytest.raises(HTTPException) as exc:
            await chapters.update_chapter(pro.id, ChapterUpdateIn(kind=ChapterKind.CHAPTER), s)
        assert exc.value.status_code == 422

        out = await chapters.update_chapter(pro.id, ChapterUpdateIn(kind=ChapterKind.CHAPTER, chapter_no=4), s)
        assert out.kind == "chapter"
        assert out.chapter_no == 4
        assert out.position == chapter_position("chapter", 4)


async def test_a_number_another_chapter_already_has_is_refused(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await _chapter(s, book, 4)
        pro = await _chapter(s, book, None, kind="prologue")

        with pytest.raises(HTTPException) as exc:
            await chapters.update_chapter(pro.id, ChapterUpdateIn(kind=ChapterKind.CHAPTER, chapter_no=4), s)
        assert exc.value.status_code == 409


async def test_a_numberless_section_cannot_be_given_a_number(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, None, kind="prologue")

        with pytest.raises(HTTPException) as exc:
            await chapters.update_chapter(pro.id, ChapterUpdateIn(chapter_no=3), s)
        assert exc.value.status_code == 422


async def test_editing_only_the_title_leaves_the_number_alone(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        numbered = await _chapter(s, book, 2)

        out = await chapters.update_chapter(numbered.id, ChapterUpdateIn(title="Renamed"), s)

        assert out.title == "Renamed"
        assert out.chapter_no == 2
        assert out.position == chapter_position("chapter", 2)


async def test_planning_chapter_n_never_lands_on_a_section_that_kept_number_n(db_factory, monkeypatch):
    async def no_title(**kw):
        return None

    monkeypatch.setattr(planner_mod, "propose_chapter_title", no_title)
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, 10, kind="prologue", outline="The prologue's own outline.")

        out = await chapters.create_chapter(
            ChapterCreateIn(book_id=book.id, chapter_no=10, pov="Marcus", outline="Chapter ten."), s
        )

        assert out.id != pro.id
        assert out.kind == "chapter"
        await s.refresh(pro)
        assert pro.outline == "The prologue's own outline."
        assert pro.pov == ""


async def test_seeding_chapter_n_never_lands_on_a_section_that_kept_number_n(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, 10, kind="prologue")

        ch = await seed._get_or_create_chapter(s, book_id=book.id, chapter_no=10, pov="Marcus")

        assert ch.id != pro.id
        assert ch.kind == "chapter"


async def test_manuscript_chapters_carry_their_id(db_factory):
    # The per-chapter export matches on this id, since a numberless section has no number to match on.
    async with db_factory() as s:
        book = await _book(s)
        pro = await _chapter(s, book, None, kind="prologue")
        await chapters.create_human_scene(
            pro.id, HumanSceneIn(scene_no=1, prose="Before the first chapter."), s, BackgroundTasks()
        )

        out = await books.manuscript(book.id, s)

        assert [c.id for c in out.chapters] == [pro.id]
        assert out.chapters[0].chapter_no is None
