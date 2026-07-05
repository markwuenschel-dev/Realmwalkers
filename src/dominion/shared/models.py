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
from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Book(Base):
    __tablename__ = "books"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    premise: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chapter(Base):
    """Owns POV (Game-of-Thrones model: one POV per whole chapter) and the outline."""

    __tablename__ = "chapters"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    chapter_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)  # plan-call proposes; author edits
    pov: Mapped[str] = mapped_column(Text)  # single narrating character
    outline: Mapped[str | None] = mapped_column(Text, nullable=True)  # input to beat-proposal
    status: Mapped[str] = mapped_column(Text, default="planned")
    # Reader-facing structural role (see ChapterKind): chapter | prologue | interlude | epilogue |
    # front_matter | back_matter. Display-only — ordering still keys off chapter_no; a plain "chapter"
    # renders "Chapter N", the rest render their own label. server_default keeps pre-existing rows valid.
    kind: Mapped[str] = mapped_column(Text, default="chapter", server_default="chapter")
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
    status: Mapped[str] = mapped_column(Text, default="active")  # active | stale | retired | superseded
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    doc_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(Text, nullable=True)


class CharacterState(Base):
    """Hard numbers. The Oracle's backing store. NEVER fuzzy-retrieved (DESIGN §5, §7)."""

    __tablename__ = "character_state"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
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
    reviewer: Mapped[str] = mapped_column(Text)  # continuity|combat|sensory|...
    severity: Mapped[str] = mapped_column(Text)  # info|warn|repair|block (legacy rows: hard == block)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # for continuity mismatches: {character, prose_value, ledger_value, context_sentence, span}
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


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
    globals_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


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
    # Stamped by the explicit Approve & apply action; a re-queued task (verify said NEEDS_ANOTHER_REPAIR)
    # keeps its stamp — one human approval covers the task's whole repair loop, not a single attempt.
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
