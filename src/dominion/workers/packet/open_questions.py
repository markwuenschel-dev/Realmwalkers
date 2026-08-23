"""The open-questions clearance predicate and its canonical normalized shape (#277).

Chapter-packet approval used to clear whenever ``open_questions["items"]`` was empty, and never read
``resolved[]`` at all. Emptying the list was therefore sufficient to open the gate — whether or not any
question was actually ruled, and whether or not the ruling said anything.

THE AUTHORITY PREDICATE. An item is **cleared** if and only if a resolved entry carries all four:

    1. ``item_id`` exactly matching the item's server-minted ``item_id``
    2. non-empty ``resolution`` text
    3. non-empty ``source``
    4. a server-recorded timestamp

Anything else leaves the item **open**. Positional matching is prohibited entirely; text matching is
prohibited entirely. Both were how the old client bound a ruling to a question, and both silently rebind
when a list is reordered or a question is edited.

``source`` is AUDIT PROVENANCE, NOT AUTHENTICATED IDENTITY. This API is unauthenticated by standing
decision (accepted risk C10), so no authenticated-actor concept exists to record. The field supports
review and accountability without claiming an identity assurance the system does not have.

PURE VALUE FUNCTIONS ONLY. Nothing here touches the ORM, the session, or ``ChapterPacket``. The one
module permitted to turn this column into a gate decision is ``workers/packet/approval_policy.py``
(enforced by ``tests/test_issue223_fork3b_authorization_seam_guard.py``); it reads the row and delegates
the *shape* questions here. Keeping this module row-free is what lets it be shared by the normalizer, the
gate, and the write path without creating a second authority seam.

LEGACY ROWS ARE READABLE HISTORY, NEVER CLEARANCE AUTHORITY (D4/D5). A resolved entry with no
``item_id`` is preserved verbatim and can never clear anything, because nothing establishes WHICH question
it ruled — array position is not identity, and duplicate question text is expected (``items[]`` mixes
human, author, and ADR-0029 system-generated sources). Existing chapters may therefore become blocked.
That is intended: backfilling ids from current array position would make old resolutions look trustworthy
while binding them to possibly different questions.

RETIREMENT BOUNDARY. ``item_id`` is a TEMPORARY JSONB binding key. It carries no reuse, lineage, or
portability semantics, it is not an Adjudication observation key, and it is deleted with the column when
#292 retires it. No future reader may treat it as an Adjudication identity.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from dominion.workers.scene_packet.hash import canonical_json

__all__ = [
    "OpenQuestionsInvalid",
    "append_open_questions",
    "cleared_item_ids",
    "normalize",
    "state_token",
    "stored_ruling_times",
    "strip_client_timestamps",
    "unresolved_items",
]


class OpenQuestionsInvalid(ValueError):
    """An attempted ruling is malformed. Surfaced as a 422 — never coerced into something valid.

    D2 is explicit that the normalizer must not silently repair an attempted new ruling: a blank
    ``resolution`` quietly turned into a stored empty string is a clearance rationale that nobody wrote,
    and it would clear the gate exactly as well as a real one.
    """


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_ruling_attempt(entry: dict[str, Any]) -> bool:
    """Does this resolved entry claim to be a NEW, id-bound ruling?

    Presence of ``item_id`` is the discriminator, and it has to be: a legacy entry is ``{q, resolution,
    at}`` with no id, and rejecting those would 422 every edit of every pre-existing chapter. An entry
    that supplies an ``item_id`` is asserting the new contract and is held to all of it.
    """
    return "item_id" in entry


def _normalize_item(raw: Any, *, mint: bool) -> dict[str, Any] | None:
    """One entry of ``items[]`` in canonical ``{item_id, text}`` shape.

    A legacy item arrives as a bare string with no identity. On a WRITE it is minted an id, which is how a
    legacy question becomes rulable at all. On a READ it is left unbound and marked ``legacy`` — D4/D5
    forbids minting ephemerally on read, because an id that changes every time the row is rendered would
    bind a ruling to nothing.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        return {"item_id": str(uuid.uuid4()), "text": text} if mint else {"text": text, "legacy": True}
    if not isinstance(raw, dict):
        return None
    text = _text(raw.get("text")) or _text(raw.get("q"))
    if not text:
        return None
    item_id = _text(raw.get("item_id"))
    if item_id:
        return {"item_id": item_id, "text": text}
    if mint:
        # Clients may not supply an id for a new item; the server mints it. Delete-and-re-add of identical
        # text therefore produces a NEW id and requires a NEW ruling — re-raising a question is a fresh
        # authority event, not a resurrection of the old one.
        return {"item_id": str(uuid.uuid4()), "text": text}
    return {"text": text, "legacy": True}


def _ruling_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (_text(entry.get("item_id")), _text(entry.get("resolution")), _text(entry.get("source")))


def _normalize_resolved(
    raw: Any, *, now: str, previous: dict[tuple[str, str, str], str] | None
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if not _is_ruling_attempt(raw):
        return dict(raw)  # legacy history, preserved verbatim; can never clear (no binding)
    item_id = _text(raw.get("item_id"))
    resolution = _text(raw.get("resolution"))
    source = _text(raw.get("source"))
    if not item_id or not resolution or not source:
        raise OpenQuestionsInvalid(
            "a ruling must carry a non-empty item_id, resolution, and source; got "
            f"item_id={raw.get('item_id')!r} resolution={raw.get('resolution')!r} source={raw.get('source')!r}"
        )
    # THE SERVER RECORDS THE RULING TIME (D2). The write path strips any client-supplied `at` before
    # calling this, so an `at` still present here came from the stored row (re-normalizing an already
    # canonical value) and is preserved — which is what makes normalization idempotent, and therefore
    # what makes clause B's state token stable across an identical resubmission.
    at = _text(raw.get("at"))
    if not at and previous is not None:
        # An unchanged ruling keeps its original timestamp; only a genuinely new or edited one is
        # re-stamped. Silently re-dating an untouched clearance rationale would be history rewriting.
        at = previous.get((item_id, resolution, source), "")
    return {"item_id": item_id, "resolution": resolution, "source": source, "at": at or now}


def stored_ruling_times(normalized: dict[str, Any]) -> dict[tuple[str, str, str], str]:
    """`(item_id, resolution, source) -> at` for the rulings already on a row, so an unchanged ruling
    survives a rewrite with its original server timestamp intact."""
    out: dict[tuple[str, str, str], str] = {}
    for entry in normalized.get("resolved") or []:
        if isinstance(entry, dict) and _text(entry.get("at")):
            out[_ruling_key(entry)] = _text(entry.get("at"))
    return out


def strip_client_timestamps(value: Any) -> Any:
    """Drop any client-supplied `at` from ruling attempts. The server owns the ruling time; a client
    that could set it could backdate a clearance rationale."""
    if not isinstance(value, dict):
        return value
    resolved = value.get("resolved")
    if not isinstance(resolved, list):
        return value
    cleaned = [
        {k: v for k, v in entry.items() if k != "at"}
        if isinstance(entry, dict) and _is_ruling_attempt(entry)
        else entry
        for entry in resolved
    ]
    return {**value, "resolved": cleaned}


def normalize(value: Any, *, mint: bool, previous: dict[tuple[str, str, str], str] | None = None) -> dict[str, Any]:
    """The canonical ``{items, resolved}`` shape. THE choke point — every write path funnels here.

    ``mint=True`` on write paths (ids are minted, ruling times are server-stamped); ``mint=False`` on read
    projections, where an id must never be invented.

    Raises ``OpenQuestionsInvalid`` when an attempted ruling is malformed. A malformed ``items`` value —
    the D7 bypass, where ``{"items": "x"}`` made the old gate read ``[]`` and open approval — is likewise
    a hard error rather than something quietly coerced to an empty list.
    """
    if value is None:
        return {"items": [], "resolved": []}
    if isinstance(value, list):
        value = {"items": value, "resolved": []}
    if not isinstance(value, dict):
        raise OpenQuestionsInvalid(f"open_questions must be an object or a list, got {type(value).__name__}")

    raw_items = value.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        # Fail closed and LOUDLY. The old reader returned [] for anything that was not a list, so a single
        # PUT of {"items": "x"} opened chapter approval while the body mirror still held the real state.
        raise OpenQuestionsInvalid(f"open_questions.items must be a list, got {type(raw_items).__name__}")
    raw_resolved = value.get("resolved")
    if raw_resolved is None:
        raw_resolved = []
    if not isinstance(raw_resolved, list):
        raise OpenQuestionsInvalid(f"open_questions.resolved must be a list, got {type(raw_resolved).__name__}")

    now = datetime.now(UTC).isoformat()
    items = [item for item in (_normalize_item(raw, mint=mint) for raw in raw_items) if item is not None]
    resolved = [
        entry
        for entry in (_normalize_resolved(raw, now=now, previous=previous) for raw in raw_resolved)
        if entry is not None
    ]
    return {"items": items, "resolved": resolved}


def cleared_item_ids(normalized: dict[str, Any]) -> set[str]:
    """Item ids carrying a resolved entry that satisfies all four parts of the predicate."""
    cleared: set[str] = set()
    for entry in normalized.get("resolved") or []:
        if not isinstance(entry, dict):
            continue
        item_id = _text(entry.get("item_id"))
        if item_id and _text(entry.get("resolution")) and _text(entry.get("source")) and _text(entry.get("at")):
            cleared.add(item_id)
    return cleared


def unresolved_items(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """Items that still block approval — the gate's actual input.

    An item with no ``item_id`` (a legacy one, read without minting) is ALWAYS unresolved: nothing can
    bind a ruling to it, so it fails closed until a human re-rules it through a write path.
    """
    cleared = cleared_item_ids(normalized)
    open_items: list[dict[str, Any]] = []
    for item in normalized.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = _text(item.get("item_id"))
        if not item_id or item_id not in cleared:
            open_items.append(item)
    return open_items


def state_token(normalized: dict[str, Any]) -> str:
    """The expected-state token for optimistic concurrency (clause B) — sha256 of the canonical value.

    Canonicalization is part of the contract, not an implementation detail: two callers that disagree
    about how to serialize produce tokens that compare unequal for identical state. So the digest is
    defined HERE and nowhere else, and it is taken over the NORMALIZED projection rather than raw column
    bytes, so a legacy row that has never been rewritten still yields a stable, comparable token.

    Dict keys are sorted; ARRAY ORDER IS PRESERVED. ``items[]`` order is the human's reading order, so
    sorting it would make a reorder invisible to the token — deliberately unlike
    ``chapter_source_fingerprint``, whose input is a set rather than a sequence.

    Computed on read, never stored: a persisted digest can drift from the JSONB it describes the moment
    one writer updates either without the other, which is the exact divergence class this ticket exists
    to eliminate. It is write coherence, not authority — it guarantees the predicate is evaluated against
    the state the mutation actually changes; it does not authenticate the caller.
    """
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def append_open_questions(open_questions: dict, questions) -> dict:
    """Append new question TEXTS to a normalized open-questions value, minting an id for each (#277).

    De-duplicated by text, append-only, order-preserving — the previous behaviour, except that each new
    item is born with the server-minted `item_id` a ruling has to bind to. Appending a bare string here
    (which is what this code used to do) would create a question no human could ever clear, because the
    clearance predicate matches on id and nothing else.
    """
    items = list(open_questions.get("items") or [])
    seen = {str(i.get("text") or "").strip() for i in items if isinstance(i, dict)}
    seen |= {str(i).strip() for i in items if not isinstance(i, dict)}
    for question in questions:
        text = str(question or "").strip()
        if text and text not in seen:
            items.append({"item_id": str(uuid.uuid4()), "text": text})
            seen.add(text)
    return {**open_questions, "items": items}
