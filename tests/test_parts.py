"""Parts CRUD + chapter assignment + manuscript grouping (renderer-neutral export foundation).

Covers the durable Book → Part → Chapter grouping: create/list/update/delete with validation, chapter
(un)assignment with cross-book guarding, delete-unassigns-not-deletes, and that the manuscript endpoint
emits parts + per-chapter part_id + book metadata in the flat wire shape the frontend spine builder reads.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers import books as books_router
from dominion.api.routers import parts as parts_router
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, Part, Scene
from dominion.shared.schemas import (
    ChapterPartAssignIn,
    PartCreateIn,
    PartUpdateIn,
)


async def _book(s, **kw) -> Book:
    book = Book(title=kw.pop("title", "X"), **kw)
    s.add(book)
    await s.flush()
    return book


async def _chapter(s, book, chapter_no: int, *, part_id=None) -> Chapter:
    ch = Chapter(book_id=book.id, chapter_no=chapter_no, pov="Marcus", part_id=part_id)
    s.add(ch)
    await s.flush()
    return ch


async def _approved_scene(s, chapter, scene_no: int, prose: str) -> Scene:
    sc = Scene(chapter_id=chapter.id, scene_no=scene_no, status=SceneStatus.APPROVED, prose=prose)
    s.add(sc)
    await s.flush()
    return sc


async def test_create_and_list_parts_ordered_by_part_no(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await parts_router.create_part(book.id, PartCreateIn(part_no=2, title="Second"), s)
        await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="First", subtitle="Dawn"), s)

        listed = await parts_router.list_parts(book.id, s)
        assert [p.part_no for p in listed] == [1, 2]
        assert listed[0].title == "First"
        assert listed[0].subtitle == "Dawn"


async def test_create_part_rejects_duplicate_part_no(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="First"), s)
        with pytest.raises(HTTPException) as exc:
            await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="Clash"), s)
        assert exc.value.status_code == 409


async def test_create_part_unknown_book_404(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await parts_router.create_part(uuid.uuid4(), PartCreateIn(part_no=1, title="Orphan"), s)
        assert exc.value.status_code == 404


async def test_part_no_unique_per_book_not_globally(db_factory):
    """Two different books may each have a Part 1 — uniqueness is scoped to the book."""
    async with db_factory() as s:
        book_a = await _book(s, title="A")
        book_b = await _book(s, title="B")
        await parts_router.create_part(book_a.id, PartCreateIn(part_no=1, title="A1"), s)
        # Same part_no in a different book is fine.
        b1 = await parts_router.create_part(book_b.id, PartCreateIn(part_no=1, title="B1"), s)
        assert b1.part_no == 1


async def test_update_part_renumber_and_collision(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        p1 = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="First"), s)
        await parts_router.create_part(book.id, PartCreateIn(part_no=2, title="Second"), s)

        # Rename + subtitle edit, no number change.
        updated = await parts_router.update_part(p1.id, PartUpdateIn(title="Prologue Arc", subtitle="I"), s)
        assert updated.title == "Prologue Arc"
        assert updated.subtitle == "I"
        assert updated.part_no == 1

        # Renumbering onto an occupied number collides.
        with pytest.raises(HTTPException) as exc:
            await parts_router.update_part(p1.id, PartUpdateIn(part_no=2), s)
        assert exc.value.status_code == 409

        # Renumbering to a free number succeeds (and a no-op self-number does not self-collide).
        moved = await parts_router.update_part(p1.id, PartUpdateIn(part_no=3), s)
        assert moved.part_no == 3
        same = await parts_router.update_part(moved.id, PartUpdateIn(part_no=3), s)
        assert same.part_no == 3


async def test_assign_and_unassign_chapter(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="First"), s)
        ch = await _chapter(s, book, 1)

        assigned = await parts_router.assign_chapter_part(ch.id, ChapterPartAssignIn(part_id=part.id), s)
        assert assigned is not None
        assert assigned.id == part.id
        await s.refresh(ch)
        assert ch.part_id == part.id

        unassigned = await parts_router.assign_chapter_part(ch.id, ChapterPartAssignIn(part_id=None), s)
        assert unassigned is None
        await s.refresh(ch)
        assert ch.part_id is None


async def test_assign_chapter_to_part_in_other_book_rejected(db_factory):
    async with db_factory() as s:
        book_a = await _book(s, title="A")
        book_b = await _book(s, title="B")
        part_b = await parts_router.create_part(book_b.id, PartCreateIn(part_no=1, title="B1"), s)
        ch_a = await _chapter(s, book_a, 1)
        with pytest.raises(HTTPException) as exc:
            await parts_router.assign_chapter_part(ch_a.id, ChapterPartAssignIn(part_id=part_b.id), s)
        assert exc.value.status_code == 400


async def test_delete_part_unassigns_chapters_not_deletes_them(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="First"), s)
        ch = await _chapter(s, book, 1, part_id=part.id)

        await parts_router.delete_part(part.id, s)

        # Part gone, chapter kept, membership cleared.
        assert (await s.execute(select(Part).where(Part.id == part.id))).first() is None
        await s.refresh(ch)
        assert ch.part_id is None


async def test_manuscript_emits_parts_membership_and_metadata(db_factory):
    async with db_factory() as s:
        book = await _book(s, title="Realmwalkers", series="Dominion Realm", book_no=1, subtitle="Ascent")
        part1 = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="The Scrim"), s)
        part2 = await parts_router.create_part(book.id, PartCreateIn(part_no=2, title="The Reserve"), s)

        ch1 = await _chapter(s, book, 1, part_id=part1.id)
        ch2 = await _chapter(s, book, 2, part_id=part2.id)
        ch3 = await _chapter(s, book, 3)  # ungrouped
        await _approved_scene(s, ch1, 1, "One.")
        await _approved_scene(s, ch2, 1, "Two.")
        await _approved_scene(s, ch3, 1, "Three.")
        await s.commit()

        ms = await books_router.manuscript(book.id, s)

        assert ms.series == "Dominion Realm"
        assert ms.book_no == 1
        assert ms.subtitle == "Ascent"
        assert [p.part_no for p in ms.parts] == [1, 2]
        by_no = {c.chapter_no: c for c in ms.chapters}
        assert by_no[1].part_id == part1.id
        assert by_no[2].part_id == part2.id
        assert by_no[3].part_id is None  # ungrouped chapter carries a null part_id


async def test_manuscript_omits_parts_with_no_rendered_chapter(db_factory):
    """A Part whose only chapter has no approved prose must NOT emit a phantom divider."""
    async with db_factory() as s:
        book = await _book(s, title="B")
        part_rendered = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="Rendered"), s)
        part_empty = await parts_router.create_part(book.id, PartCreateIn(part_no=2, title="Empty"), s)

        ch1 = await _chapter(s, book, 1, part_id=part_rendered.id)
        await _approved_scene(s, ch1, 1, "Rendered prose.")
        # part_empty's chapter has only a DRAFT scene -> not rendered.
        ch2 = await _chapter(s, book, 2, part_id=part_empty.id)
        draft = Scene(chapter_id=ch2.id, scene_no=1, status=SceneStatus.DRAFT, prose="Draft only.")
        s.add(draft)
        await s.commit()

        ms = await books_router.manuscript(book.id, s)
        assert [p.title for p in ms.parts] == ["Rendered"]


async def test_manuscript_no_parts_is_backward_compatible(db_factory):
    """A book with no parts emits parts:[] and null part_id everywhere (existing exports unaffected)."""
    async with db_factory() as s:
        book = await _book(s, title="Legacy")
        ch = await _chapter(s, book, 1)
        await _approved_scene(s, ch, 1, "Prose.")
        await s.commit()

        ms = await books_router.manuscript(book.id, s)
        assert ms.parts == []
        assert ms.chapters[0].part_id is None
        assert ms.series is None  # a plain test book inherits no series identity
