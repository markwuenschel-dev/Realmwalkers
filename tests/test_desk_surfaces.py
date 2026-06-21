"""PR-B/PR-C read+write surfaces against real Postgres (DESIGN §9).

Call the router functions directly (as tests/test_gate1.py does). These skip automatically when
Postgres isn't reachable (see tests/conftest.py). The new tables (threads/annotations/suggestions)
are created by conftest's create_all."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from dominion.api.routers import annotations as annotations_router
from dominion.api.routers import books as books_router
from dominion.api.routers import suggestions as suggestions_router
from dominion.api.routers import threads as threads_router
from dominion.shared.enums import SceneStatus, SuggestionStatus
from dominion.shared.models import (
    Book,
    CanonEntity,
    Chapter,
    CharacterState,
    Scene,
    Suggestion,
)
from dominion.shared.schemas import (
    AnnotationIn,
    SuggestionDecisionIn,
    SuggestionIn,
    ThreadIn,
    ThreadUpdateIn,
)


async def _book(s, title="Dominion Realm"):
    book = Book(title=title)
    s.add(book)
    await s.flush()
    return book


async def _scene(s, book) -> Scene:
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, version=1, status=SceneStatus.PENDING_REVIEW,
                  prose="Marcus pressed his scarred palm to the door.", prose_source="agent")
    s.add(scene)
    await s.flush()
    return scene


# --- characters + canon (PR-B) --------------------------------------------------------------------

async def test_characters_extracts_role_and_dedups_per_character(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add(CharacterState(book_id=book.id, character="Marcus", stats_json={"role": "POV", "level": 5}))
        s.add(CharacterState(book_id=book.id, character="Serra", stats_json={"level": 3}))
        await s.flush()

        out = await books_router.characters(book.id, s)
        by_name = {c.character: c for c in out}
        assert set(by_name) == {"Marcus", "Serra"}
        assert by_name["Marcus"].role == "POV"
        assert by_name["Marcus"].stats == {"level": 5}        # role lifted out of the stat rows
        assert by_name["Serra"].role is None

        # a second state row for the same character collapses to one entry (latest-state dedup,
        # matching Oracle.current()'s id.desc() heuristic — exactly one Marcus comes back)
        s.add(CharacterState(book_id=book.id, character="Marcus", stats_json={"role": "POV", "level": 6}))
        await s.flush()
        again = await books_router.characters(book.id, s)
        assert len([c for c in again if c.character == "Marcus"]) == 1


async def test_canon_filters_by_kind(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add_all([
            CanonEntity(book_id=book.id, kind="location", name="The Warded Door", body="A sealed threshold."),
            CanonEntity(book_id=book.id, kind="item", name="Oathblade", body="A bound knife."),
        ])
        await s.flush()

        locations = await books_router.canon(book.id, s, kind="location")
        assert [c.name for c in locations] == ["The Warded Door"]
        every = await books_router.canon(book.id, s, kind=None)
        assert len(every) == 2


# --- threads (PR-C) -------------------------------------------------------------------------------

async def test_thread_create_list_and_partial_update(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        created = await threads_router.create_thread(
            book.id,
            ThreadIn(name="Soren ⇄ Lyra", kind="relationship", state="sealed",
                     beats=[{"scene_no": 1, "label": "oath bound", "flag": False}]),
            s,
        )
        await s.flush()
        listed = await threads_router.list_threads(book.id, s)
        assert [t.name for t in listed] == ["Soren ⇄ Lyra"]

        updated = await threads_router.update_thread(created.id, ThreadUpdateIn(state="active"), s)
        await s.flush()
        assert updated.state == "active"
        assert updated.kind == "relationship"                 # untouched field preserved
        assert updated.beats == [{"scene_no": 1, "label": "oath bound", "flag": False}]


# --- annotations (PR-C) ---------------------------------------------------------------------------

async def test_annotation_create_list_delete(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        scene = await _scene(s, book)
        ann = await annotations_router.create_annotation(
            scene.id, AnnotationIn(quote="scarred palm", author="Vael", note="plant the oath-scar"), s
        )
        await s.flush()
        assert [a.id for a in await annotations_router.list_annotations(scene.id, s)] == [ann.id]

        res = await annotations_router.delete_annotation(scene.id, ann.id, s)
        await s.flush()
        assert res == {"deleted": str(ann.id)}
        assert await annotations_router.list_annotations(scene.id, s) == []


async def test_annotation_create_404_for_missing_scene(db_factory):
    import uuid

    from fastapi import HTTPException
    async with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await annotations_router.create_annotation(uuid.uuid4(), AnnotationIn(note="orphan"), s)
        assert exc.value.status_code == 404


# --- suggestions (PR-C) ---------------------------------------------------------------------------

async def test_suggestion_create_and_decision(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        scene = await _scene(s, book)
        sugg = await suggestions_router.create_suggestion(
            scene.id,
            SuggestionIn(quote="palm", new_text="scarred palm", author="Vael", why="plant the scar"),
            s,
        )
        await s.flush()
        assert sugg.status == SuggestionStatus.PENDING

        decided = await suggestions_router.decide_suggestion(
            sugg.id, SuggestionDecisionIn(status="accepted"), s
        )
        await s.flush()
        assert decided.status == SuggestionStatus.ACCEPTED
        stored = (await s.execute(select(Suggestion).where(Suggestion.id == sugg.id))).scalar_one()
        assert stored.status == "accepted"


async def test_suggestion_decision_rejects_bad_status(db_factory):
    from fastapi import HTTPException
    async with db_factory() as s:
        book = await _book(s)
        scene = await _scene(s, book)
        sugg = await suggestions_router.create_suggestion(scene.id, SuggestionIn(new_text="x"), s)
        await s.flush()
        with pytest.raises(HTTPException) as exc:
            await suggestions_router.decide_suggestion(sugg.id, SuggestionDecisionIn(status="maybe"), s)
        assert exc.value.status_code == 400
