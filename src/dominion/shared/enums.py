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


class ChapterKind(StrEnum):
    """Reader-facing structural role of a chapter. Display-only — ordering stays by chapter_no; only
    the heading/label changes (a `chapter` renders "Chapter N", the rest render their own label)."""

    CHAPTER = "chapter"
    PROLOGUE = "prologue"
    INTERLUDE = "interlude"
    EPILOGUE = "epilogue"
    FRONT_MATTER = "front_matter"
    BACK_MATTER = "back_matter"


class BeatStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"


class GateMode(StrEnum):
    PAUSE_EACH = "pause_each"  # default, safe
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
    HARD = "hard"  # never blocks; surfaced in the continuity panel (DESIGN §9)


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


class RuleKind(StrEnum):
    """What a distilled rule governs (LEARNING_FROM_EDITS Tier 3)."""

    VOICE = "voice"  # prose style / structure preference
    DIALOGUE = "dialogue"  # how a character's dialogue is written


class RuleProposalStatus(StrEnum):
    """A distilled rule's lifecycle: proposed, then accepted (applied to voice_spec) or rejected."""

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
    UNRESOLVED = "unresolved"  # needs human
    FORBIDDEN = "forbidden"


# --- contract-first drafting: scene packets (Phase 2 → scene-local contract) ----------------------
# A ScenePacket localizes the chapter-wide ChapterPacket into one scene's reader/POV/reveal/word
# constraints. It is the scene-local authority for review; the ChapterPacket stays macro-authoritative.


class ScenePacketStatus(StrEnum):
    """A scene packet's lifecycle. STALE means an upstream input changed (chapter packet, prior
    scene, owner file, word budget) and the packet must be re-derived or re-approved before drafting."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    STALE = "stale"


class ScenePacketVerdict(StrEnum):
    """The ScenePacket QA agent's verdict on a derived scene packet (mirrors PacketVerdict)."""

    APPROVE = "approve"
    APPROVE_WARN = "approve_warn"
    REVISE_REQUIRED = "revise_required"
    BLOCK_DRAFTING = "block_drafting"


class LengthStatus(StrEnum):
    """Where a drafted scene's word count landed against its ScenePacket word_budget (DESIGN: length)."""

    UNDER_MIN = "under_min"
    WITHIN_BUDGET = "within_budget"
    OVER_MAX = "over_max"
    OVER_HARD_MAX_COMPRESSED = "over_hard_max_compressed"
    OVER_HARD_MAX_QUARANTINED = "over_hard_max_quarantined"


class KnowledgeStatus(StrEnum):
    """Lifecycle of a KnowledgeFact: hidden until a scene reveals it to the reader."""

    HIDDEN = "hidden"
    REVEALED = "revealed"


class DraftStage(StrEnum):
    """A preserved stage of one scene's prose pipeline (DraftAttempt provenance)."""

    DRAFTER_RAW = "drafter_raw"
    ENRICHMENT_COMBAT = "enrichment_combat"
    ENRICHMENT_SENSORY = "enrichment_sensory"
    ENRICHMENT_DIALOGUE = "enrichment_dialogue"
    LENGTH_COMPRESSION = "length_compression"
    LENGTH_EXPANSION = "length_expansion"
    FINAL_RENDERED = "final_rendered"
