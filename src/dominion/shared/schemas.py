"""Pydantic DTOs — the wire contract for the API (exported via OpenAPI; see openapi.json)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from dominion.shared.enums import ChapterKind, Decision, GateMode, RuleProposalStatus, SuggestionStatus


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
    target_pass: str | None = None  # set to scope a revision to one specialist pass
    feedback: str | None = None
    edited_prose: str | None = None  # hand-edit in the inbox -> becomes canonical text


class RunIn(BaseModel):
    """POST body to start a generation run (DESIGN §8)."""

    book_id: uuid.UUID
    scope: dict[str, Any]  # e.g. {"chapter": 4} or {"chapters": [3, 4, 5]}
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None


class ContinuityResolveIn(BaseModel):
    """Resolve one continuity mismatch from the panel: pick prose or ledger (DESIGN §9)."""

    critique_id: uuid.UUID
    choice: str  # "use_prose" | "use_ledger" | "edit"


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
    scene_seed_id: uuid.UUID | None = None  # set when the beat was derived from a packet scene_seed
    scene_packet_id: uuid.UUID | None = None  # the approved ScenePacket this beat projects
    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None  # per-scene POV override; null inherits the chapter POV
    status: str


class BeatUpdateIn(BaseModel):
    """PUT body to edit a proposed beat (gate 1). Only provided fields are applied."""

    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None  # per-scene POV override; null/"" clears it (inherit chapter)


class BeatCreateIn(BaseModel):
    """POST body to add a beat by hand (a scene the planner didn't propose)."""

    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    pov: str | None = None  # per-scene POV override; null inherits the chapter POV


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
    kind: str = "chapter"  # ChapterKind value; str (like status) tolerates any legacy row
    epigraph: str | None = None


class ChapterUpdateIn(BaseModel):
    """PATCH body to edit a chapter's authored fields (title, structural kind, epigraph). Only
    provided fields are applied (mirrors BeatUpdateIn / ThreadUpdateIn) — send `epigraph: null` to
    clear it. `kind` is validated against ChapterKind."""

    title: str | None = None
    kind: ChapterKind | None = None
    epigraph: str | None = None


class ChapterCreateIn(BaseModel):
    """POST body to create/update a chapter's POV + outline, with no LLM beat-proposal call — the
    contract-first entry point (create the chapter, then POST its /packet to author the chapter
    packet). Upserts by (book_id, chapter_no); a best-effort title is generated server-side."""

    book_id: uuid.UUID
    chapter_no: int
    pov: str
    outline: str


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
    max_beats: int | None = None  # cap how many scenes the planner proposes
    target_words: int | None = None  # default per-scene length stamped on each proposed beat


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
    max_beats: int | None = None  # ceiling on proposed scenes (planner won't pad to it)
    target_words: int | None = None  # default per-scene length stamped on each proposed beat


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
    queued_jobs: int = 0  # draft jobs enqueued (only when auto_draft)


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
    status: str  # proposed | approved | blocked
    confidence: str | None = None  # green | yellow | red
    qa_verdict: str | None = None
    qa_warnings: dict[str, Any] | None = None
    body: dict[str, Any] = {}
    open_questions: dict[str, Any] | None = None
    created_at: datetime
    can_approve: bool = False
    approval_blockers: list[str] = []
    blocked_reason: str | None = None
    blocker_source: str | None = None
    blocker_kind: str | None = None
    recovery_actions: list[str] | None = None
    blocker_diagnostics: dict[str, Any] | None = None


class PacketUpdateIn(BaseModel):
    """PUT body to adjudicate/edit a proposed packet. Only provided fields are applied. The human
    edits the body, clears open questions, and may raise the confidence after reviewing flags."""

    body: dict[str, Any] | None = None
    open_questions: dict[str, Any] | None = None
    confidence: str | None = None  # green | yellow | red


class PacketProposeOut(BaseModel):
    """Status of an in-flight packet proposal. The author+QA run in the background (so the browser
    never hangs); the Desk polls and shows the live phase ('authoring' -> 'qa'). `running` flips to
    False when the packet is persisted — that's the cue to refetch it via GET."""

    running: bool
    phase: str | None = None  # authoring | qa | None
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
    status: str  # proposed | approved | blocked | stale
    qa_verdict: str | None = None
    qa_warnings: dict[str, Any] | None = None
    body: dict[str, Any] = {}
    # Canon/owner snippets this packet was derived from (handle -> doc_path/heading/score/reason), so the
    # editor can show "built from these sources" and resolve the author's claim_sources handles.
    sources: list[dict[str, Any]] | None = None
    source_hash: str | None = None
    stale_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    can_approve: bool = False
    approval_blockers: list[str] = []
    blocked_reason: str | None = None
    blocker_source: str | None = None  # author | qa | derive | unknown


class ScenePacketSummaryOut(BaseModel):
    """Slim list row for the Desk scene-packet list: statuses and counters only — never the contract
    body, QA report, or sources (those load per-packet via GET /scene-packets/{id} when a card opens).
    Keeps the list fetch small enough that tab switches render from a summary payload. The three
    status axes are DELIBERATELY separate: `status` is contract lifecycle, `qa_verdict` is the advisory
    QA opinion, `prose_state` is whether drafted prose exists — the UI must not merge them."""

    id: uuid.UUID
    chapter_id: uuid.UUID
    scene_no: int
    status: str  # proposed | approved | blocked | stale | rate_limited
    qa_verdict: str | None = None
    stale_reason: str | None = None
    can_approve: bool = False
    approval_blockers: list[str] = []
    blocked_reason: str | None = None
    blocker_source: str | None = None  # author | validation | qa | derive | rate_limit | unknown
    # True when the persisted body is a usable scene contract (drives "re-run QA" availability
    # without shipping the body itself).
    body_valid: bool = False
    # {"block": n, "repair": n, "warn": n} from persisted deterministic violations.
    violation_counts: dict[str, int] = {}
    issue_count: int = 0
    prose_state: str = "missing"  # missing | drafting | drafted | failed
    updated_at: datetime | None = None


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
    # Scenes whose author/QA call was refused by the provider (429 past retries) — transient
    # infrastructure, retriable; NOT counted as blocked.
    rate_limited: int = 0
    # Approved packets whose inputs were unchanged (source_hash match) — a re-derive skips them, and
    # the Desk says so instead of looking like it did nothing.
    skipped: int = 0
    # NOTE: the derive/status poll returns this EMPTY — the Desk refetches the list itself, and
    # embedding every full contract body made each 1.5s poll a ~100KB download.
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
    phase: str | None = None  # deriving | None
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
    cache_hit_ratio: float = 0.0  # cache_read / total prompt tokens
    cache_tokens_saved: int = 0  # ~90% of cache_read tokens (cached reads bill at ~10%)
    truncations: int = 0  # calls cut off at max_tokens
    errors: int = 0  # calls that recorded a failure
    fallbacks: int = 0  # calls marked fallback_attempt in metadata
    avg_latency_ms: int | None = None
    estimated_cost_usd: float = 0.0
    cache_savings_usd: float = 0.0


class PipelineStepOut(TelemetryTotals):
    """One stage in a scene/run pipeline timeline."""

    stage: str = ""


class SceneTelemetryOut(TelemetryTotals):
    """One scene's derive telemetry (Author + QA calls), for the per-chapter panel."""

    scene_no: int | None = None
    models: list[str] = []
    status: str = "ok"  # ok | warn | error
    stages: list[str] = []
    worst_latency_ms: int | None = None
    stage_summary: str = ""
    pipeline: list[PipelineStepOut] = []


class ChapterTelemetryOut(BaseModel):
    """Per-chapter derive telemetry: chapter totals + a per-scene breakdown."""

    chapter_id: uuid.UUID
    run_id: uuid.UUID | None = None
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
    run_total: int = 0  # total run rows before limit/offset slicing (for "load older")
    by_stage: list[TelemetryGroupOut] = []
    by_model: list[TelemetryGroupOut] = []


class LlmCallLinksOut(BaseModel):
    """Navigation targets for a telemetry call row."""

    scene_packet_id: uuid.UUID | None = None
    scene_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    chapter_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None


class LlmCallOut(BaseModel):
    """One persisted LLM call — the atomic unit for drill-down drawers."""

    id: uuid.UUID
    run_id: uuid.UUID | None = None
    book_id: uuid.UUID | None = None
    chapter_id: uuid.UUID | None = None
    scene_no: int | None = None
    scene_seed_id: uuid.UUID | None = None
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    truncated: bool = False
    latency_ms: int | None = None
    error: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    estimated_cost_usd: float = 0.0
    links: LlmCallLinksOut = LlmCallLinksOut()


class LlmCallListOut(BaseModel):
    calls: list[LlmCallOut] = []
    total: int = 0
    limit: int = 50
    offset: int = 0


class RunTelemetryOut(BaseModel):
    """Full drill-down for one telemetry run (all calls sharing a run_id)."""

    run_id: uuid.UUID
    started_at: datetime | None = None
    chapter_id: uuid.UUID | None = None
    chapter_no: int | None = None
    title: str | None = None
    totals: TelemetryTotals = TelemetryTotals()
    by_stage: list[TelemetryGroupOut] = []
    by_model: list[TelemetryGroupOut] = []
    scenes: list[SceneTelemetryOut] = []
    calls: list[LlmCallOut] = []
    settings_snapshot: dict[str, Any] | None = None


class TelemetryProblemOut(BaseModel):
    kind: str
    severity: str  # info | warn | error
    summary: str
    count: int = 0
    breakdown: list[dict[str, Any]] = []
    recommended_action: str = ""
    drill_down: dict[str, Any] = {}


class TelemetryProblemsOut(BaseModel):
    problems: list[TelemetryProblemOut] = []
    healthy: bool = True


class TelemetryDeleteOut(BaseModel):
    deleted_calls: int


class GlobalTelemetryDeleteIn(BaseModel):
    confirm: str


class StageDeltaOut(BaseModel):
    stage: str
    calls_delta: int = 0
    input_tokens_delta: int = 0
    output_tokens_delta: int = 0
    truncations_delta: int = 0


class RunCompareOut(BaseModel):
    run_a: RunRollupOut
    run_b: RunRollupOut
    stage_deltas: list[StageDeltaOut] = []


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
    kind: str = "chapter"
    epigraph: str | None = None
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
    """Result of re-queuing FAILED jobs (contract-first: reconciles fresh ScenePackets)."""

    requested: int = 0
    requeued: int = 0
    scheduled: bool = False
    queued: int = 0
    running: bool = False
    skipped: list[DraftQueueBlockerOut] = []


class ClearFailedOut(BaseModel):
    """Result of purging FAILED draft jobs without re-queueing."""

    purged: int = 0
    failed: int = 0


class DeleteSceneOut(BaseModel):
    """Result of hard-deleting one scene version."""

    deleted: uuid.UUID
    jobs_purged: int = 0


class DeleteChapterPacketOut(BaseModel):
    """Result of clearing chapter packets (and their scene packets) for one chapter."""

    deleted_chapter_packets: int = 0
    deleted_scene_packets: int = 0


class DeleteScenePacketOut(BaseModel):
    """Result of hard-deleting one scene packet."""

    deleted: uuid.UUID
    jobs_purged: int = 0


class DeleteScenePacketsOut(BaseModel):
    """Result of clearing scene packets for one chapter."""

    deleted: int = 0
    jobs_purged: int = 0


class ClearDraftScenesOut(BaseModel):
    """Result of removing all non-approved scenes (draft compile reset)."""

    purged: int = 0
    jobs_purged: int = 0


class DraftQueueBlockerOut(BaseModel):
    chapter_id: uuid.UUID
    scene_no: int | None = None
    beat_id: uuid.UUID | None = None
    scene_packet_id: uuid.UUID | None = None
    reason: str
    message: str
    required_action: str


class DraftScheduleOut(BaseModel):
    chapter_id: uuid.UUID
    queued_job_ids: list[uuid.UUID]
    queued: int = 0
    skipped: list[DraftQueueBlockerOut] = []
    repaired_beats: int = 0


class DraftReadinessOut(BaseModel):
    """Contract-first drafting gate diagnostics. `draftable` is the single gate the Draft button obeys;
    `disabled_reason` names the FIRST failing condition in plain language so the UI never shows a
    disabled button (or a "ready to draft" claim) without an explanation. `prose` reports scene prose
    coverage — `assembly_ready` is the production-assembly gate (all expected scenes have prose)."""

    chapter_id: uuid.UUID
    chapter_packet_approved: bool = False
    scene_packets: dict[str, object] = {}
    beats: dict[str, object] = {}
    jobs: dict[str, object] = {}
    prose: dict[str, object] = {}
    draftable: bool = False
    disabled_reason: str | None = None
    blockers: list[DraftQueueBlockerOut] = []


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
    # Provenance + lifecycle (Workstream H). `source`: manual | repo_ingested | packet_derived |
    # draft_derived | legacy. `status`: active | stale | retired | superseded (only `active` reaches RAG).
    source: str = "manual"
    status: str = "active"


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
    """Result of a canon ingest/rebuild from on-disk docs (series/canon).

    For the Ledger "Clean rebuild from docs" (hard path): repo-ingested rows
    (doc_path IS NOT NULL) are deleted first, then re-ingested from current files.
    `retired` counts prior repo rows removed. `indexed` is the fresh count.
    `total` is the resulting live repo-ingested corpus size.
    """

    indexed: int
    skipped: int = 0
    retired: int = 0
    total: int | None = None


class CanonCleanupIn(BaseModel):
    """Select canon rows for a cleanup action (preview / retire / bulk delete) — Workstream H.

    Rows are chosen by explicit `ids` OR by (`source_filter`, `status_filter`); with neither, nothing
    matches (fail-safe — no accidental mass purge). Manual-source rows are PROTECTED: a filter never
    retires/deletes them; you must list their id explicitly. `dry_run` is honoured by the preview route
    (always a dry run) and ignored by retire/delete (which always mutate).
    """

    ids: list[uuid.UUID] | None = None
    source_filter: str | None = None  # manual | repo_ingested | packet_derived | draft_derived | legacy | all
    status_filter: str | None = None  # active | stale | retired | superseded | all
    dry_run: bool = True


class CanonCleanupItemOut(BaseModel):
    """One row in a cleanup preview, with why it is (or isn't) actionable."""

    id: uuid.UUID
    kind: str | None = None
    name: str | None = None
    source: str = "manual"
    status: str = "active"
    summary: str | None = None  # first ~120 chars of the body, for the confirm dialog
    reason: str  # "eligible" | "protected: manual source (list id to override)" | "already retired"


class CanonCleanupPreviewOut(BaseModel):
    """Dry-run report of what a retire/delete over the selection would do (mutates nothing)."""

    dry_run: bool = True
    matched: int = 0  # rows the selection matched
    would_retire: int = 0  # actionable rows not already retired (soft retire target)
    would_delete: int = 0  # actionable rows (hard delete target)
    protected_manual: int = 0  # matched manual rows skipped because their id wasn't listed
    items: list[CanonCleanupItemOut] = []


class CanonRetireOut(BaseModel):
    """Result of a soft retire (status -> 'retired'); retired rows drop out of RAG + `?status=active`."""

    retired: int = 0
    protected_manual: int = 0


class CanonBulkDeleteOut(BaseModel):
    """Result of a hard bulk delete of canon rows."""

    deleted: int = 0
    protected_manual: int = 0


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
    kind: str  # voice | dialogue
    rule_text: str
    rationale: str | None = None
    source_pair_ids: list[str] | None = None
    status: str  # pending | accepted | rejected
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
    """One customizable agent: its current model id + which provider/tier that is."""

    setting: str
    label: str
    description: str
    model: str
    tier: str | None = None
    provider: str = "anthropic"


class ModelSettingsOut(BaseModel):
    agents: list[ModelSettingOut]
    tiers: dict[str, str]  # legacy: Anthropic tier name -> the model id it maps to
    provider_tiers: dict[str, dict[str, str]] = {}  # provider id -> {tier name -> model id}


class ModelSettingUpdateIn(BaseModel):
    """PUT body to point one agent role at a provider + tier."""

    setting: str
    tier: str  # haiku | sonnet | opus
    provider: str = "anthropic"


# --- agent operations panel ----------------------------------------------------------------------


class EscalationRuleOut(BaseModel):
    trigger: str
    description: str


class AgentContractOut(BaseModel):
    inputs: list[str]
    outputs: list[str]
    temperature: float | None = None
    max_retries: int = 3
    context_load: str = ""
    uses_memory: bool = False
    writes_artifacts: bool = False
    requires_approval: bool = False


class AgentPermissionsOut(BaseModel):
    auto_run: bool = True
    require_approval: bool = False
    can_modify_packet: bool = False
    can_block_downstream: bool = False
    can_write_summaries: bool = False
    can_update_canon: bool = False
    can_only_suggest: bool = True


class AgentPermissionsPatchIn(BaseModel):
    auto_run: bool | None = None
    require_approval: bool | None = None
    can_modify_packet: bool | None = None
    can_block_downstream: bool | None = None
    can_write_summaries: bool | None = None
    can_update_canon: bool | None = None
    can_only_suggest: bool | None = None


class AgentEstimateOut(BaseModel):
    cost_band: str
    speed_band: str
    typical_calls_per_chapter: int
    estimated_usd_per_chapter: float | None = None
    estimated_latency_sec_per_chapter: int | None = None


class AgentPolicyOut(BaseModel):
    setting: str
    primary_tier: str | None = None
    primary_model: str
    fallback_tier: str | None = None
    fallback_model: str | None = None
    fallback_provider: str | None = None
    never_fallback: list[str] = []
    escalation_rules: list[EscalationRuleOut] = []
    semantic_escalation: bool = True
    quality_level: str = "balanced"


class AgentPresetOut(BaseModel):
    id: str
    label: str
    description: str
    cost_band: str
    latency_band: str
    best_for: str
    is_custom: bool = False


class AgentOpsAgentOut(BaseModel):
    setting: str
    label: str
    description: str
    model: str
    tier: str | None = None
    provider: str = "anthropic"
    policy: AgentPolicyOut
    contract: AgentContractOut
    permissions: AgentPermissionsOut
    estimate: AgentEstimateOut
    warnings: list[str] = []


class PipelineEstimateOut(BaseModel):
    cost_band: str
    latency_band: str
    summary: str
    opus_calls: int
    sonnet_calls: int
    haiku_calls: int
    total_estimated_calls: int
    estimated_usd_per_chapter: float | None = None
    estimated_usd_low_per_chapter: float | None = None
    estimated_latency_sec_per_chapter: int | None = None


class AgentGlobalsOut(BaseModel):
    scene_token_budget: int
    scene_time_budget_s: int


class AgentGlobalsUpdateIn(BaseModel):
    scene_token_budget: int | None = None
    scene_time_budget_s: int | None = None


class CustomPresetCreateIn(BaseModel):
    label: str
    description: str | None = None


class AgentOpsOut(BaseModel):
    active_preset: str | None
    presets: list[AgentPresetOut]
    agents: list[AgentOpsAgentOut]
    pipeline_estimate: PipelineEstimateOut
    tiers: dict[str, str]
    provider_tiers: dict[str, dict[str, str]] = {}
    globals: AgentGlobalsOut


class AgentPolicyUpdateIn(BaseModel):
    fallback_tier: str | None = None
    fallback_provider: str | None = None
    never_fallback: list[str] | None = None
    semantic_escalation: bool | None = None
    quality_level: str | None = None  # fast | balanced | quality
    permissions: AgentPermissionsPatchIn | None = None


class AgentStatsOut(BaseModel):
    setting: str
    label: str
    calls: int = 0
    avg_latency_ms: int | None = None
    avg_tokens: int | None = None
    escalation_rate: float | None = None
    error_rate: float | None = None
    truncation_rate: float | None = None
    qa_pass_rate: str | None = None  # "—" until Phase 2


class AgentStatsListOut(BaseModel):
    agents: list[AgentStatsOut]
    window_runs: int


class SmokeTestCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class SmokeTestAgentOut(BaseModel):
    setting: str
    label: str
    passed: bool
    checks: list[SmokeTestCheckOut]


class SmokeTestOut(BaseModel):
    results: list[SmokeTestAgentOut]
    all_passed: bool
    mode: str = "offline"
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    live_warning: str | None = None


class SmokeTestIn(BaseModel):
    agents: list[str] | None = None  # subset of setting keys; None = all
    live: bool = False


class ChapterSequenceOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_packet_id: uuid.UUID
    status: str
    target_words: int | None = None
    max_words: int | None = None
    hard_max_words: int | None = None
    target_scene_count: int | None = None
    hard_max_scene_count: int | None = None
    body: dict[str, Any]
    qa_verdict: str | None = None
    qa_warnings: dict[str, Any] | None = None
    source_hash: str | None = None
    stale_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ArtifactOut(_ORM):
    id: uuid.UUID
    production_run_id: uuid.UUID | None = None
    artifact_type: str
    domain_table: str | None = None
    domain_id: uuid.UUID | None = None
    version: int
    status: str
    body: dict[str, Any]
    content_hash: str
    created_by_agent_run_id: uuid.UUID | None = None
    created_at: datetime


class ArtifactDependencyOut(_ORM):
    id: uuid.UUID
    artifact_id: uuid.UUID
    depends_on_artifact_id: uuid.UUID
    dependency_kind: str
    dependency_hash: str | None = None
    created_at: datetime


class AgentEventOut(_ORM):
    id: uuid.UUID
    production_run_id: uuid.UUID
    agent_run_id: uuid.UUID | None = None
    event_type: str
    stage: str | None = None
    message: str | None = None
    payload_json: dict[str, Any] | None = None
    created_at: datetime


class AgentRunOut(_ORM):
    id: uuid.UUID
    production_run_id: uuid.UUID
    agent_name: str
    agent_role: str
    model: str | None = None
    status: str
    stage: str
    input_artifact_ids: list[str]
    output_artifact_ids: list[str] | None = None
    prompt_hash: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    token_input: int | None = None
    token_output: int | None = None
    cost_estimate: float | None = None
    duration_ms: int | None = None
    error: str | None = None
    payload_json: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class IssueOut(_ORM):
    id: uuid.UUID
    production_run_id: uuid.UUID
    chapter_id: uuid.UUID
    artifact_type: str
    artifact_id: uuid.UUID
    scene_id: uuid.UUID | None = None
    scene_no: int | None = None
    validator: str
    issue_kind: str
    severity: str
    quote: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    claim: str
    contract_reference: str | None = None
    recommended_action: str
    confidence: float | None = None
    auto_repair_allowed: bool
    status: str
    payload_json: dict[str, Any] | None = None
    created_at: datetime


class IssueDecisionOut(_ORM):
    id: uuid.UUID
    issue_id: uuid.UUID
    decided_by: str
    decision: str
    reason: str | None = None
    agent_run_id: uuid.UUID | None = None
    created_at: datetime


class RepairTaskOut(_ORM):
    id: uuid.UUID
    production_run_id: uuid.UUID
    chapter_id: uuid.UUID
    scene_id: uuid.UUID | None = None
    scene_no: int | None = None
    repair_kind: str
    authority_level: str
    status: str
    issue_ids: list[str]
    target_spans: dict[str, Any] | None = None
    instructions: str
    preserve: list[str]
    must_change: list[str]
    must_not_change: list[str]
    allowed_operations: list[str]
    forbidden_operations: list[str]
    word_delta_target: int | None = None
    requires_human_approval: bool
    created_at: datetime
    updated_at: datetime


class RepairAttemptOut(_ORM):
    id: uuid.UUID
    repair_task_id: uuid.UUID
    agent_run_id: uuid.UUID | None = None
    attempt_no: int
    model: str
    patch_json: dict[str, Any] | None = None
    revised_text: str | None = None
    change_summary: str | None = None
    issues_addressed: list[str]
    new_risks: list[str]
    word_count_before: int | None = None
    word_count_after: int | None = None
    created_at: datetime


class RepairVerificationOut(_ORM):
    id: uuid.UUID
    repair_attempt_id: uuid.UUID
    agent_run_id: uuid.UUID | None = None
    verdict: str
    resolved_issue_ids: list[str]
    remaining_issue_ids: list[str]
    new_issues_json: list[dict[str, Any]] | None = None
    target_issue_resolved: bool
    canon_preserved: bool
    scene_outcome_preserved: bool
    voice_preserved: bool
    required_beats_preserved: bool
    reader_state_preserved: bool
    regression_score: float
    reason: str | None = None
    payload_json: dict[str, Any] | None = None
    created_at: datetime


class IssueDecisionIn(BaseModel):
    reason: str | None = None
    merged_into_issue_id: uuid.UUID | None = None


class ProductionRunStartIn(BaseModel):
    mode: str = "full_chapter"
    target_words: int | None = None
    hard_max_words: int | None = None
    auto_triage: bool = True


class ChapterSequenceQaOut(BaseModel):
    verdict: str
    warnings: dict[str, Any] | None = None
    required_actions: list[dict[str, Any]] = []


class ChapterSequenceUpdateIn(BaseModel):
    body: dict[str, Any]
    reason: str | None = None


class ProductionRunCreateIn(BaseModel):
    chapter_id: uuid.UUID
    mode: str = "full_chapter"
    target_words: int | None = None
    hard_max_words: int | None = None
    auto_triage: bool = True


class ProductionRunOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    status: str
    mode: str
    target_words: int | None = None
    hard_max_words: int | None = None
    current_stage: str | None = None
    source_hash: str | None = None
    settings_json: dict[str, Any] | None = None
    summary_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ProductionRunActionOut(BaseModel):
    run: ProductionRunOut
    issue_count: int = 0
    repair_task_count: int = 0
    latest_verification: RepairVerificationOut | None = None


class ProductionRunDetailOut(BaseModel):
    run: ProductionRunOut
    chapter_sequence: ChapterSequenceOut | None = None
    artifacts: list[ArtifactOut] = []
    dependencies: list[ArtifactDependencyOut] = []
    agent_runs: list[AgentRunOut] = []
    events: list[AgentEventOut] = []
    issues: list[IssueOut] = []
    issue_decisions: list[IssueDecisionOut] = []
    repair_tasks: list[RepairTaskOut] = []
    repair_attempts: list[RepairAttemptOut] = []
    repair_verifications: list[RepairVerificationOut] = []
