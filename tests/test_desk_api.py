"""Tests for the Desk-facing endpoints: browser draft trigger, world ledger, threads.

The draft-trigger drain logic is exercised without a database (run_once is mocked). The world +
threads endpoints call the router functions directly against real Postgres (like tests/test_gate1.py)
and skip automatically when Postgres isn't reachable (see tests/conftest.py).
"""
from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from dominion.api.routers import jobs as jobs_router
from dominion.api.routers import markup as markup_router
from dominion.api.routers import scenes as scenes_router
from dominion.api.routers import threads as threads_router
from dominion.api.routers import world as world_router
from dominion.shared.enums import JobStatus, SuggestionStatus
from dominion.shared.models import (
    Book,
    CanonEntity,
    Chapter,
    CharacterState,
    Job,
    Run,
    Scene,
)
from dominion.shared.schemas import (
    AnnotationIn,
    CanonEntityIn,
    CanonEntityUpdateIn,
    CharacterStateIn,
    SuggestionDecisionIn,
    SuggestionIn,
    ThreadBeatIn,
    ThreadIn,
    ThreadUpdateIn,
)
from dominion.workers.memory import canon_rag
from dominion.workers.oracle import Oracle

# --- draft trigger (no DB) ------------------------------------------------------------------------

async def test_drain_runs_until_queue_empty(monkeypatch):
    """_drain keeps drafting until run_once reports the queue is empty (returns False)."""
    calls = {"n": 0}

    async def fake_run_once():
        calls["n"] += 1
        return calls["n"] < 3  # two drafted, then empty

    monkeypatch.setattr("dominion.workers.worker.run_once", fake_run_once)
    await jobs_router._drain()
    assert calls["n"] == 3


async def test_drain_keeps_going_after_a_failed_job(monkeypatch):
    """A job that raises is logged + (in run_once) marked FAILED; the drain must not stop."""
    calls = {"n": 0}

    async def flaky_run_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("scene blew up")
        return calls["n"] < 3

    monkeypatch.setattr("dominion.workers.worker.run_once", flaky_run_once)
    await jobs_router._drain()
    assert calls["n"] == 3  # error didn't strand the rest


async def test_drain_is_single_flight(monkeypatch):
    """A second drain while one holds the lock is a no-op (no concurrent LLM storms)."""
    called = {"v": False}

    async def fake_run_once():
        called["v"] = True
        return False

    monkeypatch.setattr("dominion.workers.worker.run_once", fake_run_once)
    await jobs_router._drain_lock.acquire()
    try:
        await jobs_router._drain()
    finally:
        jobs_router._drain_lock.release()
    assert called["v"] is False


# --- draft trigger + status (DB) ------------------------------------------------------------------

async def _seed_queued_jobs(s, n: int) -> None:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    run = Run(book_id=book.id, scope_json={}, gate_mode="pause_each", token_budget=1000)
    s.add(run)
    await s.flush()
    for scene_no in range(1, n + 1):
        s.add(Job(
            run_id=run.id, kind="draft", chapter_no=1, scene_no=scene_no,
            token_budget=1000, status=JobStatus.QUEUED,
        ))
    await s.flush()


async def test_status_reports_queue_depth(db_factory):
    async with db_factory() as s:
        await _seed_queued_jobs(s, 2)
        out = await jobs_router.status(s)
        assert out.queued == 2 and out.failed == 0 and out.active_scene is None


async def test_draft_next_schedules_a_background_drain(db_factory):
    async with db_factory() as s:
        await _seed_queued_jobs(s, 1)
        bg = BackgroundTasks()
        out = await jobs_router.draft_next(bg, s)
        assert out.queued == 1 and out.scheduled is True and out.running is True
        assert len(bg.tasks) == 1  # the drain is scheduled, not run inline


async def test_draft_next_noop_when_queue_empty(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        bg = BackgroundTasks()
        out = await jobs_router.draft_next(bg, s)
        assert out.queued == 0 and out.scheduled is False
        assert len(bg.tasks) == 0


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


# --- world authoring: canon CRUD + character upsert + docs ingest (DB) ----------------------------

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


async def test_ingest_canon_indexes_on_disk_docs(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        out = await world_router.ingest_canon(book.id, s)
        assert out.indexed > 0  # the repo ships series/canon/*.md


# --- threads (DB) ---------------------------------------------------------------------------------

async def test_thread_crud_roundtrip(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()

        created = await threads_router.create_thread(
            book.id, ThreadIn(name="Soren ⇄ Lyra", kind="relationship", state="sealed", note="n"), s
        )
        assert created.name == "Soren ⇄ Lyra" and created.beats == []

        with_beat = await threads_router.add_thread_beat(
            created.id, ThreadBeatIn(scene_no=5, label="threadbound"), s
        )
        assert [b.scene_no for b in with_beat.beats] == [5]

        updated = await threads_router.update_thread(created.id, ThreadUpdateIn(state="active"), s)
        assert updated.state == "active" and updated.name == "Soren ⇄ Lyra"  # untouched field kept

        listed = await threads_router.list_threads(book.id, s)
        assert len(listed) == 1 and listed[0].beats[0].label == "threadbound"

        await threads_router.delete_thread(created.id, s)
        assert await threads_router.list_threads(book.id, s) == []


# --- markup: annotations + suggestions (DB) -------------------------------------------------------

async def _scene(s) -> Scene:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Soren")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, version=2, status="pending_review",
                  prose="He pressed his palm to the door.", prose_source="agent")
    s.add(scene)
    await s.flush()
    return scene


async def test_annotation_crud_stamps_version(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        out = await markup_router.create_annotation(
            scene.id, AnnotationIn(note="echoes ch1", quote="palm", author="Vael"), s
        )
        assert out.note == "echoes ch1" and out.quote == "palm" and out.version == 2

        listed = await markup_router.list_annotations(scene.id, s)
        assert [a.id for a in listed] == [out.id]

        await markup_router.delete_annotation(out.id, s)
        assert await markup_router.list_annotations(scene.id, s) == []


async def test_suggestion_lifecycle(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        out = await markup_router.create_suggestion(
            scene.id, SuggestionIn(quote="palm", new_text="scarred palm", why="plant the scar"), s
        )
        assert out.status == "pending" and out.quote == "palm" and out.version == 2

        decided = await markup_router.decide_suggestion(
            out.id, SuggestionDecisionIn(status=SuggestionStatus.ACCEPTED), s
        )
        assert decided.status == "accepted"

        listed = await markup_router.list_suggestions(scene.id, s)
        assert len(listed) == 1 and listed[0].status == "accepted"

        await markup_router.delete_suggestion(out.id, s)
        assert await markup_router.list_suggestions(scene.id, s) == []


# --- versions: revert (DB) ------------------------------------------------------------------------

async def test_revert_clones_version_and_supersedes_current(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Soren")
        s.add(ch)
        await s.flush()
        v1 = Scene(chapter_id=ch.id, scene_no=1, version=1, status="superseded", prose="first", prose_source="agent")
        v2 = Scene(chapter_id=ch.id, scene_no=1, version=2, status="approved", prose="second", prose_source="agent")
        s.add_all([v1, v2])
        await s.flush()

        out = await scenes_router.revert_scene(v1.id, s)
        assert out.version == 3 and out.prose == "first" and out.status == "approved"

        lineage = await scenes_router.scene_versions(out.id, s)
        assert [(v.version, str(v.status)) for v in lineage] == [
            (1, "superseded"), (2, "superseded"), (3, "approved")
        ]

        # reverting to the version that is already current is a 409
        with pytest.raises(HTTPException):
            await scenes_router.revert_scene(out.id, s)
