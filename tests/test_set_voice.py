"""set_voice authoring CLI: the core upsert finds the book, is idempotent (one row), updates in place."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.models import Book, PovProfile
from dominion.workers.legacy.set_voice import set_voice


async def test_set_voice_creates_then_updates_one_row(db_factory):
    async with db_factory() as s:
        book = Book(title="Voice Test")
        s.add(book)
        await s.flush()

        pid = await set_voice(s, book_title="Voice Test", character="Marcus", voice_spec="terse, salt-stung")
        await s.commit()

        # find-or-create resolved the seeded book, not a duplicate
        assert len((await s.execute(select(Book))).scalars().all()) == 1
        rows = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == pid
        assert rows[0].book_id == book.id
        assert rows[0].voice_spec == "terse, salt-stung"

    # Re-run with new text: same row updated in place, never a second row.
    async with db_factory() as s:
        pid2 = await set_voice(s, book_title="Voice Test", character="Marcus", voice_spec="warmer, wry")
        await s.commit()

        rows = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == pid2
        assert rows[0].voice_spec == "warmer, wry"
