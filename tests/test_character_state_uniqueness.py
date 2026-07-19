"""CHAR-UNIQ: one CharacterState row per (book, character), case-insensitive.

Unit tests pin the pure dedup-merge rule; DB-gated tests prove the functional unique index rejects a
case variant and that a case-folded read finds the row under any casing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from dominion.shared.migrations import merge_character_state_group
from dominion.shared.models import Book, CharacterState
from dominion.workers.oracle import Oracle

# --- pure dedup-merge rule (no DB) ----------------------------------------------------------------


def test_survivor_prefers_non_null_as_of_and_inherits_keys():
    a = {"id": "a", "stats_json": {"hp": 10}, "as_of_scene_id": None}
    b = {"id": "b", "stats_json": {"mp": 5}, "as_of_scene_id": uuid4()}
    survivor_id, merged = merge_character_state_group([a, b])
    assert survivor_id == "b"  # non-null as_of wins even with equal key count
    assert merged == {"mp": 5, "hp": 10}  # survivor's own + inherited missing key


def test_fills_missing_keys_and_unions_lists():
    a = {"id": "a", "stats_json": {"hp": 10, "items": ["sword"]}, "as_of_scene_id": uuid4()}
    b = {"id": "b", "stats_json": {"mp": 5, "items": ["shield"]}, "as_of_scene_id": None}
    survivor_id, merged = merge_character_state_group([a, b])
    assert survivor_id == "a"
    assert merged["hp"] == 10 and merged["mp"] == 5
    assert merged["items"] == ["sword", "shield"]  # set-union, order preserved


def test_survivor_wins_scalar_conflict():
    a = {"id": "a", "stats_json": {"hp": 10}, "as_of_scene_id": uuid4()}
    b = {"id": "b", "stats_json": {"hp": 99}, "as_of_scene_id": None}
    survivor_id, merged = merge_character_state_group([a, b])
    assert survivor_id == "a"
    assert merged["hp"] == 10  # divergent-scalar reconciliation is out of scope; survivor keeps its value


def test_tiebreak_more_keys_then_lowest_id():
    # Both as_of NULL -> more keys wins despite a higher id.
    a = {"id": "zzz", "stats_json": {"hp": 10, "mp": 5}, "as_of_scene_id": None}
    b = {"id": "aaa", "stats_json": {"hp": 1}, "as_of_scene_id": None}
    assert merge_character_state_group([a, b])[0] == "zzz"
    # Equal keys -> lowest id wins.
    c = {"id": "yyy", "stats_json": {"hp": 1}, "as_of_scene_id": None}
    d = {"id": "aaa", "stats_json": {"mp": 1}, "as_of_scene_id": None}
    assert merge_character_state_group([c, d])[0] == "aaa"


# --- constraint + case-folded read (DB) -----------------------------------------------------------


async def test_unique_index_rejects_case_variant(db_factory):
    async with db_factory() as s:
        book = Book(title="U")
        s.add(book)
        await s.flush()
        s.add(CharacterState(book_id=book.id, character="Vessa", stats_json={"hp": 10}))
        await s.flush()
        s.add(CharacterState(book_id=book.id, character="vessa", stats_json={"hp": 20}))
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_distinct_characters_coexist(db_factory):
    async with db_factory() as s:
        book = Book(title="U")
        s.add(book)
        await s.flush()
        s.add(CharacterState(book_id=book.id, character="Vessa", stats_json={}))
        s.add(CharacterState(book_id=book.id, character="Astria", stats_json={}))
        await s.flush()  # different characters, no collision


async def test_oracle_finds_row_under_different_casing(db_factory):
    async with db_factory() as s:
        book = Book(title="U")
        s.add(book)
        await s.flush()
        s.add(CharacterState(book_id=book.id, character="Vessa", stats_json={"hp": 7}))
        await s.flush()
        stats = await Oracle(s).current(book_id=book.id, character="vessa")  # different case
        assert stats["hp"] == 7
