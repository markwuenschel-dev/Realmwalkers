"""ORM schema — the single Python source of truth for the database (DESIGN §3).

Versioning is by rows, not Git: a revision inserts a new `Scene` row (version+1,
parent_scene_id set) and the prior flips to SUPERSEDED. Runtime exhaust (logs, job
status) lives in tables/stdout, never in a repo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    or_,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from dominion.shared.chapter_order import chapter_position
from dominion.shared.enums import CanonStatus


class Base(DeclarativeBase):
    pass


def _chapter_default_position(context: Any) -> int:
    """Populate `Chapter.position` on insert from the row's kind + chapter_no when it isn't given
    explicitly — so EVERY chapter carries the shared reading-order key, even on direct model construction
    (tests, seeds, any path that doesn't set it). An explicit `position=` (e.g. an import assigning a
    numberless section's slot with a per-batch `seq`) is passed by the caller and overrides this default.
    `chapter_position` treats an absent kind as a plain chapter, so column evaluation order is irrelevant.
    """
    params = context.get_current_parameters()
    return chapter_position(params.get("kind"), params.get("chapter_no"), section_type=params.get("section_type"))


class Book(Base):
    __tablename__ = "books"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    premise: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Export/provenance metadata (renderer-neutral export foundation): these feed ExportMetadata so the
    # emitters never hard-code project identity. ALL nullable with NO server default on purpose — a new
    # book must NOT inherit Dominion identity implicitly. Pre-existing rows are backfilled to the Dominion
    # values by a timestamp-guarded one-time migration (see migrations._BACKFILLS); books created after
    # that cutoff stay NULL until the project sets them, and the metadata resolver omits absent fields.
    series: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "Dominion Realm"; NULL = standalone
    book_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # position in series; drives "BOOK ONE"
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)  # real book subtitle (NOT the reader-mode line)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Volume(Base):
    """The TOP structural grouping level (Book → Volume → Part → Chapter). A Volume groups Parts, exactly
    as a Part groups Chapters — the same pattern one tier up. Durable and optional: a book may have no
    volumes, or partition its parts into ordered volumes via `Part.volume_id`. Ordering keys off
    `volume_no` (unique within a book); the "Volume One" label is derived from it, never stored."""

    __tablename__ = "volumes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    volume_no: Mapped[int] = mapped_column(Integer)  # ordering + label; unique within a book (app-enforced)
    title: Mapped[str] = mapped_column(Text)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Part(Base):
    """A reader-facing grouping of chapters (Book → Part → Chapter → Scene) — the mid-tier structural
    spine level between Book and Chapter.

    Durable and optional: a book may have zero parts (every chapter ungrouped) or partition its chapters
    into ordered parts via `Chapter.part_id`. Ordering keys off `part_no` (unique within a book); the
    reader-facing label ("Part One") is derived from it by the shared label contract, never stored.
    Chapters keep their own global `chapter_no` — a Part is a grouping/divider layer, not a renumbering.

    `kind` chooses the label WORD only: "part" → "Part I", "act" → "Act I" (an Act is structurally
    identical to a Part — same grouping, different name — so it needs no separate table). `volume_id`
    optionally nests this Part under a Volume (NULL = top-level part)."""

    __tablename__ = "parts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    # Optional nesting under a Volume (Book → Volume → Part). NULL = ungrouped (top-level part).
    volume_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("volumes.id"), nullable=True)
    part_no: Mapped[int] = mapped_column(Integer)  # ordering + label source; unique within a book (app-enforced)
    title: Mapped[str] = mapped_column(Text)  # e.g. "The Gathering Storm"
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Label word only: part | act (see PartKind). server_default keeps pre-existing rows valid as "part".
    kind: Mapped[str] = mapped_column(Text, default="part", server_default="part")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chapter(Base):
    """Owns POV (Game-of-Thrones model: one POV per whole chapter) and the outline."""

    __tablename__ = "chapters"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    # Optional grouping into a Part (Book → Part → Chapter). NULL = ungrouped (renders under no part
    # divider); reading order keys off `position`, not part membership.
    part_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parts.id"), nullable=True)
    # Reading-order sort key — the ONE thing every reader/export/list orders by (see shared/chapter_order.py,
    # the single source that computes it from kind + chapter_no). Decoupled from the display number so a
    # numberless section (prologue/epilogue/front-/back-matter) can sort before/after chapters without a
    # number to collide on. Nullable only until the one-time backfill fills legacy rows; otherwise always
    # populated — an explicit value from the caller, else derived on insert by _chapter_default_position.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True, default=_chapter_default_position)
    # DISPLAY number only ("Chapter 3"), NOT the sort key or identity. NULL for a numberless kind
    # (prologue/interlude/epilogue/front_matter/back_matter) — those label off `kind`, never a number.
    # Set only for a plain `chapter`. Identity is `id` (UUID); ordering is `position`.
    chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # plan-call proposes; author edits
    pov: Mapped[str] = mapped_column(Text)  # single narrating character
    outline: Mapped[str | None] = mapped_column(Text, nullable=True)  # input to beat-proposal
    status: Mapped[str] = mapped_column(Text, default="planned")
    # Reader-facing structural role (see ChapterKind): chapter | prologue | interlude | epilogue |
    # front_matter | back_matter. Drives BOTH the reader label (a plain "chapter" renders "Chapter N",
    # the rest render their own label) AND the reading-order band (via chapter_order.chapter_position).
    # server_default keeps pre-existing rows valid.
    kind: Mapped[str] = mapped_column(Text, default="chapter", server_default="chapter")
    # For front_matter/back_matter chapters: the specific section type (glossary | dramatis_personae |
    # map | preface | afterword | appendix | acknowledgments | preview | …). Drives the reader label +
    # semantic tag; NULL/ignored for ordinary chapters. Free text (like a slug) so the catalog can grow
    # without a migration; the frontend maps known slugs to display names, title-cases the rest.
    section_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional short quote/text shown at the chapter opening, before the prose, in reader + exports.
    epigraph: Mapped[str | None] = mapped_column(Text, nullable=True)


class PovProfile(Base):
    """Voice + few-shot exemplars per narrating character."""

    __tablename__ = "pov_profiles"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    character: Mapped[str] = mapped_column(Text)
    voice_spec: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Scene UUIDs (stored as text) the drafter few-shots on for this POV's voice (LEARNING_FROM_EDITS Tier 2).
    exemplar_scene_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)


class Run(Base):
    """A generation request: scope + gate mode (DESIGN §8)."""

    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gate_mode: Mapped[str] = mapped_column(Text)  # pause_each | draft_ahead
    token_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """One unit of worker work: draft one scene OR one revision (DESIGN §4).

    Direct ID routing (scene-packet contract system): every new draft/revision job carries the
    book/chapter/beat/scene_packet ids so `assemble_context` resolves work without `run_id` —
    `run_id` is now batch/provenance metadata, not the routing key.
    """

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    # Links draft/repair jobs back to a specific ProductionRun so that DraftRunTimeline, context
    # memory, and post-scene timeline updates are scoped to the correct production execution rather
    # than the latest by chapter.
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(Text)  # draft | revise_full | revise_pass
    target_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    target_pass: Mapped[str | None] = mapped_column(Text, nullable=True)
    book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id"), nullable=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    beat_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("beats.id"), nullable=True)
    scene_packet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_packets.id"), nullable=True)
    # The durable RevisionRequest this revision Job was minted for (ADR 0028). The revision-context
    # loader reads the immutable feedback THROUGH this link, never "latest revise Approval". Null for
    # legacy revision jobs (they fall back to the latest revise Approval only for backward compat) and
    # for all draft jobs. The FK is added NOT VALID in _EXTRA_DDL (existing table).
    revision_request_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="queued")
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Terminal (done/failed) timestamp — powers the Activity drawer's per-job durations and
    # "recently finished" feed. Nullable: rows that finished before this column existed stay NULL.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # why a FAILED job died (diagnostics)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobIntegrityState(Base):
    """Singleton (id=1) row tracking the last-emitted job-ownership integrity report (ADR 0027), so boot
    appends an Activity transition ONLY when the picture changes — Activity is append-only, and emitting
    it every boot would flood the Desk on each redeploy. Updated atomically with that Activity row.

    Deliberately NOT a ModelOverride: that table is live model-selection config loaded into runtime
    settings; integrity state is operational, not configuration.
    """

    __tablename__ = "job_integrity_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Stable hash of the current integrity holds (quarantined-live ∪ unresolved null-book rows). The
    # transition record fires when this changes, including back to the empty-holds fingerprint.
    fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    hold_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Beat(Base):
    """Per-scene plan; proposed by the plan-call, approved/edited by the human (gate 1).

    Under contract-first drafting (Phase 2) a chapter's beats are derived from the approved
    ChapterPacket's scene_seeds — `scene_seed_id` is the stable sync key that links a beat back to its
    seed, so re-deriving after a packet edit updates in place instead of duplicating. Null for beats
    that came from the independent plan-call (the fallback path for chapters without a packet)."""

    __tablename__ = "beats"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    scene_seed_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # links to packet scene_seed
    # The stronger link under the scene-packet contract system: a beat is the display/routing
    # projection of an approved ScenePacket. Hard constraints stay in the packet, never copied here.
    scene_packet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_packets.id"), nullable=True)
    scene_no: Mapped[int] = mapped_column(Integer)
    characters_present: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)  # routes specialists
    expected_state_changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # declared deltas
    # LEDGER: durable one-shot guard — True once this beat's expected_state_changes have been committed to
    # the CharacterState ledger, so a scene revision's re-approval can't double-apply relative '+N' deltas.
    # On the Beat (not the versioned Scene) because deltas are declared per beat and looked up by the stable
    # (chapter_id, scene_no).
    deltas_committed: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_injections: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    beat_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_words: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per-scene length guide
    # Optional per-scene POV override the author sets after beats are proposed; null/blank inherits the
    # chapter's POV. A scene drafts in its EFFECTIVE pov (this override or Chapter.pov) — see
    # workers/pov.effective_pov, used everywhere POV is resolved for a scene.
    pov: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="proposed")


class Scene(Base):
    """Prose. A revision is a NEW row, never a mutation. `prose` is the single source of truth."""

    __tablename__ = "scenes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    scene_no: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="draft")
    # The approved ScenePacket this draft was written against — the contract of record for review.
    scene_packet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_packets.id"), nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    length_status: Mapped[str | None] = mapped_column(Text, nullable=True)  # see enums.LengthStatus
    prose: Mapped[str | None] = mapped_column(Text, nullable=True)
    prose_source: Mapped[str] = mapped_column(Text, default="agent")  # agent | agent+human_edit
    agent_original: Mapped[str | None] = mapped_column(Text, nullable=True)  # for training capture
    passes_run: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChapterPacket(Base):
    """Chapter knowledge packet (DESIGN: contract-first drafting). Authored by the Packet Author
    agent from locked canon + outline, validated by the Packet QA agent, adjudicated by the human.

    It is the constraint document every drafting agent obeys: allowed/forbidden knowledge & reveals,
    roster/canon/timeline locks, the emotional spine, chapter entry/exit state, per-scene seeds, and
    known drift risks. The writer is scoped to it; the packet author is NOT (scoping protects the
    writer, not the planner). `confidence` drives the autonomy gate: green proceeds, yellow needs the
    human to clear flags, red blocks drafting.
    """

    __tablename__ = "chapter_packets"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    status: Mapped[str] = mapped_column(Text, default="proposed")  # proposed | approved | blocked
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # green | yellow | red
    qa_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)  # approve|approve_warn|revise_required|block
    qa_warnings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # {residual_risks: [...]}
    # The full structured packet. `claims[]` carry provenance ({claim, source_strength, source_id,
    # source_title_or_file, excerpt?, confidence}), and `scene_seeds[]` carry a server-minted stable
    # `seed_id` (UUID) — the sync key for later contract derivation, NOT scene_no (display order).
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    open_questions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScenePacket(Base):
    """Scene-local knowledge contract derived from an approved ChapterPacket (scene-packet system).

    The ChapterPacket stays macro-authoritative; the ScenePacket becomes scene-local authoritative for
    reader/POV knowledge state, allowed/forbidden reveals, intentional mysteries, reviewer
    false-positive traps, and the per-scene word budget. The drafter writes against it and the
    reviewers critique against it. `body` follows the ScenePacket body contract (DESIGN: scene-packet).

    `source_hash` is the canonical hash of every input the packet was derived from (chapter packet,
    scene seed, word budget, prior approved scenes, owner-file/canon hashes); when an input changes the
    packet is marked `stale` and cannot create a new draft job until re-derived or re-approved.
    """

    __tablename__ = "scene_packets"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    chapter_packet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapter_packets.id"))
    scene_seed_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # sync key back to the seed
    scene_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="proposed")  # see enums.ScenePacketStatus
    qa_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_warnings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The canon/owner snippets this packet was derived from: a list of
    # {handle, doc_path, heading_path, owner_topic, retrieval_reason, score}. Kept (the derive used to
    # discard everything but the snippet text) so the Desk can show "built from these sources" and the
    # author's claim_sources handles resolve back to a real file + heading — i.e. a wrong claim is traceable.
    sources: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ADR-0028 Slice 3b (Q9): the imported Scene an adoption-derived packet is bound to — the JOIN KEY
    # the waiting RevisionRequest's resume uses to find its target-scene contract. Set at derive for
    # adoption-linked packets ONLY; NULL for ordinary (planning-path) packets. A PLAIN UUID with NO inline
    # ForeignKey — the FK is added NOT VALID in migrations._EXTRA_DDL on the existing scene_packets table
    # (mirroring jobs.revision_request_id); an inline ForeignKey would make create_all emit a SECOND
    # auto-named FK. Distinct from KnowledgeFact.source_scene_id despite the shared column name.
    source_scene_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalBlocker(Base):
    """Durable, scene-scoped hold on a ScenePacket's automated approval (A1c slice 1, ADR-0031 D9/D14).

    A normalized record with its own lifecycle — NOT a flag in `ScenePacket.body`, NOT a live derivation
    from `ChapterPacket.open_questions`. An ACTIVE blocker means the scene packet may not be approved and
    may not retain approved-derived beats. Owned by `scene_packet_id` (a prose Scene may not exist before
    packet approval). Deduped by `(source, source_key)`; at most one ACTIVE row per
    `(scene_packet_id, source, source_key)` (partial-unique index). Purged only when its parent scene
    packet is deleted (ON DELETE CASCADE) — the explicit retention boundary; NOT superseded by re-derive.
    """

    __tablename__ = "approval_blockers"
    __table_args__ = (
        Index(
            "uq_active_approval_blocker",
            "scene_packet_id",
            "source",
            "source_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_packet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scene_packets.id", ondelete="CASCADE"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(Text)  # e.g. "manual_command"
    source_key: Mapped[str] = mapped_column(Text)  # supplied by the raising command
    status: Mapped[str] = mapped_column(Text, default="active")  # see enums.ApprovalBlockerStatus
    question: Mapped[str] = mapped_column(Text)  # the unresolved scene-level open question
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportAdoption(Base):
    """Durable, leased, checkpointed work that turns one chapter's imported prose into a reviewed
    ChapterPacket, on demand (ADR 0028). Its own table + own claim loop (workers/import_adoption.py):
    it commits per-scene checkpoints between long model calls, so it cannot reuse the Job worker's
    transaction-held-through-generation model. Owns adoption progress ONLY — it ends at
    `contract_proposed` and never mirrors ChapterPacket/ScenePacket approval or Job execution.

    `source_fingerprint` is a hash over sorted (scene_no, scene_id, version, prose_sha256) for every
    snapshotted scene — PROSE-HASH based, because the inbox hand-edit path mutates scene.prose in place
    (not every mutation is a new row). `evidence_manifest` is the immutable list of the exact
    ImportSceneEvidence shard ids/hashes this adoption consumed (evidence is shared/reusable across
    adoptions; the manifest is the per-adoption audit record). One adoption serves every active
    RevisionRequest in its chapter.
    """

    __tablename__ = "import_adoptions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    mode: Mapped[str] = mapped_column(Text, default="initial")  # see enums.ImportAdoptionMode
    status: Mapped[str] = mapped_column(Text, default="queued")  # see enums.ImportAdoptionStatus
    # ADR-0032 W1 (D2/D13): CURRENT retention authority (orthogonal to status) — the axis reverse-
    # cancellation (W2) guards on. NOT NULL with NEITHER a Python nor a server default: the adoption seam
    # (shared/adoption_entry.py) is now the sole writer and always supplies an explicit basis (the AST
    # guard enforces that), so a constructor that forgets it must FAIL rather than silently default. W0's
    # TEMPORARY server default (which backfilled existing rows) is dropped by migrations, and the column is
    # SET NOT NULL + CHECK-constrained to the two permitted values there.
    liveness_basis: Mapped[str] = mapped_column(
        Text
    )  # NOT NULL, no default; see enums.LivenessBasis (request_bound|operator_independent)
    source_fingerprint: Mapped[str] = mapped_column(Text)  # sorted (scene_no, scene_id, version, prose_sha256)
    # The immutable manifest of ImportSceneEvidence shards consumed: [{scene_id, scene_version,
    # prose_hash, extractor_schema_version, evidence_id}]. Filled as extraction checkpoints commit.
    evidence_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ADR-0028 Slice 3b (Q8): seed→scene lineage, written ONCE at ChapterPacket publish,
    # {seed_id: {"scene_no": int, "scene_id": uuid}}. Lets a later derive/resume map an approved packet's
    # scene_seed back to the imported Scene it was adopted from.
    seed_bindings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ADR-0028 Slice 3b (Q11), tiered-idempotency tier B: a hash over the evidence-shard ids consumed AND
    # the canon-retrieval snapshot the author saw, so an unchanged-input re-adoption is a lookup, not a
    # re-run.
    author_input_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The reviewable ChapterPacket produced on contract_proposed (a blocked packet stays here as
    # diagnostic evidence while status=failed). ON DELETE SET NULL (ADR-0028 Slice 3b, Q11 tier-C): a
    # re-author REPLACES the chapter's current packet, so the pipeline deletes the old one — a prior
    # contract_proposed adoption that still links it must not block that delete, so its link nulls instead.
    # A NULL here on a `contract_proposed` row therefore means "that SUCCESSFUL historical proposal's
    # transient packet was subsequently REPLACED", NOT that the adoption was retroactively unsuccessful.
    # (The deployed FK is altered to SET NULL by migrations._EXTRA_DDL; create_all alone would not touch a
    # persistent prod DB whose constraint predates this change.)
    chapter_packet_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chapter_packets.id", ondelete="SET NULL"), nullable=True
    )
    # ADR-0028 Slice 3b (Q11 tier-C, operator Re-author): the IMMUTABLE operator-action UUID the client
    # supplies to force a fresh author pass past the reuse gate. It is simultaneously the one-run tier-C
    # override signal (the worker BYPASSES _find_reuse when it is set) AND the idempotency key (a partial
    # UNIQUE index on it — WHERE force_author_token IS NOT NULL — makes a retried Re-author with the same
    # token collide instead of buying a second reroll). A PLAIN UUID with NO inline ForeignKey (it is not
    # a row reference). It NEVER enters author_input_fingerprint — a force token is an execution command,
    # not author-input identity, so a later ordinary Start reuses the force-generated packet via _find_reuse.
    force_author_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Audit link to the prior adoption this Re-author supersedes (the most recent CONTRACT_PROPOSED one, or
    # NULL). A PLAIN self-reference UUID; the FK is added NOT VALID in migrations._EXTRA_DDL (an inline
    # ForeignKey would make create_all emit a second auto-named FK, mirroring scene_packets.source_scene_id).
    reauthor_of_adoption_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lease/claim (durable like jobs): a claim expires and boot recovery re-queues it.
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImportSceneEvidence(Base):
    """One resumable LLM extraction from an imported scene snapshot into a span-anchored fact ledger
    (ADR 0028). An IMMUTABLE source artifact keyed by (scene_id, scene_version, prose_hash,
    extractor_schema_version) — NOT owned by a single adoption, so re-adoption reuses unchanged shards.

    `ledger` is the structured evidence the ChapterPacket Author reads as M# sources (entities, POV,
    setting, events, asserted facts, state/inventory/relationship changes, reveals, withholds,
    entry/exit state, continuity anchors, ambiguities, canon conflicts), each item anchored to a span
    of the immutable snapshot. Raw prose stays auditable in the Desk but never enters the author prompt.
    An oversized scene extracts as deterministic chunk shards + a bounded merge; shards are retained.
    """

    __tablename__ = "import_scene_evidence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    scene_version: Mapped[int] = mapped_column(Integer)
    prose_hash: Mapped[str] = mapped_column(Text)  # sha256 of the exact snapshot prose (prose_fingerprint.prose_sha256)
    # The immutable snapshot of the exact prose this evidence was extracted from (R1/ADR 0028). Scene.prose
    # is the CURRENT manuscript and can be hand-edited in place, so it is NOT an audit of a past evidence
    # identity — the snapshot bytes must live here. Copied before extraction; never mutated after.
    snapshot_prose: Mapped[str] = mapped_column(Text)
    extractor_schema_version: Mapped[str] = mapped_column(Text)  # bump to invalidate reuse on schema change
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    # The span-anchored fact ledger. When present this is a completed shard; a partial/merged extraction
    # records its chunk shards separately and links them here on merge.
    ledger: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Derived, write-once membership of the per-chunk children this row merged (audit convenience; the
    # ImportSceneEvidenceChunk rows are authoritative). `{"chunk_ids": [...]}` for a chunked extraction,
    # NULL for single-pass. Never truncation.
    merged_shard_ids: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportSceneEvidenceChunk(Base):
    """One retained chunk shard of an oversized-scene extraction (ADR 0028, R2/R3). Chunk shards of one
    snapshot share the parent's identity 4-tuple, so they CANNOT be rows in `import_scene_evidence`
    (uq_import_scene_evidence_identity) — they live here, owned by the parent. The children are
    authoritative for chunk membership and order; the parent's `merged_shard_ids` is a derived, write-once
    convenience. Each row keeps its chunk-LOCAL ledger plus the [char_offset, char_end) window it covers in
    whole-scene coordinates, so the merged parent ledger is reconstructible from the children alone."""

    __tablename__ = "import_scene_evidence_chunks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_scene_evidence.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)  # 0..N-1, extraction order
    char_offset: Mapped[int] = mapped_column(Integer)  # start offset in whole-scene coordinates
    char_end: Mapped[int] = mapped_column(Integer)  # end offset (offset + len(chunk_text)); NOT NULL (R3)
    ledger: Mapped[dict[str, Any]] = mapped_column(JSONB)  # chunk-LOCAL span-anchored ledger (unshifted)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("evidence_id", "chunk_index", name="uq_import_scene_evidence_chunk_index"),)


class RevisionRequest(Base):
    """Durable record of the author's edit intent (ADR 0028). Immutable target (scene_id, version),
    feedback, target_pass, and origin; a mutable coarse `status` (see enums.RevisionRequestStatus).
    At most ONE active request per target_scene_id — enforced by a partial unique index over the active
    states (awaiting_contract, queued, running) in _EXTRA_DDL. The fine display phase is server-derived,
    never stored.

    Feedback is immutable HERE (beside its source Approval); it is never copied onto the Job. The
    revision-context loader resolves feedback through Job.revision_request_id → this row.
    """

    __tablename__ = "revision_requests"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    # Immutable target: a specific Scene version row. scene_version is captured for audit/anchoring;
    # target_scene_id already names the version. prose_hash pins the exact snapshot (in-place edits).
    target_scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    scene_no: Mapped[int] = mapped_column(Integer)
    target_scene_version: Mapped[int] = mapped_column(Integer)
    target_prose_hash: Mapped[str] = mapped_column(Text)  # sha256 of the prose at request time (concurrency)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_pass: Mapped[str | None] = mapped_column(Text, nullable=True)  # scopes HOW it runs, not parallelism
    origin: Mapped[str] = mapped_column(Text)  # see enums.RevisionRequestOrigin
    status: Mapped[str] = mapped_column(Text, default="awaiting_contract")  # see enums.RevisionRequestStatus
    # Provenance/links (soft): the source Approval, the serving adoption (shared per chapter), the
    # minted revision Job, and the produced result Scene.
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    import_adoption_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    result_scene_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChapterSequence(Base):
    """Durable chapter-level scene ownership and budget plan used by the editorial production flow."""

    __tablename__ = "chapter_sequences"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    chapter_packet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapter_packets.id"))
    status: Mapped[str] = mapped_column(Text, default="proposed")
    target_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hard_max_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_scene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hard_max_scene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    qa_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    qa_warnings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DraftAttempt(Base):
    """A preserved stage of one scene's prose pipeline (provenance for compress/expand/enrich).

    The pipeline rewrites model output before the human sees it (enrichment, length guard); each stage
    is recorded here so the evidence of what every stage did is never destroyed. `stage` is an
    enums.DraftStage value. Append-only; never mutated."""

    __tablename__ = "draft_attempts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    scene_packet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_packets.id"), nullable=True)
    stage: Mapped[str] = mapped_column(Text)
    prose: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCall(Base):
    """One model call's persisted telemetry (cache/usage/truncation/latency) — the durable record the
    in-process progress registry never kept.

    Written by an instrumented orchestrator (currently the scene-packet derive) from the context-scoped
    telemetry sink (`workers/telemetry.py`). `stage` distinguishes call sites (scene_packet_author,
    scene_packet_qa, …) so cost and cache efficiency can be compared across chapters/scenes/models, and
    `truncated`/`error` make a blocked derive diagnosable after the fact without server-log access.
    Pure runtime exhaust: append-only, never mutated, safe to prune.
    """

    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # One derive invocation stamps all of its calls with the same run_id, so the telemetry surfaces can
    # show a single run in isolation (the Packets panel = latest run) and a per-run history (the
    # Telemetry tab) instead of one ever-growing cumulative total. Nullable: legacy rows predate it.
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Soft link to the editorial ProductionRun whose job produced this call (draft OR repair revision,
    # both go through generate_one_scene -> persist_sink). Lets Telemetry answer "cost per production
    # run" without a new cost pool. NO ForeignKey on purpose: production runs get deleted and a hard FK
    # would block that delete or cascade-erase the exhaust -- a soft link the UI resolves best-effort,
    # exactly as Activity.production_run_id does. Nullable: derive/planning calls and legacy rows.
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id"), nullable=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scene_seed_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    stage: Mapped[str] = mapped_column(Text)  # scene_packet_author | scene_packet_qa | ...
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-call diagnostics (max_tokens, context_sections, section_name, fallback_attempt, call_index, …).
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeFact(Base):
    """A discrete story fact and WHO knows it WHEN (scene-packet knowledge ledger).

    Separates durable knowledge-state from the lossy rolling summaries: a fact can be hidden, known to
    the reader after a given scene, and/or known to a character after a given scene. Populated
    best-effort from approved scenes' ScenePacket reveals (learned_during_scene.reader_must_learn);
    queryable so later tooling can answer "what did the reader know before scene N?" deterministically.
    """

    __tablename__ = "knowledge_facts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    fact: Mapped[str] = mapped_column(Text)
    source_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    known_by_reader_after_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    known_by_character: Mapped[str | None] = mapped_column(Text, nullable=True)
    known_by_character_after_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="hidden")  # hidden | revealed
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CanonEntity(Base):
    """Story bible / canon, retrievable via pgvector (DESIGN §7).

    Hybrid-retrieval metadata (scene-packet RAG upgrade): `doc_path`/`heading_path` carry provenance,
    `owner_topic`/`source_priority` drive owner-file precedence over semantic hits, and
    `content_hash`/`embedding_*` make ingest incremental (skip unchanged chunks, re-embed changed ones).
    """

    __tablename__ = "canon_entities"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)  # character|location|faction|lore|item
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provenance + lifecycle (Workstream H — stale canon/ledger cleanup). Enum-as-Text (like
    # KnowledgeFact.status): how this row got here, and whether it should still reach agent context.
    # Retrieval EXCLUDES any row whose status is not `active` (NULL treated as active for safety).
    source: Mapped[str] = mapped_column(
        Text, default="manual"
    )  # manual | repo_ingested | packet_derived | draft_derived | legacy
    status: Mapped[str] = mapped_column(
        Text, default="active"
    )  # see enums.CanonStatus (active|stale|retired|superseded)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    doc_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(Text, nullable=True)


def canon_retrievable_filter():
    """CANON-STATUS: the single status-aware canon retrieval gate (Workstream H). Only ACTIVE canon
    reaches agent/prose context; stale/retired/superseded rows are excluded, and a NULL status is treated
    as active so rows written before the `status` column existed still surface. Every retrieval path
    (workers/memory/retrieval.py, canon_rag.py) imports this, so the rule lives in exactly one place."""
    return or_(CanonEntity.status.is_(None), CanonEntity.status == CanonStatus.ACTIVE.value)


class CharacterState(Base):
    """Hard numbers. The Oracle's backing store. NEVER fuzzy-retrieved (DESIGN §5, §7)."""

    __tablename__ = "character_state"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    # CHAR-UNIQ: unique per (book_id, lower(character)) — a case-insensitive functional unique index lives
    # in migrations.py (_EXTRA_DDL). The stored value keeps display case; every reader/writer case-folds
    # its lookup. create_all can't express a functional index, so that DDL is the source of truth.
    character: Mapped[str] = mapped_column(Text)
    as_of_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False)  # from unapproved draft_ahead scene
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Summary(Base):
    """Memory. Two scopes: per-POV (feeds drafter) + omniscient (planner + reviewer) (DESIGN §7)."""

    __tablename__ = "summaries"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    scope: Mapped[str] = mapped_column(Text)  # pov | omniscient
    pov: Mapped[str | None] = mapped_column(Text, nullable=True)
    up_to_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Critique(Base):
    """Advisory ONLY. Never changes scene.status. Never blocks the inbox (DESIGN §2, §9)."""

    __tablename__ = "critiques"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    scene_packet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scene_packets.id"), nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer: Mapped[str] = mapped_column(Text)  # continuity|combat|sensory|...|scene_fidelity
    severity: Mapped[str] = mapped_column(Text)  # info|warn|repair|block (legacy rows: hard == block)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # for continuity mismatches: {character, prose_value, ledger_value, context_sentence, span}
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # SceneFidelity provenance (ADR 0021), generic + nullable + forward-only. A fidelity Critique is the
    # operational projection of one report finding: it points at the source DraftAttempt and the immutable
    # report Artifact (soft links, no FK — mirrors Issue.artifact_id), and carries a finding_signature so
    # the partial unique index (reviewer, source_artifact_id, finding_signature) keeps projection
    # idempotent. Legacy critiques leave all four NULL and are unaffected.
    draft_attempt_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    finding_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )


class Thread(Base):
    """A narrative thread (relationship / mentorship / system / power arc) tracked across scenes.

    Human-curated from the Desk's Ledger: the mock invented these, so this is the real backing store.
    """

    __tablename__ = "threads"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)  # relationship|mentorship|system|power|...
    state: Mapped[str | None] = mapped_column(Text, nullable=True)  # active|sealed|contested|rising|...
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadBeat(Base):
    """A pinned moment of a thread at a given scene number (the dots on the thread's timeline)."""

    __tablename__ = "thread_beats"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id"))
    scene_no: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    flag: Mapped[bool] = mapped_column(Boolean, default=False)  # marks an open continuity question


class Annotation(Base):
    """A human margin note pinned to a quote in a scene (Notes gutter + inline `anno` marker).

    `quote` anchors the inline marker by substring (matches the Desk's tokenize() approach); null quote
    is a scene-level note. Advisory only — never affects scene.status.
    """

    __tablename__ = "annotations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Suggestion(Base):
    """A tracked-change proposal: replace `quote` with `new_text` (empty new_text = deletion).

    Advisory until accepted; accepted suggestions are applied to the prose when the human approves the
    scene (folded into `edited_prose`). `quote` anchors the inline `sugg` marker by substring.
    """

    __tablename__ = "suggestions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str] = mapped_column(Text)
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Approval(Base):
    """The human's verdict = authoritative gate AND future training label (DESIGN §11)."""

    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(Text)  # approve|deny|revise
    target_pass: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EditPair(Base):
    """A faithful agent→human prose pair, captured on a hand-edit (DESIGN §11; LEARNING_FROM_EDITS Tier 1).

    `agent_text` is the model's RENDERED draft (the marker-form `Scene.agent_original` rendered through
    `render_stat_blocks`), so a diff against `human_text` isn't noisy with stat-block markers. One row per
    `(scene_id, version)`: a re-edit refreshes `human_text` only, keeping the original agent draft so the
    pair never degrades into a human→human diff. This is the dataset every later learning tier reads.
    """

    __tablename__ = "edit_pairs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pov: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    human_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuleProposal(Base):
    """A distilled voice/dialogue rule proposed from the author's edits (LEARNING_FROM_EDITS Tier 3).

    A periodic, human-gated job: a review-model pass reads recent `EditPair` before→after rows and
    PROPOSES durable style rules (e.g. "trims filter verbs (saw/felt/noticed)"). The author
    approves/edits/rejects each. An accepted rule is appended to the POV's `PovProfile.voice_spec`
    (read fresh on the next draft), so it reaches the drafter through the same human gate as any edit.
    Advisory until accepted — nothing here changes a draft until the author says so. `pov` matches the
    chapter's narrating character (case-sensitive), exactly as `PovProfile.character` does.
    """

    __tablename__ = "rule_proposals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    pov: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # voice | dialogue
    rule_text: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # EditPair ids this batch was distilled from (provenance; stored as text, like exemplar_scene_ids).
    source_pair_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelOverride(Base):
    """Runtime model choice per agent role (e.g. setting_name='draft_model' -> 'claude-opus-4-8').
    Applied to the live `settings` on startup and whenever the Settings screen changes one, so picking
    Haiku/Sonnet/Opus per agent never needs a redeploy."""

    __tablename__ = "model_overrides"
    setting_name: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text)


class AgentPolicyOverride(Base):
    """Per-agent fallback/escalation policy persisted from the Agent Operations panel."""

    __tablename__ = "agent_policy_overrides"
    setting_name: Mapped[str] = mapped_column(Text, primary_key=True)
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class AgentOpsState(Base):
    """Singleton row tracking the active ops preset (custom when user edits individual agents)."""

    __tablename__ = "agent_ops_state"
    id: Mapped[str] = mapped_column(Text, primary_key=True, default="default")
    active_preset: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nullable to match production: this column was added to an existing table via a bare
    # `ALTER TABLE ... ADD COLUMN globals_json JSONB` (migrations.py) with no DEFAULT/backfill, so
    # legacy rows carry NULL. Typing it non-Optional was a schema lie (and made create_all diverge
    # from prod as NOT NULL). Readers guard with `... or {}`; `default=dict` fills new ORM inserts.
    globals_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict, nullable=True)


class AgentCustomPreset(Base):
    """User-saved snapshot of model tiers + policy overrides (agent-ops Phase 3)."""

    __tablename__ = "agent_custom_presets"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionRun(Base):
    """One full editorial production attempt for a chapter."""

    __tablename__ = "production_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    status: Mapped[str] = mapped_column(Text, default="queued")
    mode: Mapped[str] = mapped_column(Text, default="full_chapter")
    target_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hard_max_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    """One agent/model invocation or deterministic orchestration step within a production run."""

    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_runs.id"))
    agent_name: Mapped[str] = mapped_column(Text)
    agent_role: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="queued")
    stage: Mapped[str] = mapped_column(Text)
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    output_artifact_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base):
    """Versioned production artifact body plus provenance."""

    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("production_runs.id"), nullable=True)
    artifact_type: Mapped[str] = mapped_column(Text)
    domain_table: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(Text, default="active")
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(Text)
    created_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactDependency(Base):
    """Directed dependency edge between two production artifacts."""

    __tablename__ = "artifact_dependencies"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"))
    depends_on_artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"))
    dependency_kind: Mapped[str] = mapped_column(Text)
    dependency_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentEvent(Base):
    """Append-only event log for production-run replay and UI visibility."""

    __tablename__ = "agent_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_runs.id"))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(Base):
    """Central cross-surface activity feed — the single source of truth for the Activity drawer.

    Unlike AgentEvent (append-only, strictly scoped to one production run) this is the app-wide feed:
    every mutating surface (production, jobs, reviews, runs, the autonomous sweeper, retention) emits a
    row through `workers.activity.record_activity`, and the drawer reads them from `GET /activity`.
    Manual "Clear" sets `dismissed_at` (soft hide); the retention sweep hard-deletes old rows.
    """

    __tablename__ = "activities"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Monotonic insertion counter. Postgres now() is the transaction timestamp — CONSTANT within one
    # transaction — so activities emitted together (one request, one sweeper tick) share created_at and
    # can't be ordered by it. This identity column gives a stable newest-first order; the feed sorts on it.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)
    # Scope/link ids are all nullable — an activity may be book-wide, chapter-wide, run-scoped, or
    # job-scoped. production_run_id/job_id carry NO ForeignKey on purpose: manual delete and the
    # retention sweep remove runs/jobs out from under their activities, and a hard FK would either
    # block that delete or cascade-erase the audit trail. They are soft links the UI resolves best-effort.
    book_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("books.id"), nullable=True, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    production_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(Text)  # production|jobs|reviews|runs|sweeper|retention|canon|packets
    kind: Mapped[str] = mapped_column(Text)  # run_started|repair_applied|draft_done|sweeper_repair|...
    severity: Mapped[str] = mapped_column(Text, default="info")  # info | success | warn | error
    title: Mapped[str] = mapped_column(Text)  # one-line human summary
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # secondary line (e.g. an error)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Manual "Clear" soft-hides by stamping this; the retention sweep hard-deletes by created_at age.
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Issue(Base):
    """Structured validator finding that can be triaged into a repair task."""

    __tablename__ = "issues"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_runs.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    artifact_type: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[uuid.UUID] = mapped_column()
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validator: Mapped[str] = mapped_column(Text)
    issue_kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    span_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    span_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim: Mapped[str] = mapped_column(Text)
    contract_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    auto_repair_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="proposed")
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IssueDecision(Base):
    """Decision history for one issue."""

    __tablename__ = "issue_decisions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("issues.id"))
    decided_by: Mapped[str] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepairTask(Base):
    """Durable repair instruction bundle built from one or more accepted issues."""

    __tablename__ = "repair_tasks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_runs.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repair_kind: Mapped[str] = mapped_column(Text)
    # Blast radius of the repair. authority_level == "human_required" is the temporary A1b compatibility
    # discriminator (ADR-0031 D16) for manual-grant work: never autonomously approved regardless of the
    # sweeper ceiling — only a human "Approve & apply" can grant it. A1c makes authorization a first-class
    # axis orthogonal to this blast-radius field.
    authority_level: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued")
    issue_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    target_spans: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    instructions: Mapped[str] = mapped_column(Text)
    preserve: Mapped[list[str]] = mapped_column(JSONB, default=list)
    must_change: Mapped[list[str]] = mapped_column(JSONB, default=list)
    must_not_change: Mapped[list[str]] = mapped_column(JSONB, default=list)
    allowed_operations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    forbidden_operations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    word_delta_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requires_human_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    # A HUMAN approval audit stamp — written ONLY on a real human grant (the explicit Approve & apply),
    # never for the sweeper's autonomous authorization (ADR-0031 D16; the old "autonomous sweeper" false
    # stamp). A re-queued task (verify said NEEDS_ANOTHER_REPAIR) keeps its stamp — one human approval
    # covers the task's whole repair loop, not a single attempt.
    human_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RepairAttempt(Base):
    """One repair execution attempt, including the queued semantic patch request and revised prose."""

    __tablename__ = "repair_attempts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repair_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_tasks.id"))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(Text)
    patch_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    revised_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues_addressed: Mapped[list[str]] = mapped_column(JSONB, default=list)
    new_risks: Mapped[list[str]] = mapped_column(JSONB, default=list)
    word_count_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    word_count_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepairVerification(Base):
    """Verification verdict for one repair attempt."""

    __tablename__ = "repair_verifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    repair_attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repair_attempts.id"))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    verdict: Mapped[str] = mapped_column(Text)
    resolved_issue_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    remaining_issue_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    new_issues_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    target_issue_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    canon_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    scene_outcome_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    required_beats_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    reader_state_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    regression_score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DraftRunTimeline(Base):
    """Active sequential drafting memory for a production run.

    Updated after successful scene drafts within the run so that subsequent scenes in the
    ChapterSequence receive the prior exit state, spent beats, reader-learned facts, and
    "must not repeat" constraints. This is the live anti-branching brain (not just a snapshot).
    """

    __tablename__ = "draft_run_timelines"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    production_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_runs.id"))
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id"))
    current_scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_so_far_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_exit_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    spent_beats: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    reader_learned: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    pov_learned: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    must_not_repeat_after: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    drafted_scenes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
