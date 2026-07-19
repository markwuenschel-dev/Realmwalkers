"""LEDGER: a beat's declared deltas commit to the ledger exactly once — even when a revision mints a
new Scene for the same (chapter, scene_no) slot and that scene is approved again."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.enums import BeatStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Scene
from dominion.workers.memory import ledger
from dominion.workers.oracle import Oracle


async def _marcus_hp(session, book_id):
    return (await Oracle(session).current(book_id=book_id, character="Marcus")).get("hp")


async def test_declared_deltas_apply_once_across_revision(db_factory):
    async with db_factory() as s:
        book = Book(title="L")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        s.add(
            Beat(
                chapter_id=ch.id,
                scene_no=1,
                beat_text="b",
                status=BeatStatus.APPROVED,
                expected_state_changes={"Marcus": {"hp": "+5"}},
            )
        )
        v1 = Scene(
            chapter_id=ch.id,
            scene_no=1,
            version=1,
            status=SceneStatus.APPROVED,
            prose="p1",
            prose_source="agent",
            agent_original="p1",
        )
        s.add(v1)
        await s.flush()

        await ledger.commit_declared_deltas(s, scene_id=v1.id)
        assert await _marcus_hp(s, book.id) == 5  # first approval applies the +5 once

        # A revision mints a NEW Scene for the same (chapter, scene_no) slot and is approved again.
        v2 = Scene(
            chapter_id=ch.id,
            scene_no=1,
            version=2,
            status=SceneStatus.APPROVED,
            prose="p2",
            prose_source="agent",
            parent_scene_id=v1.id,
            agent_original="p2",
        )
        s.add(v2)
        await s.flush()

        await ledger.commit_declared_deltas(s, scene_id=v2.id)
        assert await _marcus_hp(s, book.id) == 5  # NOT 10 — the beat's delta commits exactly once

        beat = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id, Beat.scene_no == 1))).scalar_one()
        assert beat.deltas_committed is True
