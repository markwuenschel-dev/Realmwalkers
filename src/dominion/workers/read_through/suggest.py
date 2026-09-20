"""Draft prose that answers one read-through note — the author asking "show me what you mean".

The read-through itself refuses to write prose, deliberately and at the top of its system prompt
(`prompts.py:99`, "RECOMMEND, NEVER REWRITE"). That refusal is what makes its notes trustworthy: a
note that arrives with replacement text attached is arguing for its own fix, and an author reading it
is judging the prose instead of the diagnosis. This module does not weaken that rule — it is a
SEPARATE, author-invoked call that happens only when the author has already read the note and asked,
per note, for a demonstration. The note is the brief; this writes to it.

Shaped after `reviewers/style_audit.py`, which solved the same structural problem: a `SceneContext`
asserts a book, chapter, packet and beat that a pasted passage does not have, so the audit takes a
session and a string and reads its standards itself. A read-through snapshot is in exactly that
position — `read_through_chapters` has no foreign key to `chapters` (`models.py:1394`), so there is no
Job, no ScenePacket, no POV, no beat. `assemble_context` would raise. What IS available:

  * the note — title, observation and recommendation: a better brief than any prompt template
  * the anchor — verbatim quotes the server already located in the snapshot, so the passage is known
  * `read_throughs.voice_guide_snapshot` — the author's voice, frozen at admission
  * `style_documents` — prose_contract and prose_clarity_rules, the same rows the audit judges against
  * `forbidden_drift`, scoped in AUDIT mode by the passage's OWN cast (`cast_present_in`)
  * canon, via the two calls the drafter makes (`context/draft_memory.py:37-38`)

That last one is the reason this is worth building rather than pasting the note into a chat window:
the suggestion is written against 2,400-odd embedded canon rows for this book, so it can name what is
actually true of the world instead of inventing something plausible.

WHAT THIS DELIBERATELY DOES NOT DO: it writes nothing. No row, no column, no edit to the snapshot —
which is immutable by design and unlinked to the live chapter anyway, so there is no "apply" verb for
it to reach for. The author copies what they want. `quote_is_supported` still guards the anchor, for
the same reason it guards the audit: a suggestion pinned to text that is not in the chapter is
unfalsifiable, and the author would have to go looking to discover that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import quality_effort, quality_temperature
from dominion.shared.config import settings
from dominion.workers.budget import TokenBudget
from dominion.workers.context.forbidden_drift import AUDIT, cast_present_in, scope_forbidden_drift
from dominion.workers.context.style_source import load_style_document
from dominion.workers.llm_escalation import complete_with_rate_limit_fallback
from dominion.workers.memory import owner_router, retrieval
from dominion.workers.reviewers.base import parse_json_objects, quote_is_supported

__all__ = ["ProseSuggestion", "ProseSuggestionResult", "suggest_prose"]

# Prose runs longer than a JSON finding, and two variants have to fit in one response.
_SUGGEST_MAX_TOKENS = 2600

# How much of the chapter travels with the anchor. Enough that the model can hear the rhythm either
# side of the span and match it; short enough that the standards still fit in the budget.
_WINDOW_CHARS = 1500

# At most two variants. One gives the author nothing to choose between; five turns a decision into a
# reading task, and each one is paid for.
_MAX_VARIANTS = 2

_CANON_K = 6

_MODES = ("replace", "insert_before", "insert_after")

# Same two documents the audit judges finished prose against. `voice_guide` is NOT loaded from here:
# the read-through froze its own copy at admission (`read_throughs.voice_guide_snapshot`), and using
# the live row instead would let a note written against one voice guide be answered against another.
_STANDARDS: tuple[tuple[str, str], ...] = (
    ("prose_contract", "prose_contract_path"),
    ("prose_clarity_rules", "prose_clarity_rules_path"),
)

_SYSTEM = (
    "You write prose in another author's voice, to answer one editorial note they have already read "
    "and chosen to act on. You are a hand, not a critic: the note's judgement is settled and is not "
    "yours to re-argue, soften, or widen.\n\n"
    "Rules that outrank everything else:\n"
    "1. THE VOICE IS THE AUTHOR'S, NOT YOURS. Match the diction, sentence rhythm and temperature of "
    "the surrounding passage. If your instinct is that a line would be better written differently, "
    "that instinct is wrong here — write what they would have written.\n"
    "2. ANSWER ONLY THE NOTE. Change nothing the note did not ask about. Fixing something else you "
    "noticed is a failure, however right you are.\n"
    "3. CANON IS TRUE AND CANON IS ALL YOU KNOW. Use the canon supplied below for any fact about the "
    "world. Never invent a name, rule, place, history or capability that is not in the canon or "
    "already in the passage. If the note asks you to explain something the canon does not settle, "
    "write around it — imply the mechanism without fixing it — and say so in `why`.\n"
    "4. ANCHOR VERBATIM. `anchor_quote` must be copied character for character from the passage.\n"
    "5. Return ONLY one JSON array. No prose outside it, no code fences."
)


def _response_contract(max_variants: int) -> str:
    return (
        "\nReturn ONLY a JSON array of at most "
        f"{max_variants} items, no prose and no code fences. Each item:\n"
        '{"mode": "replace"|"insert_before"|"insert_after", "anchor_quote": str, "prose": str, '
        '"why": str}\n\n'
        "`mode` says what to do with `prose` relative to `anchor_quote`: `replace` swaps the quoted "
        "span for it, `insert_before` / `insert_after` leave the quote alone and place new text "
        "beside it. A note asking for something the reader is missing usually wants an insert; a "
        "note about how something is written usually wants a replace. Choose the smaller change.\n"
        "`anchor_quote` is the exact text from the passage, copied character for character.\n"
        "`prose` is finished prose in the author's voice — not notes, not a summary, not a plan, and "
        "never a description of what you would write.\n"
        "`why` is ONE sentence saying how this answers the note, and naming anything the canon did "
        "not settle that you wrote around.\n\n"
        "If you give two items, they must be genuinely different approaches to the same note — not "
        "one passage lightly reworded. One good answer is better than two thin ones."
    )


@dataclass(frozen=True)
class ProseSuggestion:
    """One piece of suggested prose, pinned to a span of the chapter snapshot."""

    mode: str
    anchor_quote: str
    prose: str
    why: str


@dataclass(frozen=True)
class ProseSuggestionResult:
    suggestions: list[ProseSuggestion] = field(default_factory=list)
    standards_loaded: list[str] = field(default_factory=list)
    standards_missing: list[str] = field(default_factory=list)
    # Provenance for the canon that reached the prompt: what the suggestion was allowed to know.
    # Shown to the author because "which facts did it have" is the question that decides whether a
    # wrong detail is the model inventing or the canon being thin.
    canon_sources: list[str] = field(default_factory=list)
    drift_scope_characters: list[str] = field(default_factory=list)
    # Variants whose anchor was not in the chapter, dropped by the deterministic evidence check.
    fabricated_dropped: int = 0
    model: str = ""
    tokens_used: int = 0


def _window(chapter_text: str, anchor_quote: str) -> str:
    """The anchor plus enough chapter either side to hear the voice.

    Falls back to the head of the chapter when the quote is not found. That is not expected — anchors
    are located server-side against this exact text and stored with their offsets — but a note can
    carry an `unlocated` anchor from the book pass, and a silent empty passage would produce prose
    written against nothing at all.
    """
    if not chapter_text:
        return ""
    idx = chapter_text.find(anchor_quote) if anchor_quote else -1
    if idx < 0:
        return chapter_text[: _WINDOW_CHARS * 2]
    start = max(0, idx - _WINDOW_CHARS)
    end = min(len(chapter_text), idx + len(anchor_quote) + _WINDOW_CHARS)
    return chapter_text[start:end]


def _variant(item: dict[str, Any]) -> ProseSuggestion | None:
    mode = str(item.get("mode") or "").strip().lower()
    if mode not in _MODES:
        mode = "replace"
    anchor = str(item.get("anchor_quote") or "").strip()
    prose = str(item.get("prose") or "").strip()
    why = str(item.get("why") or "").strip()
    if not anchor or not prose:
        return None
    return ProseSuggestion(mode=mode, anchor_quote=anchor, prose=prose, why=why)


async def _canon_for(
    session: AsyncSession, *, book_id: uuid.UUID, query: str, characters: list[str]
) -> tuple[list[str], list[str]]:
    """(bodies, provenance labels) — the same two calls the drafter makes, owner-forced hits first."""
    routing = owner_router.route(query, characters=characters)
    snippets = await retrieval.retrieve_hybrid(
        session,
        book_id=book_id,
        query=query,
        owner_topics=routing.owner_topics,
        required_doc_paths=routing.doc_paths,
        k=_CANON_K,
    )
    owner_first = [s for s in snippets if s["retrieval_reason"] == "owner_forced"]
    rest = [s for s in snippets if s["retrieval_reason"] != "owner_forced"]
    ordered = [s for s in [*owner_first, *rest] if s.get("body")]
    bodies = [s["body"] for s in ordered]
    labels = [str(s.get("heading_path") or s.get("doc_path") or "canon") for s in ordered]
    return bodies, labels


async def suggest_prose(
    session: AsyncSession,
    *,
    book_id: uuid.UUID,
    chapter_text: str,
    anchor_quote: str,
    note_title: str,
    observation: str,
    recommendation: str,
    voice_guide: str = "",
    budget: TokenBudget | None = None,
) -> ProseSuggestionResult:
    """Write prose answering one note. One model call; writes nothing to the database."""
    model = settings.prose_suggestion_model
    passage = _window(chapter_text, anchor_quote)
    if not passage.strip():
        return ProseSuggestionResult(model=model)

    loaded: list[str] = []
    missing: list[str] = []
    sections: list[str] = []

    # The voice guide leads: it is the document that says what the prose should sound like, and it is
    # the author's own snapshot rather than whatever the live row says today.
    if voice_guide.strip():
        loaded.append("voice_guide")
        sections.append(f"=== voice_guide ===\n{voice_guide}")
    else:
        missing.append("voice_guide")

    for name, attr in _STANDARDS:
        content = await load_style_document(session, getattr(settings, attr))
        if content:
            loaded.append(name)
            sections.append(f"=== {name} ===\n{content}")
        else:
            missing.append(name)

    present = cast_present_in(passage)
    drift_scope_characters: list[str] = []
    drift_raw = await load_style_document(session, settings.forbidden_drift_path)
    if drift_raw:
        # AUDIT mode carries each pattern's warning signs AND its correction, which is what a writer
        # needs; DRAFT mode carries only names and corrections.
        scoped = scope_forbidden_drift(drift_raw, pov="", present=present, signals=passage, mode=AUDIT)
        if scoped:
            loaded.append("forbidden_drift")
            sections.append(f"=== forbidden_drift ===\n{scoped}")
            drift_scope_characters = sorted(present)
        else:
            missing.append("forbidden_drift")
    else:
        missing.append("forbidden_drift")

    canon_query = " ".join(p for p in [note_title, recommendation, anchor_quote] if p)
    canon_bodies, canon_sources = await _canon_for(
        session, book_id=book_id, query=canon_query, characters=sorted(present)
    )

    parts: list[str] = []
    if sections:
        parts.append("THE AUTHOR'S STANDARDS — write inside these:\n\n" + "\n\n".join(sections))
    if canon_bodies:
        parts.append("CANON (treat as true; do not invent beyond it):\n" + "\n".join(f"- {b}" for b in canon_bodies))
    parts.append(
        "THE NOTE YOU ARE ANSWERING:\n"
        f"Title: {note_title}\n"
        f"What the reader experiences: {observation}\n"
        f"What to change: {recommendation}"
    )
    parts.append(
        "THE PASSAGE (the note's anchor appears inside it):\n"
        f"--- anchor ---\n{anchor_quote}\n--- passage ---\n{passage}"
    )
    user = "\n\n".join(parts) + "\n" + _response_contract(_MAX_VARIANTS)

    budget = budget or TokenBudget(max_tokens=settings.prose_suggestion_token_budget)
    raw, _usage = await complete_with_rate_limit_fallback(
        setting_key="prose_suggestion_model",
        model=model,
        system=_SYSTEM,
        user=user,
        max_tokens=_SUGGEST_MAX_TOKENS,
        budget=budget,
        temperature=quality_temperature("prose_suggestion_model"),
        effort=quality_effort("prose_suggestion_model"),
    )

    suggestions: list[ProseSuggestion] = []
    dropped = 0
    for item in parse_json_objects(raw):
        variant = _variant(item)
        if variant is None:
            continue
        # The anchor must be real text from the chapter, checked against the WHOLE chapter rather than
        # the window: a model that quotes accurately but reaches slightly outside the window is right,
        # and dropping it would be the check punishing correct work.
        if not quote_is_supported(variant.anchor_quote, chapter_text):
            dropped += 1
            continue
        suggestions.append(variant)
        if len(suggestions) >= _MAX_VARIANTS:
            break

    return ProseSuggestionResult(
        suggestions=suggestions,
        standards_loaded=loaded,
        standards_missing=missing,
        canon_sources=canon_sources,
        drift_scope_characters=drift_scope_characters,
        fabricated_dropped=dropped,
        model=model,
        tokens_used=budget.used,
    )
