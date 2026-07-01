"""create_chapter: contract-first chapter creation with no LLM beat-proposal call."""

from __future__ import annotations

from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.shared.enums import ChapterStatus
from dominion.shared.models import Book, Chapter
from dominion.shared.schemas import ChapterCreateIn
from dominion.workers import planner as planner_mod


async def _book(s) -> Book:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    return book


async def test_create_chapter_upserts_by_book_and_chapter_no(db_factory, monkeypatch):
    async def no_title(**kw):
        return None

    monkeypatch.setattr(planner_mod, "propose_chapter_title", no_title)
    async with db_factory() as s:
        book = await _book(s)
        body = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="Marcus enters the scrim.")
        out = await chapters.create_chapter(body, s)
        assert out.status == ChapterStatus.PLANNED
        assert out.pov == "Marcus"
        assert out.outline == "Marcus enters the scrim."

        rows = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalars().all()
        assert len(rows) == 1  # upsert, not a duplicate

        # Re-creating the same (book_id, chapter_no) updates in place.
        body2 = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Serra", outline="Serra's side of it.")
        out2 = await chapters.create_chapter(body2, s)
        assert out2.id == out.id
        assert out2.pov == "Serra"
        rows2 = (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalars().all()
        assert len(rows2) == 1


async def test_create_chapter_never_calls_propose_beats(db_factory, monkeypatch):
    called = False

    async def fail_if_called(**kw):
        nonlocal called
        called = True
        return []

    async def no_title(**kw):
        return None

    monkeypatch.setattr(planner_mod, "propose_beats", fail_if_called)
    monkeypatch.setattr(planner_mod, "propose_chapter_title", no_title)
    async with db_factory() as s:
        book = await _book(s)
        body = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="An outline.")
        await chapters.create_chapter(body, s)
        assert called is False


async def test_create_chapter_stamps_generated_title_when_none_exists(db_factory, monkeypatch):
    async def fake_title(**kw):
        return "The Scrim Begins"

    monkeypatch.setattr(planner_mod, "propose_chapter_title", fake_title)
    async with db_factory() as s:
        book = await _book(s)
        body = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="Marcus enters the scrim.")
        out = await chapters.create_chapter(body, s)
        assert out.title == "The Scrim Begins"


async def test_create_chapter_never_overwrites_existing_title(db_factory, monkeypatch):
    called = False

    async def fake_title(**kw):
        nonlocal called
        called = True
        return "A New Generated Title"

    monkeypatch.setattr(planner_mod, "propose_chapter_title", fake_title)
    async with db_factory() as s:
        book = await _book(s)
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", title="Hand-picked Title")
        s.add(ch)
        await s.flush()

        body = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="An updated outline.")
        out = await chapters.create_chapter(body, s)
        assert out.title == "Hand-picked Title"
        assert called is False  # never even calls the planner when a title already exists


async def test_create_chapter_survives_title_call_returning_none(db_factory, monkeypatch):
    async def no_title(**kw):
        return None

    monkeypatch.setattr(planner_mod, "propose_chapter_title", no_title)
    async with db_factory() as s:
        book = await _book(s)
        body = ChapterCreateIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="An outline.")
        out = await chapters.create_chapter(body, s)
        assert out.title is None
