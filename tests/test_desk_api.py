"""Tests for the Desk-facing endpoints: browser draft trigger, world ledger, threads.

The draft-trigger drain logic is exercised without a database (run_once is mocked). The world +
threads endpoints call the router functions directly against real Postgres (like tests/test_gate1.py)
and skip automatically when Postgres isn't reachable (see tests/conftest.py).
"""

from __future__ import annotations

from dominion.api.routers import world as world_router
from dominion.shared.models import Book, CanonEntity, Chapter, CharacterState
from dominion.shared.schemas import CanonEntityIn, CanonEntityUpdateIn, CharacterStateIn
from dominion.workers import background_work as bw
from dominion.workers.memory import canon_rag
from dominion.workers.oracle import Oracle

# --- draft trigger (no DB) ------------------------------------------------------------------------


async def test_drain_is_single_flight(monkeypatch):
    """A second drain while one holds the lock is a no-op (no concurrent LLM storms)."""
    called = {"v": False}

    async def fake_run_once():
        called["v"] = True
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", fake_run_once)
    await bw._drain_lock.acquire()
    try:
        await bw.drain_queued_jobs()
    finally:
        bw._drain_lock.release()
    assert called["v"] is False


# --- world ledger (DB) ----------------------------------------------------------------------------


async def test_characters_merge_stats_canon_and_pov_flag(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        s.add(Chapter(book_id=book.id, chapter_no=1, pov="Soren"))
        s.add(CharacterState(book_id=book.id, character="Soren", stats_json={"level": 15}))
        s.add(CharacterState(book_id=book.id, character="Lyra", stats_json={"status": "sealed"}))
        s.add(CanonEntity(book_id=book.id, kind="character", name="Soren", body="An ascendant."))
        await s.flush()

        chars = await world_router.list_characters(book.id, s)
        soren = next(c for c in chars if c.character == "Soren")
        lyra = next(c for c in chars if c.character == "Lyra")
        assert soren.is_pov and soren.stats["level"] == 15 and soren.body == "An ascendant."
        assert not lyra.is_pov and lyra.body is None


async def test_canon_lists_and_filters_by_kind(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        s.add(CanonEntity(book_id=book.id, kind="character", name="Soren", body="a"))
        s.add(CanonEntity(book_id=book.id, kind="location", name="The Warded Door", body="b"))
        await s.flush()

        assert {e.kind for e in await world_router.list_canon(book.id, s)} == {"character", "location"}
        locs = await world_router.list_canon(book.id, s, kind="location")
        assert len(locs) == 1 and locs[0].name == "The Warded Door"


# --- world authoring: canon CRUD + character upsert (DB) ------------------------------------------


async def test_canon_entity_crud_embeds_and_is_retrievable(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()

        created = await world_router.create_canon(
            book.id, CanonEntityIn(kind="location", name="Eriadne", body="A warded city of glass towers."), s
        )
        assert created.kind == "location" and created.name == "Eriadne"
        # embedded on write -> the drafter/planner RAG can retrieve it immediately
        hits = await canon_rag.retrieve(s, book_id=book.id, query="warded city of glass towers", k=3)
        assert any("glass towers" in h for h in hits)

        updated = await world_router.update_canon(created.id, CanonEntityUpdateIn(body="Rewritten lore."), s)
        assert updated.body == "Rewritten lore."

        await world_router.delete_canon(created.id, s)
        assert await world_router.list_canon(book.id, s) == []


async def test_upsert_character_seeds_oracle_baseline(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()

        out = await world_router.upsert_character(
            book.id, "Soren", CharacterStateIn(stats={"level": 5, "hp": 100}, body="An ascendant."), s
        )
        assert out.character == "Soren" and out.stats["level"] == 5 and out.body == "An ascendant."
        # the Oracle reads this as the current truth
        assert (await Oracle(s).current(book_id=book.id, character="Soren"))["hp"] == 100

        # stats are set wholesale (not merged) on re-upsert
        out2 = await world_router.upsert_character(book.id, "Soren", CharacterStateIn(stats={"level": 6}), s)
        assert out2.stats == {"level": 6}

        await world_router.delete_character(book.id, "Soren", s)
        assert await world_router.list_characters(book.id, s) == []
