"""GET /chapters/{id}/packet vs GET /chapters/{id}/packet/authority (#261 H6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dominion.shared.enums import PacketStatus
from dominion.shared.models import Book, Chapter, ChapterPacket


async def _chapter(s) -> tuple[Book, Chapter]:
    book = Book(title="Authority Read")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return book, ch


async def test_authority_404_when_no_approved_packet(app_client, db_factory):
    async with db_factory() as s:
        _, ch = await _chapter(s)
        await s.commit()
        chapter_id = ch.id

    resp = await app_client.get(f"/chapters/{chapter_id}/packet/authority")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no approved packet for this chapter"


async def test_authority_returns_the_approved_row_when_it_is_the_only_packet(app_client, db_factory):
    async with db_factory() as s:
        book, ch = await _chapter(s)
        approved = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.APPROVED,
            body={"scene_seeds": []},
            open_questions={"items": []},
        )
        s.add(approved)
        await s.commit()
        chapter_id, approved_id = ch.id, approved.id

    newest = await app_client.get(f"/chapters/{chapter_id}/packet")
    authority = await app_client.get(f"/chapters/{chapter_id}/packet/authority")
    assert newest.status_code == 200 and authority.status_code == 200
    assert newest.json()["id"] == str(approved_id)
    assert authority.json()["id"] == str(approved_id)
    assert authority.json()["status"] == "approved"


async def test_authority_stays_on_predecessor_when_an_amendment_is_proposed(app_client, db_factory):
    async with db_factory() as s:
        book, ch = await _chapter(s)
        approved = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.APPROVED,
            body={"scene_seeds": []},
            open_questions={"items": []},
        )
        s.add(approved)
        await s.flush()
        proposal = ChapterPacket(
            book_id=book.id,
            chapter_id=ch.id,
            status=PacketStatus.PROPOSED,
            origin_mode="amendment",
            supersedes_packet_id=approved.id,
            body={"scene_seeds": []},
            open_questions={"items": []},
            created_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        s.add(proposal)
        await s.commit()
        chapter_id, approved_id, proposal_id = ch.id, approved.id, proposal.id

    newest = await app_client.get(f"/chapters/{chapter_id}/packet")
    authority = await app_client.get(f"/chapters/{chapter_id}/packet/authority")
    assert newest.status_code == 200 and authority.status_code == 200
    assert newest.json()["id"] == str(proposal_id)
    assert newest.json()["status"] == "proposed"
    assert authority.json()["id"] == str(approved_id)
    assert authority.json()["status"] == "approved"
