"""KNOW-MONO: the reader-knowledge 'known after scene N' marker tracks the EARLIEST reveal, so a later
recap of the same fact never pushes it forward (and a genuinely earlier reveal moves it back)."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, KnowledgeFact, Scene, ScenePacket
from dominion.workers.memory import knowledge


def _reveal_body(fact: str) -> dict:
    return {"learned_during_scene": {"reader_must_learn": [fact]}}


async def _scene_with_reveal(s, book, ch, cp_id, scene_no: int, fact: str) -> Scene:
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp_id,
        scene_no=scene_no,
        status="approved",
        body=_reveal_body(fact),
    )
    s.add(sp)
    await s.flush()
    sc = Scene(
        chapter_id=ch.id,
        scene_no=scene_no,
        version=1,
        status=SceneStatus.APPROVED,
        prose="p",
        prose_source="agent",
        agent_original="p",
        scene_packet_id=sp.id,
    )
    s.add(sc)
    await s.flush()
    return sc


async def _seed_chapter(s):
    book = Book(title="K")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": []},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    return book, ch, cp


async def _fact_marker(s, fact: str):
    return (await s.execute(select(KnowledgeFact).where(KnowledgeFact.fact == fact))).scalar_one()


async def test_later_recap_does_not_advance_marker(db_factory):
    async with db_factory() as s:
        book, ch, cp = await _seed_chapter(s)
        early = await _scene_with_reveal(s, book, ch, cp.id, 3, "F")
        await _scene_with_reveal(s, book, ch, cp.id, 7, "F")  # recap of the same fact
        late = (await s.execute(select(Scene).where(Scene.scene_no == 7))).scalar_one()

        await knowledge.record_scene_reveals(s, scene_id=early.id)
        await knowledge.record_scene_reveals(s, scene_id=late.id)  # must NOT move the marker forward

        assert (await _fact_marker(s, "F")).known_by_reader_after_scene_id == early.id


async def test_earlier_reveal_recorded_later_moves_marker_back(db_factory):
    async with db_factory() as s:
        book, ch, cp = await _seed_chapter(s)
        early = await _scene_with_reveal(s, book, ch, cp.id, 3, "G")
        late = await _scene_with_reveal(s, book, ch, cp.id, 7, "G")

        await knowledge.record_scene_reveals(s, scene_id=late.id)  # recap recorded first -> marker at scene 7
        await knowledge.record_scene_reveals(s, scene_id=early.id)  # earlier reveal -> marker moves back to 3

        assert (await _fact_marker(s, "G")).known_by_reader_after_scene_id == early.id
