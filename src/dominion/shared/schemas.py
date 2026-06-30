"""Pydantic DTOs — the wire contract for the API (exported via OpenAPI; see openapi.json)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from dominion.shared.enums import Decision, GateMode, RuleProposalStatus, SuggestionStatus


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CritiqueOut(_ORM):
    id: uuid.UUID
    reviewer: str
    severity: str
    note: str | None = None
    payload: dict[str, Any] | None = None
    scene_packet_id: uuid.UUID | None = None  # which scene contract this critique was raised against


class SceneOut(_ORM):
    id: uuid.UUID
    chapter_id: uuid.UUID
    scene_no: int
    version: int
    status: str
    scene_packet_id: uuid.UUID | None = None
    word_count: int | None = None
    length_status: str | None = None
    prose: str | None = None
    prose_source: str
    passes_run: list[str] | None = None
    token_count: int | None = None
    model: str | None = None
    created_at: datetime


class SceneDetail(SceneOut):
    critiques: list[CritiqueOut] = []
    is_exemplar: bool = False  # is this scene a curated voice exemplar for its POV? (Tier 2 learning)


class ExemplarIn(BaseModel):
    """Toggle a scene as a voice exemplar for its POV (LEARNING_FROM_EDITS Tier 2)."""
    enabled: bool


class DecisionIn(BaseModel):
    """POST body for approve / deny / revise (DESIGN §9)."""
    decision: Decision
    target_pass: str | None = None       # set to scope a revision to one specialist pass
    feedback: str | None = None
    edited_prose: str | None = None      # hand-edit in the inbox -> becomes canonical text


class RunIn(BaseModel):
    """POST body to start a generation run (DESIGN §8)."""
    book_id: uuid.UUID
    scope: dict[str, Any]                          # e.g. {"chapter": 4} or {"chapters": [3, 4, 5]}
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None


class ContinuityResolveIn(BaseModel):
    """Resolve one continuity mismatch from the panel: pick prose or ledger (DESIGN §9)."""
    critique_id: uuid.UUID
    choice: str                          # "use_prose" | "use_ledger" | "edit"


# --- Gate 1: books, runs, chapters, beats (DESIGN §4, §8) -----------------------------------------

class BookIn(BaseModel):
    """POST body to create a book."""
    title: str
    premise: str | None = None


class BookOut(_ORM):
    id: uuid.UUID
    title: str
    premise: str | None = None
    created_at: datetime


class BeatOut(_ORM):
    id: uuid.UUID
    chapter_id: uuid.UUID
    scene_seed_id: uuid.UUID | None = None       # set when the beat was derived from a packet scene_seed
    scene_packet_id: uuid.UUID | None = None     # the approved ScenePacket this beat projects
    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None                       # per-scene POV override; null inherits the chapter POV
    status: str


class BeatUpdateIn(BaseModel):
    """PUT body to edit a proposed beat (gate 1). Only provided fields are applied."""
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None                       # per-scene POV override; null/"" clears it (inherit chapter)


class BeatCreateIn(BaseModel):
    """POST body to add a beat by hand (a scene the planner didn't propose)."""
    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None                       # per-scene POV override; null inherits the chapter POV


class ApproveBeatsIn(BaseModel):
    """Optional POST body for approve: restrict to a subset of beats (those to draft now)."""
    beat_ids: list[uuid.UUID] | None = None


class HumanSceneIn(BaseModel):
    """POST body to write a manuscript section by hand — lands APPROVED so it flows into context."""
    scene_no: int
    prose: str


class RedraftIn(BaseModel):
    """POST body to re-draft existing scenes: re-queue a draft for each (supersedes the current version)."""
    scene_ids: list[uuid.UUID]


class ChapterOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_no: int
    title: str | None = None
    pov: str
    outline: str | None = None
    status: str


class ChapterUpdateIn(BaseModel):
    """PATCH body to edit a chapter's authored fields (currently just the title). Only provided
    fields are applied (mirrors BeatUpdateIn / ThreadUpdateIn)."""
    title: str | None = None


class RunStartIn(BaseModel):
    """POST body to start a run: outline a chapter; the planner proposes its beats (gate 1).

    Re-running for the same chapter re-proposes (replaces the chapter's still-proposed beats).
    """
    book_id: uuid.UUID
    chapter_no: int
    pov: str
    outline: str
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None
    max_beats: int | None = None       # cap how many scenes the planner proposes
    target_words: int | None = None    # default per-scene length stamped on each proposed beat


class RunStartOut(BaseModel):
    """Result of starting a run: the chapter and its proposed (unapproved) beats."""
    run_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_no: int
    pov: str
    beats: list[BeatOut] = []


# --- Multi-chapter batch (propose several chapters in one request, optional auto-draft) ------------

class BatchChapterSpec(BaseModel):
    """One chapter to propose in a batch run (mirrors RunStartIn's per-chapter fields)."""
    chapter_no: int
    pov: str
    outline: str
    max_beats: int | None = None       # ceiling on proposed scenes (planner won't pad to it)
    target_words: int | None = None    # default per-scene length stamped on each proposed beat


class BatchRunStartIn(BaseModel):
    """POST body to propose beats for SEVERAL chapters in one request. When `auto_draft` is set, each
    chapter's proposed beats are approved and draft jobs are queued immediately (skips gate-1 review)."""
    book_id: uuid.UUID
    chapters: list[BatchChapterSpec]
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None
    auto_draft: bool = False


class BatchChapterResultOut(BaseModel):
    """Per-chapter outcome of a batch run, for inline display in the Planner."""
    chapter_id: uuid.UUID
    chapter_no: int
    pov: str
    beat_count: int = 0
    queued_jobs: int = 0               # draft jobs enqueued (only when auto_draft)


class BatchRunOut(BaseModel):
    """Result of a batch run: one row per requested chapter."""
    run_id: uuid.UUID
    results: list[BatchChapterResultOut] = []


# --- Contract-first drafting: chapter knowledge packets (Phase 1) ---------------------------------

class PacketOut(_ORM):
    """A chapter knowledge packet for the Desk review panel. `body` is the full structured packet
    (claims with provenance, scene seeds with stable ids, locks, risks); `qa_warnings` carries the
    Packet QA verdict's residual risks + issues; `open_questions` are items the human must adjudicate."""
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    status: str                                  # proposed | approved | blocked
    confidence: str | None = None                # green | yellow | red
    qa_verdict: str | None = None
    qa_warnings: dict[str, Any] | None = None
    body: dict[str, Any] = {}
    open_questions: dict[str, Any] | None = None
    created_at: datetime
    can_approve: bool = False
    approval_blockers: list[str] = []


class PacketUpdateIn(BaseModel):
    """PUT body to adjudicate/edit a proposed packet. Only provided fields are applied. The human
    edits the body, clears open questions, and may raise the confidence after reviewing flags."""
    body: dict[str, Any] | None = None
    open_questions: dict[str, Any] | None = None
    confidence: str | None = None                # green | yellow | red


class PacketProposeOut(BaseModel):
    """Status of an in-flight packet proposal. The author+QA run in the background (so the browser
    never hangs); the Desk polls and shows the live phase ('authoring' -> 'qa'). `running` flips to
    False when the packet is persisted — that's the cue to refetch it via GET."""
    running: bool
    phase: str | None = None        # authoring | qa | None
    elapsed_s: int | None = None


# --- Contract-first drafting: scene packets (scene-local contract) --------------------------------

class ScenePacketOut(_ORM):
    """A derived scene-local contract for the Desk. `body` follows the ScenePacket body contract
    (reader/POV knowledge state, allowed/forbidden reveals, intentional mysteries, false-positive
    traps, word budget); `qa_warnings` carries the ScenePacket QA verdict's residual risks + issues."""
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_packet_id: uuid.UUID
    scene_seed_id: uuid.UUID | None = None
    scene_no: int
    status: str                                  # proposed | approved | blocked | stale
    qa_verdict: str | None = None
    qa_warnings: dict[str, Any] | None = None
    body: dict[str, Any] = {}
    source_hash: str | None = None
    stale_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    can_approve: bool = False
    approval_blockers: list[str] = []


class ScenePacketUpdateIn(BaseModel):
    """PUT body to edit/adjudicate a scene packet. Editing the body after approval returns it to
    proposed unless status is explicitly set back to approved in the same call."""
    body: dict[str, Any] | None = None
    status: str | None = None


class ScenePacketDeriveOut(BaseModel):
    """Result of deriving scene packets for a chapter from its approved ChapterPacket."""
    created: int = 0
    updated: int = 0
    blocked: int = 0
    stale: int = 0
    packets: list[ScenePacketOut] = []
    context_budget_report: dict[str, Any] | None = None


class ScenePacketApproveIn(BaseModel):
    """Optional POST body for batch approve: restrict to a subset of packets."""
    packet_ids: list[uuid.UUID] | None = None


class ScenePacketDeriveStatusOut(BaseModel):
    """Status of an in-flight scene-packet derivation (the ScenePacket Author + QA run per scene in the
    background, so a large chapter never hangs the request). The Desk polls and refetches the list when
    `running` flips False. `result` carries the counts once the run finishes."""
    running: bool
    phase: str | None = None        # deriving | None
    elapsed_s: int | None = None
    result: ScenePacketDeriveOut | None = None


class ScenePacketQaOut(BaseModel):
    """Result of running QA against one scene packet."""
    packet_id: uuid.UUID
    verdict: str
    warnings: dict[str, Any] | None = None


# --- LLM call telemetry (persisted per-call cost/cache, aggregated for the Desk) -------------------

class TelemetryTotals(BaseModel):
    """Aggregated cost/cache/health over a set of LLM calls (one scene, chapter, stage, model, or all)."""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cache_hit_ratio: float = 0.0          # cache_read / total prompt tokens
    cache_tokens_saved: int = 0           # ~90% of cache_read tokens (cached reads bill at ~10%)
    truncations: int = 0                  # calls cut off at max_tokens
    errors: int = 0                       # calls that recorded a failure
    avg_latency_ms: int | None = None


class SceneTelemetryOut(TelemetryTotals):
    """One scene's derive telemetry (Author + QA calls), for the per-chapter panel."""
    scene_no: int | None = None
    models: list[str] = []


class ChapterTelemetryOut(BaseModel):
    """Per-chapter derive telemetry: chapter totals + a per-scene breakdown."""
    chapter_id: uuid.UUID
    totals: TelemetryTotals = TelemetryTotals()
    scenes: list[SceneTelemetryOut] = []


class TelemetryGroupOut(TelemetryTotals):
    """A named aggregation bucket for the global tab (by stage or by model)."""
    key: str = ""


class ChapterRollupOut(TelemetryTotals):
    """One chapter's totals for the global cross-chapter comparison."""
    chapter_id: uuid.UUID
    chapter_no: int | None = None
    title: str | None = None


class RunRollupOut(TelemetryTotals):
    """One derive run's totals (all calls sharing a run_id), for the per-run history table. `started_at`
    is the run's earliest call; `chapter_no`/`title` label which chapter the run derived."""
    run_id: uuid.UUID | None = None
    started_at: datetime | None = None
    chapter_id: uuid.UUID | None = None
    chapter_no: int | None = None
    title: str | None = None


class BookTelemetryOut(BaseModel):
    """Global telemetry for a book: overall totals plus comparison rollups across chapters, stages,
    and models — the cross-chapter/scene view the global Telemetry tab renders."""
    totals: TelemetryTotals = TelemetryTotals()
    by_chapter: list[ChapterRollupOut] = []
    by_run: list[RunRollupOut] = []
    run_total: int = 0                    # total run rows before limit/offset slicing (for "load older")
    by_stage: list[TelemetryGroupOut] = []
    by_model: list[TelemetryGroupOut] = []


# --- History + manuscript read surfaces (DESIGN §9, §13) ------------------------------------------

class SceneVersionOut(SceneOut):
    """A scene row plus its preserved pre-edit text, for version diffing in History."""
    agent_original: str | None = None


class DraftAttemptOut(_ORM):
    """One preserved stage of a scene's prose pipeline (provenance: raw → enrichment → length → final)."""
    id: uuid.UUID
    stage: str
    word_count: int | None = None
    model: str | None = None
    prose: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class ManuscriptScene(BaseModel):
    scene_no: int
    prose: str | None = None


class ManuscriptChapter(BaseModel):
    chapter_no: int
    title: str | None = None
    pov: str
    scenes: list[ManuscriptScene] = []


class ManuscriptOut(BaseModel):
    """The approved manuscript, assembled in reading order (latest approved version per scene)."""
    book_id: uuid.UUID
    title: str
    chapters: list[ManuscriptChapter] = []


# --- Drafting (browser-driven worker) -------------------------------------------------------------

class ActiveScene(BaseModel):
    chapter_no: int | None = None
    scene_no: int | None = None
    # What the worker is doing right now ("drafting prose", "enriching · combat", "reviewing"), plus
    # how long it's been on this scene — so the Desk shows live progress, not a frozen spinner. Both
    # come from the in-process phase registry (workers/progress.py); null when unknown.
    phase: str | None = None
    elapsed_s: int | None = None
    # Cache performance for the scene just completed (null while the scene is still drafting).
    cache_hit_ratio: float | None = None
    total_cache_read_tokens: int | None = None
    total_cache_creation_tokens: int | None = None


class JobsStatusOut(BaseModel):
    """Live queue state, so the Desk can show a 'drafting…' indicator without a terminal."""
    running: bool = False
    queued: int = 0
    failed: int = 0
    active_scene: ActiveScene | None = None
    # Cache stats for the most recently completed scene — persists during the idle window so the
    # Desk can show cache efficiency after drafting finishes, not just while it's running.
    last_cache_hit_ratio: float | None = None
    last_cache_read_tokens: int | None = None
    last_cache_creation_tokens: int | None = None
    last_cache_tokens_saved: int | None = None


class DraftNextOut(BaseModel):
    scheduled: bool = False
    queued: int = 0
    running: bool = False


class RetryFailedOut(BaseModel):
    """Result of re-queuing FAILED jobs (e.g. after a transient outage or topping up API credits)."""
    requeued: int = 0
    scheduled: bool = False
    queued: int = 0
    running: bool = False


class FailedJobOut(BaseModel):
    """A FAILED job + why it died, so the Desk can show the actual error instead of a generic note."""
    id: uuid.UUID
    chapter_no: int | None = None
    scene_no: int | None = None
    last_error: str | None = None


# --- World ledger + in-prose entity cards (DESIGN §5, §7) -----------------------------------------

class CharacterStateOut(BaseModel):
    """Hard numbers from the Oracle (CharacterState), with the canon body if the character has one."""
    character: str
    stats: dict[str, Any] = {}
    provisional: bool = False
    is_pov: bool = False
    body: str | None = None


class CanonEntityOut(_ORM):
    id: uuid.UUID
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CanonEntityIn(BaseModel):
    """Create a canon entity (location/faction/item/lore/…). Re-embedded on write for retrieval."""
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CanonEntityUpdateIn(BaseModel):
    """Edit a canon entity. Only provided fields are applied; body changes trigger a re-embed."""
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CharacterStateIn(BaseModel):
    """Seed/replace a character's Oracle stats (absolute values, not deltas), with an optional canon
    description. `stats` overwrites the stored stat block; `body` upserts a kind='character' canon
    entity so the hover-card / Ledger body and RAG see it too."""
    stats: dict[str, Any] = {}
    body: str | None = None


class CanonIngestOut(BaseModel):
    """Result of rebuilding the retrieval index from the on-disk canon docs."""
    indexed: int


class KnowledgeFactOut(_ORM):
    """A discrete story fact + who knows it when (scene-packet knowledge ledger)."""
    id: uuid.UUID
    book_id: uuid.UUID
    fact: str
    status: str
    known_by_character: str | None = None
    source_scene_id: uuid.UUID | None = None
    known_by_reader_after_scene_id: uuid.UUID | None = None
    known_by_character_after_scene_id: uuid.UUID | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


# --- World threads (curated arcs across scenes) ---------------------------------------------------

class ThreadBeatOut(_ORM):
    id: uuid.UUID
    scene_no: int
    label: str | None = None
    flag: bool = False


class ThreadOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None
    beats: list[ThreadBeatOut] = []


class ThreadIn(BaseModel):
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None


class ThreadUpdateIn(BaseModel):
    """Only provided fields are applied (mirrors BeatUpdateIn)."""
    name: str | None = None
    kind: str | None = None
    state: str | None = None
    note: str | None = None


class ThreadBeatIn(BaseModel):
    scene_no: int
    label: str | None = None
    flag: bool = False


# --- Scene markup: annotations (margin notes) + suggestions (tracked changes) ---------------------

class AnnotationOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str | None = None
    author: str | None = None
    note: str | None = None
    created_at: datetime


class AnnotationIn(BaseModel):
    note: str
    quote: str | None = None
    author: str | None = None


class SuggestionOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str
    new_text: str | None = None
    author: str | None = None
    why: str | None = None
    status: str
    created_at: datetime


class SuggestionIn(BaseModel):
    quote: str
    new_text: str | None = None
    author: str | None = None
    why: str | None = None


class SuggestionDecisionIn(BaseModel):
    status: SuggestionStatus


# --- distilled voice/dialogue rules (LEARNING_FROM_EDITS Tier 3) -----------------------------------

class RuleProposalOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    pov: str
    kind: str                                    # voice | dialogue
    rule_text: str
    rationale: str | None = None
    source_pair_ids: list[str] | None = None
    status: str                                  # pending | accepted | rejected
    created_at: datetime


class RuleProposalDecisionIn(BaseModel):
    """Accept or reject a proposed rule. On accept, `rule_text` (if set) replaces the proposed text,
    so the author can edit a rule before it lands in the POV's voice spec."""
    status: RuleProposalStatus
    rule_text: str | None = None


# --- canon / planning / style docs (Domain-B markdown, read-only) ---------------------------------
class DocMeta(BaseModel):
    path: str  # id, relative to the docs root (e.g. "canon/timeline/master_timeline.md")
    title: str  # first "# " heading, else a humanised filename
    category: str  # top-level folder: "canon" | "planning" | "style"


class DocOut(DocMeta):
    content: str  # raw markdown


# --- runtime model selection (Settings screen) ---------------------------------------------------

class ModelSettingOut(BaseModel):
    """One customizable agent: its current model id + which tier (haiku/sonnet/opus) that is."""
    setting: str
    label: str
    description: str
    model: str
    tier: str | None = None


class ModelSettingsOut(BaseModel):
    agents: list[ModelSettingOut]
    tiers: dict[str, str]  # tier name -> the model id it maps to


class ModelSettingUpdateIn(BaseModel):
    """PUT body to point one agent role at a tier."""
    setting: str
    tier: str  # haiku | sonnet | opus
