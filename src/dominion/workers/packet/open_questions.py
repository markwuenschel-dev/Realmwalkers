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

import copy
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from dominion.workers.scene_packet.hash import canonical_json

__all__ = [
    "OpenQuestionsItemMembershipMismatch",
    "OpenQuestionsInvalid",
    "OpenQuestionsLegacyServerOwned",
    "apply_client_update",
    "append_open_questions",
    "cleared_item_ids",
    "normalize",
    "prepare_legacy_items",
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


class OpenQuestionsLegacyServerOwned(OpenQuestionsInvalid):
    """A client tried to write a no-id legacy item or historical resolution.

    Legacy values have no safe client-visible identity. They are therefore readable but server-owned until
    the explicit Prepare transition mints stable ids while holding the packet row lock.
    """


class OpenQuestionsItemMembershipMismatch(OpenQuestionsInvalid):
    """A client omitted, duplicated, edited, or invented an item identity.

    Ordinary writes may record or remove rulings only. They cannot add, remove, rename, or reorder the
    authority-bearing question inventory.
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


def _mint_item_id(used_item_ids: set[str]) -> str:
    """Return a fresh server identity that does not collide with this normalized list."""
    while True:
        item_id = str(uuid.uuid4())
        if item_id not in used_item_ids:
            return item_id


def _normalize_item(
    raw: Any,
    *,
    mint: bool,
    prior_item_text_by_id: dict[str, str] | None,
    used_item_ids: set[str],
) -> dict[str, Any] | None:
    """One entry of ``items[]`` in canonical ``{item_id, text}`` shape.

    A legacy item arrives as a bare string with no identity. Trusted packet construction and the explicit
    Prepare transition may mint an id; ordinary client writes never do. On a READ it is left unbound and
    marked ``legacy`` — D4/D5 forbids minting ephemerally on read, because an id that changes every time
    the row is rendered would bind a ruling to nothing.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if not mint:
            return {"text": text, "legacy": True}
        item_id = _mint_item_id(used_item_ids)
        used_item_ids.add(item_id)
        return {"item_id": item_id, "text": text}
    if not isinstance(raw, dict):
        return None
    text = _text(raw.get("text")) or _text(raw.get("q"))
    if not text:
        return None
    item_id = _text(raw.get("item_id"))
    if not mint:
        return {"item_id": item_id, "text": text} if item_id else {"text": text, "legacy": True}

    # An untrusted API write may retain only an identity already stored for the exact same question. A
    # client-chosen UUID for a new question is not an identity: accepting it would let that request submit
    # both a question and its matching ruling, defeating the server-minted binding contract. Trusted
    # internal writers leave ``prior_item_text_by_id`` as None because they construct canonical packets,
    # not client authority input.
    if (
        item_id
        and (prior_item_text_by_id is None or prior_item_text_by_id.get(item_id) == text)
        and item_id not in used_item_ids
    ):
        used_item_ids.add(item_id)
        return {"item_id": item_id, "text": text}

    # Delete-and-re-add of identical text, text edits, duplicate client ids, and client-supplied ids for
    # new questions all become a NEW authority event. An old ruling therefore cannot silently clear them.
    if mint:
        minted_item_id = _mint_item_id(used_item_ids)
        used_item_ids.add(minted_item_id)
        return {"item_id": minted_item_id, "text": text}
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
    # THE SERVER RECORDS THE RULING TIME (D2). `apply_client_update` strips every client-supplied `at`
    # before calling this, so an `at` still present here came from the stored row (re-normalizing an already
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


def normalize(
    value: Any,
    *,
    mint: bool,
    previous: dict[tuple[str, str, str], str] | None = None,
    prior_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The canonical ``{items, resolved}`` shape for trusted construction and read projection.

    ``mint=True`` mints ids and stamps rulings for trusted construction or Prepare. ``mint=False`` projects
    reads, where an id must never be invented. Untrusted updates use ``apply_client_update`` instead; its
    exact id-set containment forbids text/position matching and client-side inventory changes.

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

    prior_item_text_by_id: dict[str, str] | None = None
    if prior_items is not None:
        prior_item_text_by_id = {}
        for prior in prior_items:
            prior_item_id = _text(prior.get("item_id"))
            prior_text = _text(prior.get("text"))
            if prior_item_id and prior_text:
                prior_item_text_by_id.setdefault(prior_item_id, prior_text)

    now = datetime.now(UTC).isoformat()
    used_item_ids: set[str] = set()
    items = [
        item
        for item in (
            _normalize_item(
                raw,
                mint=mint,
                prior_item_text_by_id=prior_item_text_by_id,
                used_item_ids=used_item_ids,
            )
            for raw in raw_items
        )
        if item is not None
    ]
    resolved = [
        entry
        for entry in (_normalize_resolved(raw, now=now, previous=previous) for raw in raw_resolved)
        if entry is not None
    ]
    return {"items": items, "resolved": resolved}


def apply_client_update(stored_value: Any, submitted_value: Any) -> dict[str, Any]:
    """Apply an ordinary Desk ruling update without granting the client item authority.

    The returned value preserves stored item ordering and every unbound legacy value. A client supplies
    exactly the existing id-bound items and any current id-bound rulings; it cannot introduce a question,
    omit an existing one, alter question text, or manufacture legacy history. This deliberately makes the
    module the one authority choke point for every untrusted packet update.
    """
    stored = normalize(stored_value, mint=False)
    if not isinstance(submitted_value, dict):
        raise OpenQuestionsInvalid("open_questions must be an object")

    raw_items = submitted_value.get("items")
    raw_resolved = submitted_value.get("resolved")
    if not isinstance(raw_items, list):
        raise OpenQuestionsInvalid("open_questions.items must be a list")
    if raw_resolved is None:
        raw_resolved = []
    if not isinstance(raw_resolved, list):
        raise OpenQuestionsInvalid("open_questions.resolved must be a list")

    stored_items_by_id: dict[str, dict[str, Any]] = {}
    for stored_item in stored["items"]:
        item_id = _text(stored_item.get("item_id"))
        if item_id:
            stored_items_by_id[item_id] = stored_item

    submitted_item_ids: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise OpenQuestionsLegacyServerOwned("clients cannot submit unbound legacy question items")
        item_id = _text(raw_item.get("item_id"))
        if not item_id:
            raise OpenQuestionsLegacyServerOwned("clients cannot submit unbound legacy question items")
        text = _text(raw_item.get("text")) or _text(raw_item.get("q"))
        stored_item = stored_items_by_id.get(item_id)
        if stored_item is None or text != _text(stored_item.get("text")):
            raise OpenQuestionsItemMembershipMismatch("each submitted question must be an exact existing id-bound item")
        if item_id in submitted_item_ids:
            raise OpenQuestionsItemMembershipMismatch("each id-bound item may appear only once")
        submitted_item_ids.add(item_id)

    if submitted_item_ids != set(stored_items_by_id):
        raise OpenQuestionsItemMembershipMismatch("an ordinary update must retain every existing id-bound question")

    previous = stored_ruling_times(stored)
    now = datetime.now(UTC).isoformat()
    bound_resolved: list[dict[str, Any]] = []
    resolved_item_ids: set[str] = set()
    for raw_entry in raw_resolved:
        if not isinstance(raw_entry, dict) or not _is_ruling_attempt(raw_entry):
            raise OpenQuestionsLegacyServerOwned("clients cannot submit unbound historical rulings")
        item_id = _text(raw_entry.get("item_id"))
        if item_id not in stored_items_by_id:
            raise OpenQuestionsItemMembershipMismatch("a ruling must name an existing id-bound question")
        if item_id in resolved_item_ids:
            raise OpenQuestionsItemMembershipMismatch("a question may have at most one current ruling")
        resolved_item_ids.add(item_id)
        normalized_entry = _normalize_resolved(
            {key: value for key, value in raw_entry.items() if key != "at"},
            now=now,
            previous=previous,
        )
        if normalized_entry is None:  # pragma: no cover - guarded by the dict/item_id checks above
            raise OpenQuestionsInvalid("a ruling entry is required")
        bound_resolved.append(normalized_entry)

    legacy_resolved = [
        copy.deepcopy(entry)
        for entry in stored["resolved"]
        if isinstance(entry, dict) and not _is_ruling_attempt(entry)
    ]
    # Preserve the stored raw item representation and its human reading order. In particular, a bare
    # legacy string must not be silently rewritten to an object merely because somebody ruled on another
    # id-bound question in the same packet.
    raw_stored_items = stored_value.get("items") if isinstance(stored_value, dict) else None
    if not isinstance(raw_stored_items, list):  # normalize() above already rejects malformed non-lists.
        raise OpenQuestionsInvalid("stored open_questions.items must be a list")
    return {"items": copy.deepcopy(raw_stored_items), "resolved": [*legacy_resolved, *bound_resolved]}


def prepare_legacy_items(stored_value: Any) -> dict[str, Any]:
    """Mint ids for every stored legacy item in one server-owned, row-locked transition.

    This function receives no client item or ruling payload. It is idempotent once all items are bound and
    preserves legacy resolutions as readable non-authoritative history, so approval remains blocked until
    the author records fresh id-bound rulings.
    """
    stored = normalize(stored_value, mint=False)
    if not any(not _text(item.get("item_id")) for item in stored["items"]):
        return copy.deepcopy(stored_value) if isinstance(stored_value, dict) else stored
    return normalize(stored_value, mint=True, previous=stored_ruling_times(stored))


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
    bind a ruling to it, so it fails closed until Prepare mints a durable id and a human records a fresh
    id-bound ruling.
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
