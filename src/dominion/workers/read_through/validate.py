"""Enforce the read-through response contract on whatever object the model returned.

Bounds live HERE as pydantic constraints, not only in the prompt: a prompt asks, this checks. Each note
item is validated on its own (pattern: ``scene_fidelity/adapters.py:64-97``) — one bad item (unknown
category, over-long field, wrong quote count, blank string) is dropped and COUNTED, never allowed to sink
the notes around it. What does sink the response is structural: not an object, ``notes`` not a list, or
(chapter output) a missing/invalid digest, because the book pass depends on that digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from dominion.shared.enums import ReadThroughAnchorRole, ReadThroughNoteCategory, ReadThroughNotePriority

__all__ = [
    "BODY_MAX",
    "BOOK_NOTE_CAP",
    "CHAPTER_NOTE_CAP",
    "DIGEST_ITEM_MAX",
    "DIGEST_LIST_MAX",
    "DIGEST_SUMMARY_MAX",
    "QUOTES_MAX",
    "QUOTE_MAX",
    "TITLE_MAX",
    "BookResult",
    "ChapterResult",
    "CharacterState",
    "Digest",
    "RawBookNote",
    "RawChapterNote",
    "validate_book_output",
    "validate_chapter_output",
]

CHAPTER_NOTE_CAP = 8
BOOK_NOTE_CAP = 12
TITLE_MAX = 120
BODY_MAX = 1200
QUOTE_MAX = 400
QUOTES_MAX = 3
DIGEST_SUMMARY_MAX = 1000
DIGEST_LIST_MAX = 20
DIGEST_ITEM_MAX = 200

_Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX)]
_Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=BODY_MAX)]
_Quote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUOTE_MAX)]
_DigestSummary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DIGEST_SUMMARY_MAX)]
_DigestItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DIGEST_ITEM_MAX)]
_Position = Annotated[int, Field(ge=0)]


class _NoteBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: ReadThroughNoteCategory
    priority: ReadThroughNotePriority
    title: _Title
    observation: _Body
    recommendation: _Body
    anchor_role: ReadThroughAnchorRole = ReadThroughAnchorRole.EVIDENCE

    @field_validator("category", "priority", mode="before")
    @classmethod
    def _enum_word(cls, value: object) -> object:
        """ "High" / " pacing " are the vocabulary word with different case or padding, not a new word."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("anchor_role", mode="before")
    @classmethod
    def _anchor_role_word(cls, value: object) -> object:
        if value is None:
            return ReadThroughAnchorRole.EVIDENCE
        return value.strip().lower() if isinstance(value, str) else value


class RawChapterNote(_NoteBase):
    """One chapter note as the model reported it. Its quotes are not yet located (see ``anchors.locate``)."""

    quotes: list[_Quote] = Field(min_length=1, max_length=QUOTES_MAX)


class RawBookNote(_NoteBase):
    """One cross-chapter note. ``positions`` are the chapter positions it concerns, as the prompt's headings
    number them; whether each names a chapter in this run is the caller's check (it holds the chapters)."""

    quotes: list[_Quote] = Field(default_factory=list, max_length=QUOTES_MAX)
    positions: list[_Position] = Field(min_length=1)

    @field_validator("positions", mode="before")
    @classmethod
    def _no_bool_positions(cls, value: object) -> object:
        # `True` is an int to pydantic's lax mode; a model writing `[true]` has not named chapter 1.
        if isinstance(value, list) and any(isinstance(item, bool) for item in value):
            raise ValueError("positions must be integers")
        return value

    @field_validator("positions")
    @classmethod
    def _dedupe_positions(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(value))


class CharacterState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: _DigestItem
    state: _DigestItem


class Digest(BaseModel):
    """A bounded factual record of one chapter, read by the book pass when the full text will not fit."""

    model_config = ConfigDict(extra="ignore")

    summary: _DigestSummary
    characters: list[CharacterState] = Field(default_factory=list, max_length=DIGEST_LIST_MAX)
    threads_opened: list[_DigestItem] = Field(default_factory=list, max_length=DIGEST_LIST_MAX)
    threads_resolved: list[_DigestItem] = Field(default_factory=list, max_length=DIGEST_LIST_MAX)
    setups: list[_DigestItem] = Field(default_factory=list, max_length=DIGEST_LIST_MAX)
    timeline: list[_DigestItem] = Field(default_factory=list, max_length=DIGEST_LIST_MAX)


@dataclass
class ChapterResult:
    notes: list[RawChapterNote]
    digest: Digest
    capped: bool  # the model said it held notes back, or sent more valid notes than the cap
    dropped_invalid: int  # note items that failed validation


@dataclass
class BookResult:
    notes: list[RawBookNote]
    capped: bool
    dropped_invalid: int


def _validate_items[NoteT: BaseModel](items: list[Any], model: type[NoteT]) -> tuple[list[NoteT], int]:
    valid: list[NoteT] = []
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            valid.append(model.model_validate(item))
        except ValidationError:
            dropped += 1
    return valid, dropped


def validate_chapter_output(obj: object) -> ChapterResult | None:
    """Validate a chapter response (the dict ``packet.parse.extract_object`` returned). None = unusable."""
    if not isinstance(obj, dict):
        return None
    raw_notes = obj.get("notes")
    if not isinstance(raw_notes, list):
        return None
    try:
        digest = Digest.model_validate(obj.get("digest"))
    except ValidationError:
        return None
    notes, dropped = _validate_items(raw_notes, RawChapterNote)
    capped = obj.get("capped") is True or len(notes) > CHAPTER_NOTE_CAP
    return ChapterResult(notes=notes[:CHAPTER_NOTE_CAP], digest=digest, capped=capped, dropped_invalid=dropped)


def validate_book_output(obj: object) -> BookResult | None:
    """Validate a book-pass response. None = unusable (not an object, or ``notes`` not a list)."""
    if not isinstance(obj, dict):
        return None
    raw_notes = obj.get("notes")
    if not isinstance(raw_notes, list):
        return None
    notes, dropped = _validate_items(raw_notes, RawBookNote)
    capped = obj.get("capped") is True or len(notes) > BOOK_NOTE_CAP
    return BookResult(notes=notes[:BOOK_NOTE_CAP], capped=capped, dropped_invalid=dropped)
