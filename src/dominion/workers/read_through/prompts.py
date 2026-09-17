"""The read-through prompts — ONE builder for admission-time estimation and for execution.

Admission builds every chapter prompt from the run's settings snapshot to refuse an over-budget chapter
before anything is queued; the worker later builds the same prompts from the same snapshot. Because the
limits come from the snapshot and never from live ``settings``, a settings change mid-run cannot make a
later chapter's prompt differ from what admission checked. ``estimated_input_tokens`` is the exact number
``llm.complete`` compares to ``input_budget`` (llm.py:619-632).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dominion.shared.config import settings
from dominion.shared.enums import (
    ReadThroughAnchorRole,
    ReadThroughBookInputMode,
    ReadThroughNoteCategory,
    ReadThroughNotePriority,
)
from dominion.workers import llm
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
)

__all__ = [
    "PROMPT_VERSION",
    "BookChapterInput",
    "ChapterInput",
    "PromptParts",
    "build_book_prompt",
    "build_chapter_prompt",
    "estimated_input_tokens",
    "snapshot_settings",
]

PROMPT_VERSION = "read-through-v1"

# Settings whose values are snapshotted under their own names; the two model roles are stored as
# "model" / "fallback_model" instead.
_SETTING_PREFIX = "read_through_"
_MODEL_SETTINGS = {"read_through_model": "model", "read_through_fallback_model": "fallback_model"}
_CHAPTER_MAX_TOKENS = "read_through_chapter_max_tokens"
_CHAPTER_INPUT_BUDGET = "read_through_chapter_input_budget"
_BOOK_MAX_TOKENS = "read_through_book_max_tokens"
_BOOK_INPUT_BUDGET = "read_through_book_input_budget"


@dataclass(frozen=True)
class PromptParts:
    system: str
    user: str
    max_tokens: int
    input_budget: int


@dataclass(frozen=True)
class ChapterInput:
    position: int
    label: str
    text: str


@dataclass(frozen=True)
class BookChapterInput:
    position: int
    label: str
    text: str | None  # required in full_text mode
    digest: dict[str, Any] | None  # required in digests mode
    note_titles: tuple[str, ...]  # this chapter's own notes, so the book pass does not repeat them


def _choices(values: Sequence[str]) -> str:
    return "|".join(f'"{value}"' for value in values)


_CATEGORIES = _choices([c.value for c in ReadThroughNoteCategory])
_PRIORITIES = _choices([p.value for p in ReadThroughNotePriority])
_ANCHOR_ROLES = _choices([r.value for r in ReadThroughAnchorRole])

_SYSTEM = (
    "You are a developmental editor giving an author notes on their own book. You read whole chapters "
    "the way a trusted editor reads a manuscript: for structure, pacing, character, continuity, setups "
    "and payoffs, and clarity — what a reader will experience, where they will be lost, bored or "
    "unconvinced, and why. You are not a line editor and this is not a rules audit: do not correct "
    "grammar, word choice or sentence rhythm, and do not score the prose against a checklist.\n\n"
    "Rules that matter more than coverage:\n"
    "1. RECOMMEND, NEVER REWRITE. Say what to change and why. Never write replacement prose, sample "
    "sentences or dialogue for the author.\n"
    "2. THE VOICE IS THE AUTHOR'S. A voice guide, when one is supplied, describes the voice the author "
    "INTENDS. Never recommend moving away from it, and never report a choice it calls for as a problem.\n"
    "3. QUOTE ONLY WHAT IS THERE. Copy quoted text exactly, character for character, including any "
    "markdown such as * or _.\n"
    "4. FEWER, BETTER NOTES. Rank by how much the change would improve the reader's experience. A chapter "
    "that needs few notes gets few; never manufacture a finding.\n"
    "5. Return ONLY one JSON object: no prose before or after it, and no code fences."
)

_QUOTE_RULES = (
    f"- Copy each quote VERBATIM from the chapter, including any markdown. 5 to 40 words, one sentence or "
    f"less, under {QUOTE_MAX} characters. To leave words out inside a quote, write … in their place.\n"
    "- Every part of one quote must come from the same scene: never join text across a scene break (a "
    "line such as *** or --- or a Scene heading).\n"
    '- anchor_role "evidence": the quotes show the problem itself. anchor_role "location": the quotes '
    "mark WHERE the concern applies — for example the passage where a reader first needs context they "
    "have not been given. A location quote only locates the concern; it is not proof that something is "
    "absent.\n"
)

_NOTE_FIELDS = (
    f'"category": {_CATEGORIES}, "priority": {_PRIORITIES}, "title": str, "observation": str, '
    f'"recommendation": str, "anchor_role": {_ANCHOR_ROLES}'
)

_CHAPTER_CONTRACT = (
    "RESPONSE CONTRACT — return exactly one JSON object, no code fences:\n"
    '{"notes": [NOTE, ...], "capped": bool, "digest": DIGEST}\n\n'
    f'NOTE = {{{_NOTE_FIELDS}, "quotes": [str, ...]}}\n'
    f"- At most {CHAPTER_NOTE_CAP} notes, the most important first. If more than {CHAPTER_NOTE_CAP} issues "
    f'deserved a note, keep the {CHAPTER_NOTE_CAP} most important and set "capped": true; otherwise '
    '"capped": false.\n'
    f"- title: at most {TITLE_MAX} characters. observation: what the reader experiences and why, at most "
    f"{BODY_MAX} characters. recommendation: what to change — never the words to change it to — at most "
    f"{BODY_MAX} characters.\n"
    f"- quotes: 1 to {QUOTES_MAX} per note.\n" + _QUOTE_RULES + "\n"
    'DIGEST = {"summary": str, "characters": [{"name": str, "state": str}], "threads_opened": [str], '
    '"threads_resolved": [str], "setups": [str], "timeline": [str]}\n'
    "- A factual record of this chapter for a later cross-chapter read — what happens, not your opinion "
    "of it. state: where the character stands at the end of the chapter. setups: details planted for "
    "later. timeline: when events happen, in order.\n"
    f"- summary: at most {DIGEST_SUMMARY_MAX} characters. Each list: at most {DIGEST_LIST_MAX} items, "
    f"each at most {DIGEST_ITEM_MAX} characters.\n\n"
    'The digest is always required. An empty notes list is a real answer: {"notes": [], "capped": false, '
    '"digest": {...}}.'
)

_BOOK_CONTRACT = (
    "RESPONSE CONTRACT — return exactly one JSON object, no code fences:\n"
    '{"notes": [BOOK_NOTE, ...], "capped": bool}\n\n'
    f'BOOK_NOTE = {{{_NOTE_FIELDS}, "positions": [int, ...], "quotes": [str, ...]}}\n'
    f"- At most {BOOK_NOTE_CAP} notes about the book ACROSS its chapters: arcs, structure and pacing "
    "across chapters, continuity between chapters, setups and their payoffs, and the promises the early "
    "chapters make. Only raise what takes more than one chapter to see, and never repeat a note already "
    "given on a single chapter (listed under each chapter below).\n"
    f"- Most important first. If more than {BOOK_NOTE_CAP} deserved a note, keep the {BOOK_NOTE_CAP} most "
    'important and set "capped": true; otherwise "capped": false.\n'
    "- positions: the chapter numbers from the CHAPTER headings that this note concerns — at least one.\n"
    f"- title: at most {TITLE_MAX} characters. observation and recommendation: at most {BODY_MAX} "
    "characters each; recommend what to change, never the words to change it to.\n"
    f"- quotes: 0 to {QUOTES_MAX}, each taken from one of the chapters listed in positions.\n" + _QUOTE_RULES
)

_FULL_TEXT_NOTICE = "You are reading the FULL TEXT of every chapter listed below."

_DIGEST_NOTICE = (
    "IMPORTANT: you are reading SUMMARIES, not the manuscript. Each chapter below is a short digest that "
    "leaves most of the text out, so you cannot know what the chapters do or do not contain. Never assert "
    "that something is missing, unexplained, absent, dropped or never set up. Phrase any such concern as "
    'something for the author to verify — "check whether the ferry debt is established before chapter 4" '
    "— not as a finding. You have no manuscript text to quote, so every quotes list must be empty."
)


def snapshot_settings() -> dict[str, Any]:
    """The values a run is admitted and executed under: prompt version, both model roles, and every
    ``settings.read_through_*`` limit under its own name. Plain JSON types only."""
    snapshot: dict[str, Any] = {
        "prompt_version": PROMPT_VERSION,
        "model": settings.read_through_model,
        "fallback_model": settings.read_through_fallback_model,
    }
    for name in type(settings).model_fields:
        if name.startswith(_SETTING_PREFIX) and name not in _MODEL_SETTINGS:
            snapshot[name] = getattr(settings, name)
    return snapshot


def _one_line(label: str) -> str:
    return " ".join(label.split())


def _voice_guide_section(voice_guide: str | None) -> str:
    if voice_guide and voice_guide.strip():
        return (
            "=== VOICE GUIDE: the author's INTENDED voice — never recommend against it ===\n"
            f"{voice_guide.strip()}\n"
            "=== END OF VOICE GUIDE ==="
        )
    return (
        "=== VOICE GUIDE ===\n"
        "No voice guide was supplied for this read-through. Take the intended voice from the text itself, "
        "and do not treat a deliberate, consistent stylistic choice as a problem.\n"
        "=== END OF VOICE GUIDE ==="
    )


def build_chapter_prompt(snapshot: Mapping[str, Any], voice_guide: str | None, chapter: ChapterInput) -> PromptParts:
    """The prompt for one chapter call. Limits come from ``snapshot`` (see ``snapshot_settings``)."""
    heading = f"CHAPTER {chapter.position}: {_one_line(chapter.label)}"
    user = "\n\n".join(
        [
            _voice_guide_section(voice_guide),
            f"=== {heading} ===\n{chapter.text}\n=== END OF CHAPTER {chapter.position} ===",
            f"TASK: Give your developmental notes on chapter {chapter.position} above, and its digest.",
            _CHAPTER_CONTRACT,
        ]
    )
    return PromptParts(
        system=_SYSTEM,
        user=user,
        max_tokens=int(snapshot[_CHAPTER_MAX_TOKENS]),
        input_budget=int(snapshot[_CHAPTER_INPUT_BUDGET]),
    )


def _book_chapter_block(chapter: BookChapterInput, mode: ReadThroughBookInputMode) -> str:
    label = _one_line(chapter.label)
    if mode is ReadThroughBookInputMode.FULL_TEXT:
        if chapter.text is None:
            raise ValueError(f"chapter {chapter.position} has no text for a full_text book pass")
        heading = f"=== CHAPTER {chapter.position}: {label} ==="
        body = chapter.text
    else:
        if chapter.digest is None:
            raise ValueError(f"chapter {chapter.position} has no digest for a digests book pass")
        heading = f"=== CHAPTER {chapter.position}: {label} (SUMMARY, not the manuscript) ==="
        body = "Digest: " + json.dumps(chapter.digest, ensure_ascii=False)
    if chapter.note_titles:
        given = "Notes already given on this chapter (do not repeat):\n" + "\n".join(
            f"- {_one_line(title)}" for title in chapter.note_titles
        )
    else:
        given = "Notes already given on this chapter: none."
    return f"{heading}\n{body}\n\n{given}\n=== END OF CHAPTER {chapter.position} ==="


def build_book_prompt(
    snapshot: Mapping[str, Any],
    voice_guide: str | None,
    chapters: Sequence[BookChapterInput],
    mode: str,
) -> PromptParts:
    """The prompt for the cross-chapter book pass. ``mode`` is ``"full_text"`` or ``"digests"``."""
    try:
        input_mode = ReadThroughBookInputMode(mode)
    except ValueError:
        raise ValueError(f"unknown book input mode {mode!r}") from None
    if not chapters:
        raise ValueError("a book pass needs at least one chapter")

    if input_mode is ReadThroughBookInputMode.FULL_TEXT:
        notice = _FULL_TEXT_NOTICE
        task = "TASK: Give your cross-chapter notes on the chapters above."
    else:
        notice = _DIGEST_NOTICE
        task = (
            "TASK: Give your cross-chapter notes on the chapters above. Remember you read SUMMARIES, not the "
            "manuscript: raise possible gaps only as something for the author to verify, with no quotes."
        )
    user = "\n\n".join(
        [
            _voice_guide_section(voice_guide),
            notice,
            *(_book_chapter_block(chapter, input_mode) for chapter in chapters),
            task,
            _BOOK_CONTRACT,
        ]
    )
    return PromptParts(
        system=_SYSTEM,
        user=user,
        max_tokens=int(snapshot[_BOOK_MAX_TOKENS]),
        input_budget=int(snapshot[_BOOK_INPUT_BUDGET]),
    )


def estimated_input_tokens(parts: PromptParts) -> int:
    """The estimated input ``llm.complete`` gates on ``input_budget``: the same section estimate with the
    output allowance taken back out (llm.py:619-627; no cached prefix blocks, no explicit sections)."""
    sections = llm.estimate_context_tokens(system=parts.system, user=parts.user, max_tokens=parts.max_tokens)
    return max(0, sum(sections.values()) - parts.max_tokens)
