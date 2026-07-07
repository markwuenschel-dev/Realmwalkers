"""Volumes CRUD + part→volume assignment + manuscript nesting (structural export v2).

Covers the top grouping tier (Book → Volume → Part → Chapter): volume CRUD with validation, part
(un)assignment to a volume with cross-book guarding, delete-unassigns-not-deletes, and that the
manuscript endpoint emits volumes + per-part volume_id/kind in the flat wire shape the spine builder reads.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers import books as books_router
from dominion.api.routers import parts as parts_router
from dominion.api.routers import volumes as volumes_router
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, Scene, Volume
from dominion.shared.schemas import (
    PartCreateIn,
    PartVolumeAssignIn,
    VolumeCreateIn,
    VolumeUpdateIn,
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


async def test_create_list_update_delete_volume(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=2, title="Second"), s)
        v1 = await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="First"), s)

        listed = await volumes_router.list_volumes(book.id, s)
        assert [v.volume_no for v in listed] == [1, 2]

        updated = await volumes_router.update_volume(v1.id, VolumeUpdateIn(title="Renamed", subtitle="I"), s)
        assert updated.title == "Renamed"
        assert updated.subtitle == "I"


async def test_create_volume_rejects_duplicate_volume_no(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="First"), s)
        with pytest.raises(HTTPException) as exc:
            await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="Clash"), s)
        assert exc.value.status_code == 409


async def test_assign_and_unassign_part_to_volume(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        vol = await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="V1"), s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="P1"), s)

        assigned = await parts_router.assign_part_volume(part.id, PartVolumeAssignIn(volume_id=vol.id), s)
        assert assigned.volume_id == vol.id

        unassigned = await parts_router.assign_part_volume(part.id, PartVolumeAssignIn(volume_id=None), s)
        assert unassigned.volume_id is None


async def test_assign_part_to_volume_in_other_book_rejected(db_factory):
    async with db_factory() as s:
        book_a = await _book(s, title="A")
        book_b = await _book(s, title="B")
        vol_b = await volumes_router.create_volume(book_b.id, VolumeCreateIn(volume_no=1, title="B1"), s)
        part_a = await parts_router.create_part(book_a.id, PartCreateIn(part_no=1, title="A1"), s)
        with pytest.raises(HTTPException) as exc:
            await parts_router.assign_part_volume(part_a.id, PartVolumeAssignIn(volume_id=vol_b.id), s)
        assert exc.value.status_code == 400


async def test_delete_volume_unassigns_parts_not_deletes_them(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        vol = await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="V1"), s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="P1"), s)
        await parts_router.assign_part_volume(part.id, PartVolumeAssignIn(volume_id=vol.id), s)

        await volumes_router.delete_volume(vol.id, s)

        assert (await s.execute(select(Volume).where(Volume.id == vol.id))).first() is None
        await s.refresh(part)
        assert part.volume_id is None


async def test_create_part_with_act_kind(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        act = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="Rising", kind="act"), s)
        assert act.kind == "act"


async def test_manuscript_emits_volumes_and_part_nesting(db_factory):
    async with db_factory() as s:
        book = await _book(s, title="Realmwalkers")
        vol = await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="The Long Winter"), s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="Frost"), s)
        await parts_router.assign_part_volume(part.id, PartVolumeAssignIn(volume_id=vol.id), s)

        ch1 = await _chapter(s, book, 1, part_id=part.id)
        await _approved_scene(s, ch1, 1, "One.")
        await s.commit()

        ms = await books_router.manuscript(book.id, s)
        assert [v.volume_no for v in ms.volumes] == [1]
        assert ms.parts[0].volume_id == vol.id
        assert ms.parts[0].kind == "part"


async def test_manuscript_omits_volume_with_no_rendered_part(db_factory):
    """A Volume whose only part has no drafted chapter must NOT emit a phantom divider."""
    async with db_factory() as s:
        book = await _book(s, title="B")
        vol = await volumes_router.create_volume(book.id, VolumeCreateIn(volume_no=1, title="Empty"), s)
        part = await parts_router.create_part(book.id, PartCreateIn(part_no=1, title="P"), s)
        await parts_router.assign_part_volume(part.id, PartVolumeAssignIn(volume_id=vol.id), s)
        ch = await _chapter(s, book, 1, part_id=part.id)
        draft = Scene(chapter_id=ch.id, scene_no=1, status=SceneStatus.DRAFT, prose="Draft only.")
        s.add(draft)
        await s.commit()

        ms = await books_router.manuscript(book.id, s)
        assert ms.volumes == []
