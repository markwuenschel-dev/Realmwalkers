"""CANON-STATUS: the canon retrievability rule lives in exactly one place (models.canon_retrievable_filter),
imported by both retrieval paths; stale/retired canon is excluded and active + legacy-NULL rows surface."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.models import Book, CanonEntity, canon_retrievable_filter


def test_single_shared_retrievable_filter():
    import dominion.workers.memory.canon_rag as canon_rag
    import dominion.workers.memory.retrieval as retrieval

    assert not hasattr(retrieval, "_active_only")  # no local duplicate remains
    assert not hasattr(canon_rag, "_active_only")
    assert retrieval.canon_retrievable_filter is canon_retrievable_filter
    assert canon_rag.canon_retrievable_filter is canon_retrievable_filter


async def test_filter_excludes_non_active_and_keeps_null(db_factory):
    async with db_factory() as s:
        book = Book(title="C")
        s.add(book)
        await s.flush()
        s.add(CanonEntity(book_id=book.id, kind="lore", name="active_row", status="active"))
        s.add(CanonEntity(book_id=book.id, kind="lore", name="retired_row", status="retired"))
        s.add(CanonEntity(book_id=book.id, kind="lore", name="legacy_null", status=None))  # pre-column row
        await s.flush()
        names = set(
            (
                await s.execute(
                    select(CanonEntity.name).where(CanonEntity.book_id == book.id, canon_retrievable_filter())
                )
            ).scalars()
        )
        assert names == {"active_row", "legacy_null"}  # retired excluded; NULL treated as active
