"""Author-time manuscript-vs-canon conflict detection (ADR 0028 Slice 3b / ADR 0029).

Q5: at ChapterPacket author time, a manuscript-evidence claim flagged as a *candidate* canon conflict is
re-anchored against LIVE retrieved locked canon before it is trusted. Candidates are taken as unconfirmed
hints from the extractor's `canon_conflicts` ledger section, then re-anchored against live canon before
they are trusted — those hints are prose-only, with no canon fingerprint, which is exactly why the
re-anchor is mandatory, not optional. A candidate becomes a real, human-adjudicable open question
ONLY when it re-anchors to BOTH sides:

  - the M# side: a valid immutable anchor into the imported-prose snapshot (a `prose_hash` plus a
    [start, end) character span within the snapshot);
  - the C# side: a CURRENT locked-canon row surfaced right now by
    `workers/memory/canon_rag.retrieve_with_meta` (verified signature:
    `retrieve_with_meta(session, *, book_id, query, k=6) -> list[{"id", "name", "body"}]`).

Anything that cannot be re-anchored to a current C#+M# span FAILS CLOSED: it is reported as a
`FailClosedConflict` signal (never silently dropped), and — like a re-anchored conflict — it blocks
ChapterPacket approval. Manuscript evidence never overrides locked canon here and is never promoted to
canon; a re-anchored conflict is only ENCODED (shared/manuscript_conflict.format_conflict) for a human
to adjudicate. Resolution is HUMAN-ONLY; this module makes no adjudication.

This is reusable detection for the propose lane (A2) to call; it does NOT wire propose. The propose lane
binds a retriever with `session_retriever(session, book_id)`, passes the same C# handle map it hands the
author, appends `ConflictDetectionResult.open_questions()` to `open_questions.items[]`, and treats
`fail_closed` as an approval block.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from dominion.shared.manuscript_conflict import ManuscriptCanonConflict, format_conflict

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: One row from `canon_rag.retrieve_with_meta`: {"id", "name", "body"}.
CanonHit = Mapping[str, Any]
#: The retrieval seam: given a query, return the live top-k locked-canon rows. Bound to
#: `canon_rag.retrieve_with_meta` in production (see `session_retriever`); faked in unit tests.
CanonRetriever = Callable[[str], Awaitable[Sequence[CanonHit]]]

#: Longest verbatim slice of either assertion carried into the encoded open question. Canon bodies run
#: ~1000 chars; the full assertion stays auditable in canon/evidence, so the question keeps a snippet.
_DEFAULT_SNIPPET_CHARS = 240


class FailClosedReason(StrEnum):
    """Why a flagged conflict could not be re-anchored to a current C#+M# span."""

    #: The manuscript side lacks a valid immutable anchor (no `prose_hash`, or a missing/out-of-range span).
    UNANCHORED_MANUSCRIPT_SPAN = "unanchored_manuscript_span"
    #: Live retrieval surfaces no current locked canon the assertion re-anchors to (empty, or the named
    #: `canon_id` is no longer retrievable/locked).
    NO_CURRENT_CANON = "no_current_canon"


@dataclass(frozen=True)
class ManuscriptClaim:
    """A candidate manuscript-vs-canon conflict awaiting re-anchoring.

    The manuscript side carries its M# provenance (`handle` → immutable snapshot identity + span). The
    canon side is intentionally UN-fingerprinted by default (`canon_id=None`): detection re-anchors it by
    live retrieval. A caller that already holds a canon fingerprint may set `canon_id` to REQUIRE that
    exact row still be retrievable (else fail closed).
    """

    handle: str  # M# label, e.g. "M1"
    scene_id: str
    scene_version: int
    prose_hash: str
    span: tuple[int, int] | None
    assertion: str  # the manuscript-side claim text (a DERIVED_FROM_MANUSCRIPT assertion)
    query: str | None = None  # retrieval query; defaults to `assertion`
    snapshot_prose_len: int | None = None  # snapshot length, to bound-check the span when known
    canon_id: str | None = None  # optional fingerprint the live retrieval must still surface


@dataclass(frozen=True)
class ReanchoredConflict:
    """A candidate that re-anchored on both sides: the structured conflict plus its encoded question."""

    conflict: ManuscriptCanonConflict
    question: str  # == format_conflict(conflict); ready to append to open_questions.items[]


@dataclass(frozen=True)
class FailClosedConflict:
    """A candidate that could NOT be re-anchored. A signal, never an encoded question — the propose lane
    must block/hold the packet rather than proceed as if there were no conflict."""

    manuscript_handle: str
    scene_id: str
    reason: FailClosedReason
    detail: str


@dataclass(frozen=True)
class ConflictDetectionResult:
    """The outcome of re-anchoring a batch of candidate conflicts."""

    reanchored: tuple[ReanchoredConflict, ...]
    fail_closed: tuple[FailClosedConflict, ...]

    @property
    def blocks_approval(self) -> bool:
        """Both a re-anchored (human-adjudicable) conflict and a fail-closed signal block approval."""
        return bool(self.reanchored or self.fail_closed)

    def open_questions(self) -> list[str]:
        """The encoded open-question strings for the re-anchored conflicts (append to items[])."""
        return [rc.question for rc in self.reanchored]


def _anchored_span(claim: ManuscriptClaim) -> tuple[int, int] | None:
    """The claim's span if it is a valid immutable anchor into the snapshot, else None."""
    span = claim.span
    if not (isinstance(span, tuple) and len(span) == 2):
        return None
    start, end = span
    if not (isinstance(start, int) and isinstance(end, int)):
        return None
    if not (0 <= start <= end):
        return None
    if claim.snapshot_prose_len is not None and end > claim.snapshot_prose_len:
        return None
    return (start, end)


def _bind_canon(hits: Sequence[CanonHit], canon_id: str | None) -> tuple[int, CanonHit] | None:
    """Bind the C# side to a current locked-canon row, or None if it can't re-anchor.

    With a `canon_id`, the SAME row must still be retrievable (else the canon it named is gone/unlocked).
    Without one, the top usable hit (retrieval is cosine-ranked) is bound. A usable hit has an id and a
    non-empty body — an empty-body row cannot carry a conflicting assertion.
    """
    usable = [
        (i, h)
        for i, h in enumerate(hits)
        if isinstance(h, Mapping) and h.get("id") is not None and str(h.get("body") or "").strip()
    ]
    if canon_id is not None:
        for i, h in usable:
            if str(h.get("id")) == str(canon_id):
                return (i, h)
        return None
    return usable[0] if usable else None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _snippet(value: Any, limit: int) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def detect_manuscript_canon_conflicts(
    claims: Sequence[ManuscriptClaim],
    *,
    retrieve: CanonRetriever,
    canon_handle_by_id: Mapping[str, str] | None = None,
    snippet_chars: int = _DEFAULT_SNIPPET_CHARS,
) -> ConflictDetectionResult:
    """Re-anchor each candidate manuscript-vs-canon conflict against live locked canon.

    For every claim: re-anchor the M# side (valid `prose_hash` + span), then the C# side (live retrieval
    binds a current locked-canon row). Re-anchored on both → an encoded, human-adjudicable open question.
    Either side unanchored → a fail-closed signal. Both outcomes block ChapterPacket approval.

    `canon_handle_by_id` maps a bound canon id to the C# label the author was shown (keeps the question's
    handle consistent with the packet); a bound row absent from the map falls back to its retrieval rank.
    """
    handle_map = dict(canon_handle_by_id or {})
    reanchored: list[ReanchoredConflict] = []
    fail_closed: list[FailClosedConflict] = []

    for claim in claims:
        span = _anchored_span(claim)
        if not claim.prose_hash or not claim.prose_hash.strip() or span is None:
            fail_closed.append(
                FailClosedConflict(
                    manuscript_handle=claim.handle,
                    scene_id=claim.scene_id,
                    reason=FailClosedReason.UNANCHORED_MANUSCRIPT_SPAN,
                    detail="manuscript claim lacks a valid (prose_hash, span) anchor into the snapshot",
                )
            )
            continue

        hits = list(await retrieve(claim.query or claim.assertion))
        bound = _bind_canon(hits, claim.canon_id)
        if bound is None:
            fail_closed.append(
                FailClosedConflict(
                    manuscript_handle=claim.handle,
                    scene_id=claim.scene_id,
                    reason=FailClosedReason.NO_CURRENT_CANON,
                    detail=(
                        "no current locked canon re-anchors this conflict "
                        f"(canon_id={claim.canon_id!r}, live_hits={len(hits)})"
                    ),
                )
            )
            continue

        index, hit = bound
        canon_id = str(hit.get("id"))
        conflict = ManuscriptCanonConflict(
            canon_handle=handle_map.get(canon_id) or f"C{index + 1}",
            canon_id=canon_id,
            canon_name=_str_or_none(hit.get("name")),
            manuscript_handle=claim.handle,
            scene_id=claim.scene_id,
            scene_version=claim.scene_version,
            prose_hash=claim.prose_hash,
            span=span,
            canon_claim=_snippet(hit.get("body"), snippet_chars),
            manuscript_claim=_snippet(claim.assertion, snippet_chars),
        )
        reanchored.append(ReanchoredConflict(conflict=conflict, question=format_conflict(conflict)))

    return ConflictDetectionResult(reanchored=tuple(reanchored), fail_closed=tuple(fail_closed))


def session_retriever(session: AsyncSession, book_id: uuid.UUID, *, k: int = 6) -> CanonRetriever:
    """A `CanonRetriever` bound to a DB session — the production seam over `canon_rag.retrieve_with_meta`.

    `canon_rag` is imported lazily so this module (and `detect_manuscript_canon_conflicts`) stays
    importable and unit-testable without the embedding/DB stack.
    """
    from dominion.workers.memory import canon_rag

    async def _retrieve(query: str) -> Sequence[CanonHit]:
        return await canon_rag.retrieve_with_meta(session, book_id=book_id, query=query, k=k)

    return _retrieve
