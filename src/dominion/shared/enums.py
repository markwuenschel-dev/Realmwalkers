"""Status vocabularies for the workflow (DESIGN §3). String-backed for clarity in the DB."""
from __future__ import annotations

from enum import StrEnum


class SceneStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REVISION_REQUESTED = "revision_requested"
    SUPERSEDED = "superseded"


class ChapterStatus(StrEnum):
    PLANNED = "planned"
    BEATS_PROPOSED = "beats_proposed"
    BEATS_APPROVED = "beats_approved"
    DRAFTING = "drafting"
    DONE = "done"


class BeatStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"


class GateMode(StrEnum):
    PAUSE_EACH = "pause_each"   # default, safe
    DRAFT_AHEAD = "draft_ahead"


class RunStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ABORTED = "aborted"


class JobKind(StrEnum):
    DRAFT = "draft"
    REVISE_FULL = "revise_full"
    REVISE_PASS = "revise_pass"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    HARD = "hard"      # never blocks; surfaced in the continuity panel (DESIGN §9)


class Decision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"
    REVISE = "revise"


class ReviewerKind(StrEnum):
    CONTINUITY = "continuity"
    COMBAT = "combat"
    SENSORY = "sensory"
    DIALOGUE = "dialogue"
    PACING = "pacing"
    VOICE = "voice"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# --- contract-first drafting: chapter packets (Phase 1) -------------------------------------------
# Scene-level enums (SceneVerdict, the REJECTED/MOVED scene statuses) arrive with the QA gate in a
# later phase — Phase 1 ships only the packet layer (DESIGN: contract-first drafting, phased build).

class PacketConfidence(StrEnum):
    """The Packet Author's self-assessed confidence — drives the autonomy gate.
    GREEN: eligible for fast-approve. YELLOW: human reviews flagged items. RED: drafting blocked."""
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class PacketVerdict(StrEnum):
    """The Packet QA agent's verdict on a proposed chapter packet."""
    APPROVE = "approve"
    APPROVE_WARN = "approve_warn"
    REVISE_REQUIRED = "revise_required"
    BLOCK_DRAFTING = "block_drafting"


class PacketStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"


class ClaimSource(StrEnum):
    """Source-strength label the Packet Author must attach to every packet claim, so agents can act
    on packets without pretending all decisions are equally certain."""
    LOCKED_CANON = "locked_canon"
    DERIVED_FROM_OUTLINE = "derived_from_outline"
    PLAUSIBLE_INFERENCE = "plausible_inference"
    UNRESOLVED = "unresolved"          # needs human
    FORBIDDEN = "forbidden"
