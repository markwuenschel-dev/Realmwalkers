"""Unit tests for read-through response validation (pure: no DB, no model). Synthetic prose only."""

from __future__ import annotations

from typing import Any

from dominion.shared.enums import ReadThroughAnchorRole, ReadThroughNoteCategory, ReadThroughNotePriority
from dominion.workers.read_through.validate import (
    BODY_MAX,
    BOOK_NOTE_CAP,
    CHAPTER_NOTE_CAP,
    DIGEST_ITEM_MAX,
    DIGEST_LIST_MAX,
    DIGEST_SUMMARY_MAX,
    QUOTE_MAX,
    QUOTES_MAX,
    TITLE_MAX,
    validate_book_output,
    validate_chapter_output,
)


def _note(**overrides: Any) -> dict[str, Any]:
    note: dict[str, Any] = {
        "category": "pacing",
        "priority": "high",
        "title": "The harbour wait drags",
        "observation": "Three paragraphs repeat the same beat of waiting.",
        "recommendation": "Compress the wait so the morning arrives sooner.",
        "anchor_role": "evidence",
        "quotes": ["She watched its lamps shrink across the black water"],
    }
    note.update(overrides)
    return note


def _book_note(**overrides: Any) -> dict[str, Any]:
    note = _note(quotes=[], positions=[1, 2])
    note.update(overrides)
    return note


def _digest(**overrides: Any) -> dict[str, Any]:
    digest: dict[str, Any] = {
        "summary": "Ilse misses the ferry and waits out the night at the harbour.",
        "characters": [{"name": "Ilse", "state": "stranded"}],
        "threads_opened": ["how Ilse will pay her passage"],
        "threads_resolved": [],
        "setups": ["the coins counted twice"],
        "timeline": ["night", "morning"],
    }
    digest.update(overrides)
    return digest


def _chapter(notes: list[Any], **extra: Any) -> dict[str, Any]:
    return {"notes": notes, "capped": False, "digest": _digest(), **extra}


def test_invalid_digest_returns_none():
    assert validate_chapter_output(None) is None
    assert validate_chapter_output([_note()]) is None
    assert validate_chapter_output({"notes": "none", "digest": _digest()}) is None
    assert validate_chapter_output({"notes": [_note()]}) is None  # digest missing
    assert validate_chapter_output(_chapter([], digest="a summary")) is None
    assert validate_chapter_output(_chapter([], digest=_digest(summary="   "))) is None
    assert validate_chapter_output(_chapter([], digest=_digest(summary="x" * (DIGEST_SUMMARY_MAX + 1)))) is None
    assert validate_chapter_output(_chapter([], digest=_digest(setups=["s"] * (DIGEST_LIST_MAX + 1)))) is None
    assert validate_chapter_output(_chapter([], digest=_digest(timeline=["x" * (DIGEST_ITEM_MAX + 1)]))) is None
    assert validate_chapter_output(_chapter([], digest=_digest(characters=[{"name": "Ilse"}]))) is None
    assert validate_chapter_output(_chapter([], digest=_digest(threads_opened="one thread"))) is None

    at_bounds = _digest(
        summary="x" * DIGEST_SUMMARY_MAX,
        characters=[{"name": "n" * DIGEST_ITEM_MAX, "state": "s"}] * DIGEST_LIST_MAX,
        timeline=["t" * DIGEST_ITEM_MAX] * DIGEST_LIST_MAX,
    )
    assert validate_chapter_output(_chapter([], digest=at_bounds)) is not None
    sparse = validate_chapter_output(_chapter([], digest={"summary": "Only a summary."}))
    assert sparse is not None and sparse.digest.setups == [] and sparse.digest.characters == []


def test_note_count_and_field_bounds_enforced():
    many = validate_chapter_output(_chapter([_note(title=f"note {i}") for i in range(CHAPTER_NOTE_CAP + 2)]))
    assert many is not None
    assert [note.title for note in many.notes] == [f"note {i}" for i in range(CHAPTER_NOTE_CAP)]
    assert many.capped is True
    assert many.dropped_invalid == 0

    at_max = _note(
        title="t" * TITLE_MAX,
        observation="o" * BODY_MAX,
        recommendation="r" * BODY_MAX,
        quotes=["q" * QUOTE_MAX] * QUOTES_MAX,
    )
    over = [
        _note(title="t" * (TITLE_MAX + 1)),
        _note(observation="o" * (BODY_MAX + 1)),
        _note(recommendation="r" * (BODY_MAX + 1)),
        _note(quotes=["q" * (QUOTE_MAX + 1)]),
        _note(quotes=["a quoted passage here"] * (QUOTES_MAX + 1)),
        _note(quotes=[]),
    ]
    result = validate_chapter_output(_chapter([at_max, *over]))
    assert result is not None
    assert len(result.notes) == 1 and result.notes[0].title == "t" * TITLE_MAX
    assert result.dropped_invalid == len(over)
    assert result.capped is False

    book = validate_book_output({"notes": [_book_note(title=f"b{i}") for i in range(BOOK_NOTE_CAP + 1)]})
    assert book is not None
    assert len(book.notes) == BOOK_NOTE_CAP and book.capped is True


def test_bad_item_dropped_and_counted():
    notes = [
        _note(title="kept first", extra_field="ignored"),
        _note(category="tone"),
        _note(priority="urgent"),
        _note(title="   "),
        "not an object",
        _note(anchor_role="proof"),
        _note(quotes=["a real quoted passage", "   "]),
        _note(observation=42),
        _note(title="kept second", category=" Setup_Payoff ", priority="LOW", anchor_role=None),
    ]
    result = validate_chapter_output(_chapter(notes))
    assert result is not None
    assert [note.title for note in result.notes] == ["kept first", "kept second"]
    assert result.dropped_invalid == 7
    second = result.notes[1]
    assert second.category is ReadThroughNoteCategory.SETUP_PAYOFF
    assert second.priority is ReadThroughNotePriority.LOW
    assert second.anchor_role is ReadThroughAnchorRole.EVIDENCE

    no_role = _note()
    del no_role["anchor_role"]
    defaulted = validate_chapter_output(_chapter([no_role]))
    assert defaulted is not None and defaulted.notes[0].anchor_role is ReadThroughAnchorRole.EVIDENCE


def test_book_note_positions_required():
    missing = _book_note()
    del missing["positions"]
    notes = [
        missing,
        _book_note(positions=[]),
        _book_note(positions=[True]),
        _book_note(positions=[-1]),
        _book_note(positions="1"),
        _book_note(title="kept", positions=[2, 1, 2], anchor_role="location"),
    ]
    result = validate_book_output({"notes": notes})
    assert result is not None
    assert [note.title for note in result.notes] == ["kept"]
    assert result.notes[0].positions == [2, 1]
    assert result.notes[0].quotes == []
    assert result.notes[0].anchor_role is ReadThroughAnchorRole.LOCATION
    assert result.dropped_invalid == 5

    empty = validate_book_output({"notes": []})  # a book pass carries no digest
    assert empty is not None and empty.notes == [] and empty.capped is False
    assert validate_book_output({"notes": None}) is None
    assert validate_book_output("notes") is None


def test_model_capped_flag_respected():
    two = [_note(), _note()]
    for flag, expected in ((True, True), (False, False), ("true", False)):
        chapter = validate_chapter_output(_chapter(two, capped=flag))
        assert chapter is not None and chapter.capped is expected and len(chapter.notes) == 2
        book = validate_book_output({"notes": [_book_note()], "capped": flag})
        assert book is not None and book.capped is expected
    uncapped = validate_chapter_output({"notes": two, "digest": _digest()})
    assert uncapped is not None and uncapped.capped is False
