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
from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, Text, func
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
    pov: Mapped[str] = mapped_column(Text)                       # single narrating character
    outline: Mapped[str | None] = mapped_column(Text, nullable=True)  # input to beat-proposal
    status: Mapped[str] = mapped_column(Text, default="planned")


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
    gate_mode: Mapped[str] = mapped_column(Text)                 # pause_each | draft_ahead
    token_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """One unit of worker work: draft one scene OR one revision (DESIGN §4)."""
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(Text)                      # draft | revise_full | revise_pass
    target_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    target_pass: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="queued")
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    scene_no: Mapped[int] = mapped_column(Integer)
    characters_present: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)  # routes specialists
    expected_state_changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # declared deltas
    knowledge_injections: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    beat_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_words: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per-scene length guide
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
    status: Mapped[str] = mapped_column(Text, default="proposed")     # proposed | approved | blocked
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # green | yellow | red
    qa_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)  # approve|approve_warn|revise_required|block
    qa_warnings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)  # {residual_risks: [...]}
    # The full structured packet. `claims[]` carry provenance ({claim, source_strength, source_id,
    # source_title_or_file, excerpt?, confidence}), and `scene_seeds[]` carry a server-minted stable
    # `seed_id` (UUID) — the sync key for later contract derivation, NOT scene_no (display order).
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    open_questions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CanonEntity(Base):
    """Story bible / canon, retrievable via pgvector (DESIGN §7)."""
    __tablename__ = "canon_entities"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id"))
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)  # character|location|faction|lore|item
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)


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
    scope: Mapped[str] = mapped_column(Text)                     # pov | omniscient
    pov: Mapped[str | None] = mapped_column(Text, nullable=True)
    up_to_scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id"), nullable=True)
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class Critique(Base):
    """Advisory ONLY. Never changes scene.status. Never blocks the inbox (DESIGN §2, §9)."""
    __tablename__ = "critiques"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenes.id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewer: Mapped[str] = mapped_column(Text)                  # continuity|combat|sensory|...
    severity: Mapped[str] = mapped_column(Text)                  # info|warn|hard
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
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)   # relationship|mentorship|system|power|...
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
    decision: Mapped[str] = mapped_column(Text)                  # approve|deny|revise
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
    kind: Mapped[str] = mapped_column(Text)                       # voice | dialogue
    rule_text: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # EditPair ids this batch was distilled from (provenance; stored as text, like exemplar_scene_ids).
    source_pair_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
