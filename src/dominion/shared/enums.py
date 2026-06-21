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


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReviewerKind(StrEnum):
    CONTINUITY = "continuity"
    COMBAT = "combat"
    SENSORY = "sensory"
    DIALOGUE = "dialogue"
    PACING = "pacing"
    VOICE = "voice"
