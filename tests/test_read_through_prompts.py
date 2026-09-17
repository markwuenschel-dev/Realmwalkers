"""Unit tests for the read-through prompt builder (pure: no DB, no provider traffic). Synthetic prose only."""

from __future__ import annotations

import json
import re

import pytest

from dominion.shared.config import settings
from dominion.shared.enums import ReadThroughNoteCategory
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.read_through.prompts import (
    PROMPT_VERSION,
    BookChapterInput,
    ChapterInput,
    PromptParts,
    build_book_prompt,
    build_chapter_prompt,
    estimated_input_tokens,
    snapshot_settings,
)

_PROSE = (
    "The ferry left without Ilse. She watched its lamps shrink across the black water, counting the "
    "coins in her pocket twice.\n\n***\n\nBy morning the harbour smelled of tar and rain.\n"
)
_DIGEST = {
    "summary": "Ilse misses the ferry and waits out the night at the harbour.",
    "characters": [{"name": "Ilse", "state": "stranded and short of money"}],
    "threads_opened": ["how Ilse will pay her passage"],
    "threads_resolved": [],
    "setups": ["the coins counted twice"],
    "timeline": ["night", "the next morning"],
}
_LIMITS = (
    "read_through_chapter_max_tokens",
    "read_through_chapter_input_budget",
    "read_through_book_max_tokens",
    "read_through_book_input_budget",
)


def _book_chapters() -> list[BookChapterInput]:
    return [
        BookChapterInput(position=1, label="The Harbour", text=_PROSE, digest=_DIGEST, note_titles=("Slow open",)),
        BookChapterInput(position=2, label="Tar and\nRain", text=_PROSE, digest=_DIGEST, note_titles=()),
    ]


async def _llm_own_estimate(parts: PromptParts, model: str) -> int:
    """The number `llm.complete` itself compares to `input_budget`, read from its refusal. The prompt
    budget gate raises before any provider traffic (llm.py:629-639)."""
    with pytest.raises(llm.PromptBudgetExceeded) as excinfo:
        await llm.complete(
            model=model,
            system=parts.system,
            user=parts.user,
            max_tokens=parts.max_tokens,
            budget=TokenBudget(max_tokens=10_000_000),
            input_budget=-1,
        )
    match = re.search(r"estimated_input_tokens=(\d+)", str(excinfo.value))
    assert match is not None
    return int(match.group(1))


async def test_admission_estimate_uses_same_builder_as_execution():
    snapshot = snapshot_settings()
    stored = json.loads(json.dumps(snapshot))  # what the worker reads back from the JSONB column
    chapter = ChapterInput(position=3, label="The Harbour", text=_PROSE * 40)

    admitted = build_chapter_prompt(snapshot, "Plain, cold sentences.", chapter)
    executed = build_chapter_prompt(stored, "Plain, cold sentences.", chapter)
    assert admitted == executed
    assert estimated_input_tokens(admitted) == await _llm_own_estimate(executed, snapshot["model"])

    for mode in ("full_text", "digests"):
        book = build_book_prompt(stored, None, _book_chapters(), mode)
        assert book == build_book_prompt(snapshot, None, _book_chapters(), mode)
        assert estimated_input_tokens(book) == await _llm_own_estimate(book, snapshot["model"])


def test_snapshot_limits_used_not_live_settings(monkeypatch: pytest.MonkeyPatch):
    snapshot = snapshot_settings()
    assert snapshot["prompt_version"] == PROMPT_VERSION
    assert snapshot["model"] == settings.read_through_model
    assert snapshot["fallback_model"] == settings.read_through_fallback_model
    expected = {
        name
        for name in type(settings).model_fields
        if name.startswith("read_through_") and name not in {"read_through_model", "read_through_fallback_model"}
    }
    assert expected <= snapshot.keys() and set(_LIMITS) <= expected
    assert json.loads(json.dumps(snapshot)) == snapshot

    chapter = ChapterInput(position=1, label="The Harbour", text=_PROSE)
    chapter_before = build_chapter_prompt(snapshot, None, chapter)
    book_before = build_book_prompt(snapshot, None, _book_chapters(), "full_text")

    for name in _LIMITS:
        monkeypatch.setattr(settings, name, 7)
    monkeypatch.setattr(settings, "read_through_model", "a-different-model")

    chapter_after = build_chapter_prompt(snapshot, None, chapter)
    book_after = build_book_prompt(snapshot, None, _book_chapters(), "full_text")
    assert chapter_after == chapter_before
    assert book_after == book_before
    assert chapter_after.max_tokens == snapshot["read_through_chapter_max_tokens"] != 7
    assert chapter_after.input_budget == snapshot["read_through_chapter_input_budget"] != 7
    assert book_after.max_tokens == snapshot["read_through_book_max_tokens"] != 7
    assert book_after.input_budget == snapshot["read_through_book_input_budget"] != 7
    assert snapshot_settings()["read_through_chapter_max_tokens"] == 7  # only a NEW snapshot sees the change


def test_voice_guide_absent_is_stated():
    snapshot = snapshot_settings()
    chapter = ChapterInput(position=1, label="The Harbour", text=_PROSE)
    absent = "No voice guide was supplied"

    assert absent in build_chapter_prompt(snapshot, None, chapter).user
    assert absent in build_chapter_prompt(snapshot, "  \n ", chapter).user
    assert absent in build_book_prompt(snapshot, None, _book_chapters(), "full_text").user

    present = build_chapter_prompt(snapshot, "Short declaratives. No adverbs.", chapter).user
    assert absent not in present
    assert "Short declaratives. No adverbs." in present
    assert "INTENDED voice" in present


def test_digest_mode_forbids_absence_claims_in_prompt():
    snapshot = snapshot_settings()
    digests = build_book_prompt(snapshot, None, _book_chapters(), "digests").user
    full = build_book_prompt(snapshot, None, _book_chapters(), "full_text").user

    assert "you are reading SUMMARIES, not the manuscript" in digests
    assert "Never assert that something is missing, unexplained, absent" in digests
    assert "something for the author to verify" in digests
    assert "Ilse misses the ferry" in digests  # the digest is what it reads...
    assert "black water" not in digests  # ...and never the chapter text

    assert "SUMMARIES" not in full
    assert "black water" in full


def test_headings_contract_and_mode_inputs():
    snapshot = snapshot_settings()
    chapter_user = build_chapter_prompt(snapshot, None, ChapterInput(position=3, label="The Harbour", text=_PROSE)).user
    assert "=== CHAPTER 3: The Harbour ===" in chapter_user
    assert all(f'"{category.value}"' in chapter_user for category in ReadThroughNoteCategory)
    assert '"capped"' in chapter_user and '"digest"' in chapter_user and '"anchor_role"' in chapter_user

    book = build_book_prompt(snapshot, None, _book_chapters(), "full_text")
    assert "=== CHAPTER 1: The Harbour ===" in book.user
    assert "=== CHAPTER 2: Tar and Rain ===" in book.user  # a label never breaks its heading line
    assert "- Slow open" in book.user
    assert '"positions"' in book.user
    assert "developmental editor" in book.system and "NEVER REWRITE" in book.system

    orphan = BookChapterInput(position=4, label="Four", text=None, digest=None, note_titles=())
    with pytest.raises(ValueError):
        build_book_prompt(snapshot, None, [orphan], "full_text")
    with pytest.raises(ValueError):
        build_book_prompt(snapshot, None, [orphan], "digests")
    with pytest.raises(ValueError):
        build_book_prompt(snapshot, None, _book_chapters(), "summaries")
    with pytest.raises(ValueError):
        build_book_prompt(snapshot, None, [], "full_text")
