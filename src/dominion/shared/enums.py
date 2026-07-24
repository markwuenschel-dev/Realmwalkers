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


class PartKind(StrEnum):
    """The label WORD for a Part-level grouping. Structurally identical either way — an Act is a Part
    that renders "Act I" instead of "Part I". Display-only, like ChapterKind."""

    PART = "part"
    ACT = "act"


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
    # An ownerless/conflicted job withheld from execution and from the normal failure controls
    # (retry-failed / clear-failed): terminal, never claimable, retained as integrity evidence (ADR 0027).
    QUARANTINED = "quarantined"


class Severity(StrEnum):
    """Unified severity vocabulary — one language across the issue pipeline and the packet contract
    (shared/severity.py uses the warn/repair/block subset). On Issue/Critique rows these are ADVISORY
    labels for the human (DESIGN §9): even BLOCK never gates drafting there — gating facts derive only
    via shared.severity.issue_gates for packet-contract issues. Legacy rows/JSON snapshots may still
    say "hard" (the pre-unification spelling of BLOCK); readers tolerate both."""

    INFO = "info"
    WARN = "warn"
    REPAIR = "repair"  # fixable: never blocks drafting/approval, gates final export
    BLOCK = "block"


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
    on packets without pretending all decisions are equally certain.

    This is now an ENFORCED precedence order, not just a label (ADR 0029, see shared/claim_precedence.py):
        LOCKED_CANON > DERIVED_FROM_MANUSCRIPT > DERIVED_FROM_OUTLINE > PLAUSIBLE_INFERENCE > UNRESOLVED
    FORBIDDEN is NOT a rank — it is a separate surface-term prohibition (packet/surface_policy.py).
    DERIVED_FROM_MANUSCRIPT means "traceable in an imported prose snapshot" (an M# handle → immutable
    (scene_id, version, prose_hash) + span): strong evidence, but never canon and never an automatic
    override of locked canon. A conflict the order can't break becomes an approval-blocking open question.
    """

    LOCKED_CANON = "locked_canon"
    DERIVED_FROM_MANUSCRIPT = "derived_from_manuscript"  # traceable in imported prose (ADR 0028/0029)
    DERIVED_FROM_OUTLINE = "derived_from_outline"
    PLAUSIBLE_INFERENCE = "plausible_inference"
    UNRESOLVED = "unresolved"  # needs human
    FORBIDDEN = "forbidden"


# --- contract-first drafting: scene packets (Phase 2 → scene-local contract) ----------------------
# A ScenePacket localizes the chapter-wide ChapterPacket into one scene's reader/POV/reveal/word
# constraints. It is the scene-local authority for review; the ChapterPacket stays macro-authoritative.


class ScenePacketStatus(StrEnum):
    """A scene packet's lifecycle. STALE means an upstream input changed (chapter packet, prior
    scene, owner file, word budget) and the packet must be re-derived or re-approved before drafting.
    RATE_LIMITED means the provider 429'd the author/QA call past its automatic retries — transient
    infrastructure, NOT an invalid contract: retry derive (or re-run QA when the body survived)."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"


class ScenePacketVerdict(StrEnum):
    """The ScenePacket QA agent's verdict on a derived scene packet (mirrors PacketVerdict)."""

    APPROVE = "approve"
    APPROVE_WARN = "approve_warn"
    REVISE_REQUIRED = "revise_required"
    BLOCK_DRAFTING = "block_drafting"


class ApprovalBlockerStatus(StrEnum):
    """Lifecycle of a scene-tier ApprovalBlocker (A1c slice 1, ADR-0031 D14). ACTIVE holds automated
    approval of its ScenePacket; RESOLVED requires an explicit rationale + source. No supersede in slice
    1 — a manual_command blocker survives re-derive and is purged only by parent deletion."""

    ACTIVE = "active"
    RESOLVED = "resolved"


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


class CanonStatus(StrEnum):
    """Lifecycle of a CanonEntity row (Workstream H — stale canon/ledger cleanup). Only ACTIVE canon
    reaches agent/prose context; the retrievability rule lives in models.canon_retrievable_filter."""

    ACTIVE = "active"
    STALE = "stale"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


class ArtifactType(StrEnum):
    """The Artifact.artifact_type discriminator vocabulary — one authoritative registry so a producer
    literal and a distant consumer filter can't silently drift (ARTIFACT-TYPE). tests/test_artifact_type.py
    asserts every `artifact_type=` write and `artifact_type==` filter literal in the tree is a member."""

    CHAPTER_PACKET = "chapter_packet"
    CONTRACT_CLASSIFICATION = "contract_classification"
    CHAPTER_SEQUENCE = "chapter_sequence"
    SCENE_PACKET = "scene_packet"
    SCENE_DRAFT = "scene_draft"
    SCENE_REVIEW_REPORT = "scene_review_report"
    DRAFT_RUN_TIMELINE = "draft_run_timeline"
    ISSUE_SET = "issue_set"
    REPAIR_TASK = "repair_task"
    AGENT_EVALUATION = "agent_evaluation"
    FINAL_CHAPTER = "final_chapter"
    CHAPTER_DRAFT = "chapter_draft"
    CHAPTER_DRAFT_QA = "chapter_draft_qa"
    READER_SIMULATION = "reader_simulation"
    SCENE_FIDELITY_REPORT = "scene_fidelity_report"


class DraftStage(StrEnum):
    """A preserved stage of one scene's prose pipeline (DraftAttempt provenance)."""

    DRAFTER_RAW = "drafter_raw"
    ENRICHMENT_COMBAT = "enrichment_combat"
    ENRICHMENT_SENSORY = "enrichment_sensory"
    ENRICHMENT_DIALOGUE = "enrichment_dialogue"
    LENGTH_COMPRESSION = "length_compression"
    LENGTH_EXPANSION = "length_expansion"
    FINAL_RENDERED = "final_rendered"


class ProductionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    REPAIRING = "repairing"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChapterSequenceStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    STALE = "stale"


class IssueStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MERGED = "merged"
    REPAIR_QUEUED = "repair_queued"
    REPAIRED = "repaired"
    VERIFIED = "verified"
    ESCALATED = "escalated"
    FALSE_POSITIVE = "false_positive"
    # SceneFidelity lifecycle (ADR 0020), additive/forward-only. OVERRIDDEN: an author-recorded exception
    # cancelled the human-required task (never inherits to later drafts). SUPERSEDED: a newer current
    # eligible Critique materialized a successor Issue, which the superseded Issue references.
    OVERRIDDEN = "overridden"
    SUPERSEDED = "superseded"


class IssueDecisionKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MERGE = "merge"
    ESCALATE = "escalate"
    MARK_FALSE_POSITIVE = "mark_false_positive"
    DEFER = "defer"


class RepairAuthorityLevel(StrEnum):
    SPAN_ONLY = "span_only"
    SCENE_LOCAL = "scene_local"
    SCENE_STRUCTURAL = "scene_structural"
    CROSS_SCENE = "cross_scene"
    CHAPTER_STRUCTURAL = "chapter_structural"
    # A1b compatibility discriminator (ADR-0031 D16): HUMAN_REQUIRED is a manual-grant Authorization
    # Requirement — an explicit human grant regardless of ceiling — NOT an auto-approval rung. A1c will
    # separate the authorization axis from authority_level (blast radius) durably.
    HUMAN_REQUIRED = "human_required"


# Authority levels the sweeper may use as an auto-approval ceiling. HUMAN_REQUIRED is excluded (it is
# never auto-approvable); any other value fails closed / normalizes to CHAPTER_STRUCTURAL (ADR-0031 D16).
AUTO_APPROVAL_CEILINGS: frozenset[str] = frozenset(
    level.value for level in RepairAuthorityLevel if level is not RepairAuthorityLevel.HUMAN_REQUIRED
)


def is_manual_grant(authority_level: str | RepairAuthorityLevel) -> bool:
    """True iff `authority_level` denotes a manual-grant Authorization Requirement — one that needs an
    explicit human grant and is NEVER autonomously approved, regardless of the sweeper ceiling
    (ADR-0031 D16, A1b). The single decision point for that question: call sites must not re-derive
    `== HUMAN_REQUIRED` (they had drifted on the `.value` suffix). Accepts the enum member or the raw
    persisted string (StrEnum-safe). Distinct from `not in AUTO_APPROVAL_CEILINGS`, which also rejects
    garbage ceilings — this predicate answers only "is it human_required?".

    A1b→A1c: `human_required` is currently overloaded onto RepairAuthorityLevel, which otherwise ranks
    blast radius. The durable fix — a first-class `authorization_requirement` axis orthogonal to blast
    radius — is deferred, UNSCHEDULED follow-up (NOT one of ADR-0031 D18's ADR-0028 slices; it needs its
    own ticket). Until it lands, this helper is the seam that keeps the conflation in one place.
    """
    return authority_level == RepairAuthorityLevel.HUMAN_REQUIRED


class RepairTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    VERIFIED = "verified"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RepairVerificationVerdict(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    NEEDS_ANOTHER_REPAIR = "needs_another_repair"
    ESCALATE_TO_HUMAN = "escalate_to_human"


# --- import adoption & durable revision requests (ADR 0028) ---------------------------------------
# Imported prose is turned into a reviewed contract by a durable, leased Import Adoption; the author's
# edit intent lives in a durable Revision Request until its contract is ready and a revision Job mints.


class ImportAdoptionMode(StrEnum):
    """INITIAL: no approved ChapterPacket yet — propose the first chapter-wide packet from evidence.
    AMENDMENT: an approved ChapterPacket exists but a newly imported scene has no seed (the one case
    normal re-derive can't fix) — copy-on-write from the current packet + evidence for the new prose."""

    INITIAL = "initial"
    AMENDMENT = "amendment"


class ImportAdoptionStatus(StrEnum):
    """Adoption owns adoption progress ONLY; it ends at `contract_proposed` and never mirrors
    downstream ChapterPacket/ScenePacket approval, Job execution, or revision completion (ADR 0028).

    AWAITING_START: created but not worker-claimable (legacy reconciliation; needs explicit start).
    QUEUED: claimable by the adoption worker. RUNNING: leased/extracting. CONTRACT_PROPOSED: terminal
    success, reached atomically with a linked ChapterPacket(status=proposed). FAILED: unusable packet
    or exhausted retries (may link a blocked packet as diagnostic evidence). INVALIDATED: source
    fingerprint no longer matches (re-adoption reuses unchanged evidence shards). CANCELLED: an
    awaiting_start/queued adoption with no remaining active requests."""

    AWAITING_START = "awaiting_start"
    QUEUED = "queued"
    RUNNING = "running"
    CONTRACT_PROPOSED = "contract_proposed"
    FAILED = "failed"
    INVALIDATED = "invalidated"
    CANCELLED = "cancelled"


class LivenessBasis(StrEnum):
    """An ImportAdoption's CURRENT retention authority (ADR-0032 D2) — NOT historical creation
    provenance. Orthogonal to entry status: `status` decides the initial state, `liveness_basis`
    decides SURVIVAL when no RevisionRequest remains.

    REQUEST_BOUND: active only while at least one qualifying RevisionRequest needs its output — a
    request-orphan is reverse-cancellable (D9). OPERATOR_INDEPENDENT: an explicit operator command
    (Start/Re-author) established chapter-contract reconstruction as independently-desired work, so the
    operator command is itself durable demand and the adoption is never auto-cancelled. Merge is
    MONOTONIC — operator_independent never downgrades (D2)."""

    REQUEST_BOUND = "request_bound"
    OPERATOR_INDEPENDENT = "operator_independent"


class EntryIntent(StrEnum):
    """What an adoption-entry caller consents to (ADR-0032 D1). Decides the INITIAL status the seam
    mints, orthogonal to `liveness_basis`. An enum, never a boolean — amendment mode and future entry
    policies would make a boolean unreadable.

    SPEND: worker-claimable spend consent — mints/promotes to `queued`. RECORD_WITHOUT_SPEND: records
    durable intent without consenting to spend — mints `awaiting_start` (an unpaused queue is not consent
    for historical spend); used by boot reconciliation (W4)."""

    SPEND = "spend"
    RECORD_WITHOUT_SPEND = "record_without_spend"


class AdoptionOperation(StrEnum):
    """The canonical adoption-entry command discriminator (ADR-0032 D1/D4). It — not
    `force_author_token`, which is only an idempotency/override contract — drives the seam's documented
    eligibility table and each caller's (entry_intent, liveness_basis). Its value is also the D12
    observability `trigger`.

    OPERATOR_START / REAUTHOR are wired in W1; REVISION (sync auto-start, W3) and RECONCILIATION (boot,
    W4) are wired in their waves — an unwired operation fails closed in the seam."""

    OPERATOR_START = "operator_start"
    REAUTHOR = "reauthor"
    REVISION = "revision"
    RECONCILIATION = "reconciliation"


class EntryEffect(StrEnum):
    """What an adoption-entry call actually did to persisted state (ADR-0032 D11/D12). CREATED: a new row
    was inserted. PROMOTED: a meaningful mutation of an existing row (awaiting_start→queued, a
    request_bound→operator_independent liveness upgrade, or attaching Re-author force-token/lineage where
    the ADR requires it). UNCHANGED: no persisted field changed (a completely inert reuse — emits no
    transition telemetry). `joined` is deliberately absent: it is a W3 coordinator/request-link
    interpretation, not an adoption-row transition."""

    CREATED = "created"
    PROMOTED = "promoted"
    UNCHANGED = "unchanged"


class ReconcileDemandOutcome(StrEnum):
    """What `reconcile_adoption_demand_locked` (ADR-0032 D9) did to the chapter's active adoption when a
    request-lifecycle mutation REMOVED demand. Reverse cancellation is adoption-owned and fail-closed.

    NO_ACTIVE_ADOPTION: the chapter has no active (awaiting_start/queued/running) adoption to reconcile.
    PRESERVED_NON_REQUEST_BOUND: an `operator_independent` adoption is durable demand in its own right and
      is NEVER auto-cancelled (D2/D9). PRESERVED_RUNNING: a running adoption finishes — interrupting a
      mid-model-call claim is out of scope (D10). PRESERVED_ACTIVE_DEMAND: a `request_bound` adoption still
      has at least one qualifying active RevisionRequest, so its demand stands. CANCELLED: a `request_bound`
      awaiting_start/queued adoption with zero qualifying active requests is reverse-cancelled.

    An INDETERMINATE demand read (SQL failure, >1 active adoption despite the partial-unique index, an
    unknown liveness/status value) is NOT one of these outcomes — it raises and rolls the whole
    authority-changing transaction back. Never infer 'no demand' from a bad read."""

    NO_ACTIVE_ADOPTION = "no_active_adoption"
    PRESERVED_NON_REQUEST_BOUND = "preserved_non_request_bound"
    PRESERVED_RUNNING = "preserved_running"
    PRESERVED_ACTIVE_DEMAND = "preserved_active_demand"
    CANCELLED = "cancelled"


class RevisionRequestStatus(StrEnum):
    """Durable author edit-intent lifecycle. Coarse and persisted; the fine UI banner (Preparing
    contract / Awaiting chapter approval / Derive target scene contract / Awaiting scene approval /
    Queued / Ready for review / Held / Failed) is SERVER-DERIVED from the request + adoption + packets
    + Job, never stored (ADR 0028).

    AWAITING_CONTRACT: needs an approved ScenePacket (adoption/derive in flight). QUEUED: a revision
    Job is queued. RUNNING: the revision Job is executing. COMPLETED: a review-ready revised Scene
    landed. HELD: the worker produced a partial/budget-held result (NOT review-ready). FAILED: the
    revision Job failed (terminal evidence; Retry reactivates this same request). SUPERSEDED: replaced
    by a newer request for the same scene. CANCELLED: the author cancelled the queued revision."""

    AWAITING_CONTRACT = "awaiting_contract"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    HELD = "held"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class RevisionRequestOrigin(StrEnum):
    """Where the durable intent came from — kept for audit and for the read model's context."""

    REVIEW = "review"  # POST /scenes/{id}/decision, decision=revise
    CONTINUITY = "continuity"  # POST /scenes/{id}/continuity/resolve, choice=use_ledger
    LEGACY_RECONCILIATION = "legacy_reconciliation"  # boot recovery from the latest revise Approval
