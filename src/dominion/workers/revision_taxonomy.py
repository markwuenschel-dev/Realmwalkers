"""Pure decision logic for the revise/redraft response taxonomy (ADR 0028).

The single `_accept_revision_request_locked(...)` seam computes DB facts, then calls
`classify_revision(...)` here to decide the outcome. Keeping the decision pure makes the
200/202/404/422/409 mapping directly unit-testable and guarantees reviews, continuity resolution, and
ScenePacket-approval resume can never drift in how they map state to a response.

Note the 200/202 split is now decided one level up: ADR-0032 D11 makes it a function of BOTH the
request disposition and whether adoption entry moved anything forward, so `accept_revision_intent`
owns it. The outcomes here still decide accept-vs-refuse and the 4xx codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dominion.shared.enums import RevisionRequestStatus

# The active states the partial unique index covers — an existing request in one of these blocks a
# second, and governs supersede-vs-replay-vs-conflict.
_ACTIVE = {
    RevisionRequestStatus.AWAITING_CONTRACT,
    RevisionRequestStatus.QUEUED,
    RevisionRequestStatus.RUNNING,
}


class RevisionOutcome(StrEnum):
    REPLAY_EXISTING = "replay_existing"  # 200 — exact replay of the active request
    ACCEPTED_NEW = "accepted_new"  # 202 — no active request; create a fresh one
    ACCEPTED_REPLACEMENT = "accepted_replacement"  # 202 — supersede the active request, create a fresh one
    NOT_FOUND = "not_found"  # 404
    UNPROCESSABLE = "unprocessable"  # 422
    CONFLICT = "conflict"  # 409 with a typed blocker reason


# Typed 409 blocker reasons (kept as constants so the API + tests share one vocabulary).
BLOCKER_SCENE_SUPERSEDED = "scene_superseded"
BLOCKER_SCENE_CHANGED = "scene_changed"
BLOCKER_REVISION_IN_PROGRESS = "revision_in_progress"
BLOCKER_AMBIGUOUS_CONTRACT = "ambiguous_active_scene_contract"
BLOCKER_OWNERSHIP_HOLD = "ownership_integrity_hold"
BLOCKER_CHAPTER_BUSY = "chapter_workflow_busy"

# 404 subjects / 422 reasons (for the response detail).
NOT_FOUND_SCENE = "scene"
NOT_FOUND_CHAPTER = "chapter"
UNPROCESSABLE_EMPTY_SOURCE = "missing_or_empty_revision_source"
UNPROCESSABLE_UNSUPPORTED_PASS = "unsupported_pass"
UNPROCESSABLE_MALFORMED = "malformed_request"


@dataclass(frozen=True)
class RevisionFacts:
    """DB facts the seam precomputes for the target scene, in evaluation order. Booleans are three-state
    only where a distinction matters; otherwise plain bools."""

    scene_exists: bool
    chapter_exists: bool
    ownership_ok: bool  # Scene -> Chapter -> Book resolves consistently
    scene_superseded: bool
    # Optimistic concurrency: the caller's expected prose hash vs the scene's current prose hash. None
    # means the caller supplied no expectation — treated as a malformed request (hash is REQUIRED).
    expected_prose_hash: str | None
    current_prose_hash: str | None
    # Request body validity (empty feedback+source, unsupported pass).
    source_present: bool
    pass_supported: bool
    # More than one active, compatible approved ScenePacket for the current slot (stale duplicates don't count).
    ambiguous_active_contract: bool
    # The existing active request for this scene, if any, described by the fields that decide replay/supersede.
    active_request_status: RevisionRequestStatus | None
    active_is_exact_replay: bool  # same target scene+version+prose_hash, same pass, same feedback


@dataclass(frozen=True)
class RevisionDecision:
    outcome: RevisionOutcome
    reason: str | None = None  # blocker reason (409) / not-found subject (404) / unprocessable reason (422)


def classify_revision(f: RevisionFacts) -> RevisionDecision:
    """Map precomputed facts to the response outcome. Evaluation order matters: existence → validity →
    authority/concurrency conflicts → active-request state. On any 404/409/422 the seam persists neither
    the Approval nor the RevisionRequest (the rollback guarantee)."""
    # 404 — the target or its chapter is gone.
    if not f.scene_exists:
        return RevisionDecision(RevisionOutcome.NOT_FOUND, NOT_FOUND_SCENE)
    if not f.chapter_exists:
        return RevisionDecision(RevisionOutcome.NOT_FOUND, NOT_FOUND_CHAPTER)

    # 422 — malformed intent. Missing prose-hash expectation is malformed (the hash is a REQUIRED
    # concurrency input); empty source and unsupported pass are unprocessable too.
    if f.expected_prose_hash is None:
        return RevisionDecision(RevisionOutcome.UNPROCESSABLE, UNPROCESSABLE_MALFORMED)
    if not f.source_present:
        return RevisionDecision(RevisionOutcome.UNPROCESSABLE, UNPROCESSABLE_EMPTY_SOURCE)
    if not f.pass_supported:
        return RevisionDecision(RevisionOutcome.UNPROCESSABLE, UNPROCESSABLE_UNSUPPORTED_PASS)

    # 409 — authority / concurrency the forward path can't fix.
    if not f.ownership_ok:
        return RevisionDecision(RevisionOutcome.CONFLICT, BLOCKER_OWNERSHIP_HOLD)
    if f.scene_superseded:
        return RevisionDecision(RevisionOutcome.CONFLICT, BLOCKER_SCENE_SUPERSEDED)
    if f.expected_prose_hash != f.current_prose_hash:
        return RevisionDecision(RevisionOutcome.CONFLICT, BLOCKER_SCENE_CHANGED)
    if f.ambiguous_active_contract:
        return RevisionDecision(RevisionOutcome.CONFLICT, BLOCKER_AMBIGUOUS_CONTRACT)

    # Active-request state. A terminal request (failed/superseded/cancelled/completed/held) is NOT active
    # — it does not block a fresh request (Retry on a failed one is a separate action, not a new revise).
    status = f.active_request_status
    if status is None or status not in _ACTIVE:
        return RevisionDecision(RevisionOutcome.ACCEPTED_NEW)
    if f.active_is_exact_replay:
        return RevisionDecision(RevisionOutcome.REPLAY_EXISTING)
    if status == RevisionRequestStatus.RUNNING:
        # A materially different revise while running would silently discard the new feedback; refuse.
        return RevisionDecision(RevisionOutcome.CONFLICT, BLOCKER_REVISION_IN_PROGRESS)
    # awaiting_contract | queued → supersede (cancel only an unclaimed job) and create the replacement.
    return RevisionDecision(RevisionOutcome.ACCEPTED_REPLACEMENT)
