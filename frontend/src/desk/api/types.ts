// Wire types for the Writers' Desk — generated from OpenAPI (see openapi.json at repo root).
// Hand-maintained extensions below cover nested JSON bodies OpenAPI cannot express (dict[str, Any]).

import type { components } from "./generated";

type S = components["schemas"];

// --- generated re-exports (flat API DTOs) -------------------------------------------------------

export type ModelSettingOut = S["ModelSettingOut"];
export type ModelSettingsOut = S["ModelSettingsOut"];
// Deterministic editorial-pipeline agents shown read-only in the Agents tab (no model, $0).
export type EditorialAgentOut = S["EditorialAgentOut"];
export type AgentOpsOut = S["AgentOpsOut"];
export type TelemetryDeleteOut = S["TelemetryDeleteOut"];
export type AgentGlobalsOut = S["AgentGlobalsOut"];
export type AgentOpsAgentOut = S["AgentOpsAgentOut"];
export type AgentPresetOut = S["AgentPresetOut"];
export type AgentPolicyOut = S["AgentPolicyOut"];
export type AgentStatsListOut = S["AgentStatsListOut"];
export type AgentStatsOut = S["AgentStatsOut"];
export type SmokeTestOut = S["SmokeTestOut"];
export type PipelineEstimateOut = S["PipelineEstimateOut"];
export type CritiqueOut = S["CritiqueOut"];
// SceneFidelity author surfaces (ADR 0016).
export type ScenePacketFidelityOut = S["ScenePacketFidelityOut"];
export type FidelityViolationOut = S["FidelityViolationOut"];
export type FidelityAcceptIn = S["FidelityAcceptIn"];
export type FidelityRequirementActionIn = S["FidelityRequirementActionIn"];
export type SceneFidelityOut = S["SceneFidelityOut"];
export type ClauseEvaluationOut = S["ClauseEvaluationOut"];
export type RepairPreviewOut = S["RepairPreviewOut"];
export type RepairPreviewCreateIn = S["RepairPreviewCreateIn"];
export type RepairPreviewActionIn = S["RepairPreviewActionIn"];
export type IssueOverrideIn = S["IssueOverrideIn"];
export type SceneOut = Omit<S["SceneOut"], "prose"> & { prose: string | null };
export type SceneDetail = Omit<S["SceneDetail"], "prose"> & { prose: string | null };
export type SceneVersionOut = S["SceneVersionOut"];
export type DecisionIn = S["DecisionIn"];
export type ContinuityResolveIn = S["ContinuityResolveIn"];
export type BookOut = S["BookOut"];
export type BookIn = S["BookIn"];
export type BookUpdateIn = S["BookUpdateIn"];
// Book → Part → Chapter grouping (renderer-neutral export foundation). Wire is flat: `parts[]` is a
// sibling list on ManuscriptOut and each chapter carries `part_id`; the spine builder tree-ifies it.
export type PartOut = S["PartOut"];
// `kind` has a server default ("part"), so callers may omit it — the generated schema marks it required.
export type PartCreateIn = Omit<S["PartCreateIn"], "kind"> & { kind?: string };
export type PartUpdateIn = S["PartUpdateIn"];
export type ChapterPartAssignIn = S["ChapterPartAssignIn"];
export type PartVolumeAssignIn = S["PartVolumeAssignIn"];
export type ManuscriptPart = S["ManuscriptPart"];
// Volumes — the top grouping tier (Book → Volume → Part → Chapter).
export type VolumeOut = S["VolumeOut"];
export type VolumeCreateIn = S["VolumeCreateIn"];
export type VolumeUpdateIn = S["VolumeUpdateIn"];
export type ManuscriptVolume = S["ManuscriptVolume"];
export type ChapterOut = Omit<S["ChapterOut"], "title" | "outline"> & {
  title: string | null;
  outline: string | null;
};
export type ChapterUpdateIn = S["ChapterUpdateIn"];
export type ChapterCreateIn = S["ChapterCreateIn"];
// `pov` is a per-scene POV override the wire schema doesn't yet model (blank = inherit chapter POV).
export type BeatOut = S["BeatOut"] & { pov?: string | null };
export type BeatUpdateIn = S["BeatUpdateIn"] & { pov?: string | null };
export type BeatCreateIn = S["BeatCreateIn"];
export type RunStartIn = S["RunStartIn"];
export type RunStartOut = S["RunStartOut"];
export type ManuscriptScene = S["ManuscriptScene"];
export type ManuscriptChapter = S["ManuscriptChapter"];
export type ManuscriptOut = S["ManuscriptOut"];
export type ActiveScene = S["ActiveScene"];
export type JobsStatusOut = Omit<S["JobsStatusOut"], "active_scene"> & {
  active_scene: ActiveScene | null;
};
export type FailedJobOut = S["FailedJobOut"];
export type QueuedJobOut = S["QueuedJobOut"];
export type CancelJobOut = S["CancelJobOut"];
export type JobsPauseOut = S["JobsPauseOut"];
export type RecentJobOut = S["RecentJobOut"];
export type RecentJobsOut = S["RecentJobsOut"];
export type DraftNextOut = S["DraftNextOut"];
export type RepairApplyAllOut = S["RepairApplyAllOut"];
export type ChapterPipelineOut = S["ChapterPipelineOut"];
export type ChapterRunFactsOut = S["ChapterRunFactsOut"];
export type RetryFailedOut = S["RetryFailedOut"] & {
  requested?: number;
  skipped?: DraftQueueBlockerOut[];
};
export type ClearFailedOut = S["ClearFailedOut"];
export type ClearFinishedJobsOut = S["ClearFinishedJobsOut"];
export type DeleteSceneOut = S["DeleteSceneOut"];
export type ClearDraftScenesOut = S["ClearDraftScenesOut"];

// Central Activity feed + cleanup (the single source behind the Activity drawer).
export type ActivityOut = S["ActivityOut"];
export type ActivityClearIn = S["ActivityClearIn"];
export type ActivityClearOut = S["ActivityClearOut"];
export type DeleteProductionRunOut = S["DeleteProductionRunOut"];
export type ClearProductionRunsOut = S["ClearProductionRunsOut"];
// Autonomous self-repair sweeper settings.
export type AutonomyOut = S["AutonomyOut"];
export type AutonomyUpdateIn = S["AutonomyUpdateIn"];

export type DraftQueueBlockerOut = {
  chapter_id: string;
  scene_no?: number | null;
  beat_id?: string | null;
  scene_packet_id?: string | null;
  reason: string;
  message: string;
  required_action: string;
};

export type DraftScheduleOut = {
  chapter_id: string;
  queued_job_ids: string[];
  queued: number;
  skipped: DraftQueueBlockerOut[];
  repaired_beats: number;
};

export type DraftReadinessProse = {
  scenes_with_prose?: number;
  expected_scenes?: number;
  missing_scene_numbers?: number[];
  assembly_ready?: boolean;
};

// A deterministic chapter-structure fault detected from the contracts alone. `kind` is one of
// sequence_scene_count_mismatch | sequence_budget_mismatch | scene_scope_bleed |
// duplicate_irreversible_beat | canon_contract_leak. The scene-count kind carries the machine
// fields the one-click "Align plan to N seeded scenes" action needs.
export type StructuralBlockerOut = {
  kind: string;
  message: string;
  sequence_id?: string | null;
  planned_scene_count?: number | null;
  seed_count?: number | null;
};

export type DraftReadinessOut = {
  chapter_id: string;
  chapter_packet_approved: boolean;
  scene_packets: Record<string, unknown>;
  beats: Record<string, unknown>;
  jobs: Record<string, unknown>;
  prose?: DraftReadinessProse;
  // Legacy queueability flag — kept for compatibility. Bind actions/badges to `can_draft` instead.
  draftable: boolean;
  // Plain-language name of the FIRST failing draft gate in pipeline order (packet → sequence/budget
  // → scene packets (stale/QA) → beats → jobs → prose coverage → rate limit); null iff can_draft.
  disabled_reason?: string | null;
  blockers: DraftQueueBlockerOut[];
  // --- authoritative draft gate (recovery L8): the ONLY fields ready badges / draft buttons obey.
  scene_packets_stale: number;
  scene_packet_qa_blocking: number;
  active_draft_jobs: number;
  missing_scene_drafts: number[];
  structural_blockers: StructuralBlockerOut[];
  provider_rate_limited: boolean;
  can_draft: boolean;
};

export type ScenePacketSummaryOut = S["ScenePacketSummaryOut"];
export type CharacterStateOut = S["CharacterStateOut"];
export type CanonEntityOut = S["CanonEntityOut"];
export type CanonEntityIn = S["CanonEntityIn"];
export type CanonEntityUpdateIn = S["CanonEntityUpdateIn"];
export type CharacterStateIn = S["CharacterStateIn"];
// Async canon rebuild ack (202) — the re-index runs in the background; completion shows in the Activity feed.
export type CanonRebuildStartedOut = S["CanonRebuildStartedOut"];
// Stale-canon cleanup (Workstream H): status-aware select -> preview -> soft retire / hard delete.
export type CanonCleanupIn = S["CanonCleanupIn"];
export type CanonCleanupItemOut = S["CanonCleanupItemOut"];
export type CanonCleanupPreviewOut = S["CanonCleanupPreviewOut"];
export type CanonRetireOut = S["CanonRetireOut"];
export type CanonBulkDeleteOut = S["CanonBulkDeleteOut"];
export type ThreadBeatOut = S["ThreadBeatOut"];
export type ThreadOut = S["ThreadOut"];
export type ThreadIn = S["ThreadIn"];
export type ThreadBeatIn = Omit<S["ThreadBeatIn"], "flag"> & { flag?: boolean };
export type AnnotationOut = S["AnnotationOut"];
export type AnnotationIn = S["AnnotationIn"];
export type SuggestionOut = Omit<S["SuggestionOut"], "new_text"> & { new_text: string | null };
export type SuggestionIn = S["SuggestionIn"];
export type RuleProposalOut = S["RuleProposalOut"];
export type RuleProposalDecisionIn = S["RuleProposalDecisionIn"];
export type DocMeta = S["DocMeta"];
/** Full library document (OpenAPI schema name: DocOut). */
export type DocDetail = S["DocOut"];
export type PacketProposeOut = S["PacketProposeOut"];
export type ScenePacketDeriveOut = S["ScenePacketDeriveOut"];
export type ScenePacketDeriveStatusOut = S["ScenePacketDeriveStatusOut"];
export type TelemetryTotals = S["TelemetryTotals"];
export type SceneTelemetryOut = S["SceneTelemetryOut"];
export type ChapterTelemetryOut = S["ChapterTelemetryOut"];
export type TelemetryGroupOut = S["TelemetryGroupOut"];
export type ChapterRollupOut = S["ChapterRollupOut"];
export type RunRollupOut = S["RunRollupOut"];
// Production attribution + editorial visibility (Telemetry tab). These schemas ARE in generated.ts now
// (S["ProductionRunRollupOut"] / S["EditorialAgentRunOut"]), but the generated versions mark the
// attribution/activity fields OPTIONAL (`?:`) where these hand types assert them required-present.
// Kept hand-written on purpose so consumers keep the stricter presence guarantee; collapsing to the
// generated shape is a deferred consumer/contract decision, not part of the safe realignment.
// One editorial production run's LLM spend (draft + repair calls sharing a production_run_id).
export type ProductionRunRollupOut = TelemetryTotals & {
  production_run_id: string | null;
  chapter_id: string | null;
  chapter_no: number | null;
  status: string | null;
};
// One deterministic editorial-orchestration step ($0, no tokens) — pipeline activity, not a cost pool.
export interface EditorialAgentRunOut {
  production_run_id: string | null;
  agent_name: string;
  agent_role: string;
  stage: string;
  status: string;
  duration_ms: number | null;
  started_at: string | null;
  cost_usd: number;
}
// S["BookTelemetryOut"] now declares run_total/by_production_run/by_kind/editorial_runs, so this could
// collapse to a plain re-export — EXCEPT by_production_run/editorial_runs would then use the generated
// element types, whose attribution/activity fields are optional. The extension re-pins them to the
// stricter hand ProductionRunRollupOut/EditorialAgentRunOut, so it is kept until that drift is resolved.
export type BookTelemetryOut = S["BookTelemetryOut"] & {
  run_total: number;
  by_production_run: ProductionRunRollupOut[];
  by_kind: TelemetryGroupOut[];
  editorial_runs: EditorialAgentRunOut[];
};
export type LlmCallOut = S["LlmCallOut"];
export type LlmCallListOut = S["LlmCallListOut"];
export type RunTelemetryOut = S["RunTelemetryOut"];
export type TelemetryProblemOut = S["TelemetryProblemOut"];
export type TelemetryProblemsOut = S["TelemetryProblemsOut"];
export type RunCompareOut = S["RunCompareOut"];
export type PipelineStepOut = S["PipelineStepOut"];
export type LlmCallLinksOut = S["LlmCallLinksOut"];
export type DraftAttemptOut = S["DraftAttemptOut"];
export type KnowledgeFactOut = S["KnowledgeFactOut"];
export type HumanSceneIn = S["HumanSceneIn"];
export type RedraftIn = S["RedraftIn"];
export type GateMode = S["GateMode"];

export type DecisionKind = S["Decision"];
export type SuggestionStatus = S["SuggestionStatus"];
export type RuleProposalStatus = S["RuleProposalStatus"];

// --- batch runs (POST /runs/batch) ---------------------------------------------------------------
// Stage several chapters and plan them all in one call; `auto_draft` runs gate 1 → draft unattended.
// Now in OpenAPI: re-exported from the generated DTOs. NOTE: generated BatchRunStartIn marks
// `gate_mode` required (no `| string`) where the old hand type had it optional — see report.
export type BatchChapterSpec = S["BatchChapterSpec"];
export type BatchRunStartIn = S["BatchRunStartIn"];
/** @deprecated use BatchRunStartIn (generated name). */
export type BatchRunStart = S["BatchRunStartIn"];
export type BatchChapterResult = S["BatchChapterResultOut"];
export type BatchRunOut = S["BatchRunOut"];

// --- frontend refinements (not in OpenAPI or looser on the wire) ---------------------------------

export type LengthStatus =
  | "under_min"
  | "within_budget"
  | "over_max"
  | "over_hard_max_compressed"
  | "over_hard_max_quarantined";

export type RuleKind = "voice" | "dialogue";

export type ScenePacketStatus = "proposed" | "approved" | "blocked" | "stale" | "rate_limited";

/** Timestamped line in the Desk live activity feed (client-only; not an API DTO). */
export interface ActivityEntry {
  id: string;
  ts: number;
  text: string;
}

// --- nested JSON extensions (packet / scene-packet bodies) ----------------------------------------

export interface PacketClaim {
  claim: string;
  source_strength: string;
  source_id: string | null;
  source_title_or_file?: string | null;
  excerpt?: string | null;
  confidence?: string;
}

export interface PacketRisk {
  risk: string;
  why_dangerous: string;
  prevention: string;
}

export interface PacketWordBudget {
  min?: number;
  target?: number;
  max?: number;
  hard_max?: number;
}

export interface PacketSceneSeed {
  seed_id: string;
  scene_no: number;
  scene_job?: string;
  required_beats?: string[];
  forbidden_beats?: string[];
  exit_state?: string;
  scene_type?: string;
  word_budget?: PacketWordBudget;
}

export interface PacketBody {
  chapter_job?: string;
  one_sentence_spine?: string;
  entry_state?: string;
  exit_state?: string;
  emotional_spine?: string;
  characters_present?: string[];
  characters_absent?: string[];
  characters_mentioned_only?: string[];
  characters_forbidden?: string[];
  allowed_knowledge?: string[];
  forbidden_knowledge?: string[];
  required_reveals?: string[];
  forbidden_reveals?: string[];
  canon_locks?: string[];
  roster_locks?: string[];
  relationship_locks?: string[];
  timeline_locks?: string[];
  allowed_ui_concepts?: string[];
  forbidden_ui_concepts?: string[];
  required_unanswered_questions?: string[];
  scene_seeds?: PacketSceneSeed[];
  known_risks?: PacketRisk[];
  claims?: PacketClaim[];
  open_questions?: string[];
  confidence?: string;
  blocked_reason?: string;
  adjudication_notes?: string;
}

export interface ResolvedQuestion {
  q: string;
  resolution: string;
  at: string;
}

export interface QaIssue {
  kind?: string;
  // Dotted path of the offending scene-packet field (e.g. "known_before_scene.reader"), or null/absent
  // for a whole-packet problem. The editor uses this to render the issue inline next to its control.
  field?: string | null;
  detail?: string;
  // `repair` = fixable: never blocks drafting/approval, does block final export. `block` = true
  // blocker (deterministic checks only — LLM issues are capped at repair).
  severity?: "info" | "warn" | "repair" | "block" | string;
  // Persisted gate facts (new rows). Old rows omit them — normalizePacketViolation() in
  // lib/packetBlockers.ts derives the fallback from severity.
  blocks_drafting?: boolean;
  blocks_human_review?: boolean;
  blocks_final_export?: boolean;
}

export interface PacketWarnings {
  residual_risks?: string[];
  issues?: QaIssue[];
  // Deterministic-validation channel: contract violations found by the deterministic validator (block +
  // warn), distinct from QA `issues` above. Invalid provenance collapses to a single warn item with
  // kind "provenance_normalized". `blocker_source` names which gate produced the block ("validation").
  violations?: QaIssue[];
  blocker_source?: string;
  blocker_kind?: string;
  recovery_actions?: string[];
  blocker_diagnostics?: Record<string, unknown> | null;
  blocked_reason?: string;
}

export type PacketOut = Omit<
  S["PacketOut"],
  | "body"
  | "qa_warnings"
  | "open_questions"
  | "blocked_reason"
  | "blocker_source"
  | "blocker_kind"
  | "recovery_actions"
  | "blocker_diagnostics"
> & {
  body: PacketBody;
  qa_warnings: PacketWarnings | null;
  open_questions: { items?: string[]; resolved?: ResolvedQuestion[] } | null;
  blocked_reason?: string | null;
  blocker_source?: string | null;
  blocker_kind?: string | null;
  recovery_actions?: string[];
  blocker_diagnostics?: Record<string, unknown> | null;
};

export type PacketUpdateIn = Omit<S["PacketUpdateIn"], "body" | "open_questions"> & {
  body?: PacketBody | null;
  open_questions?: { items?: string[]; resolved?: ResolvedQuestion[] } | null;
};

export interface SceneWordBudget {
  min?: number;
  target?: number;
  max?: number;
  hard_max?: number;
  compression_priority?: string[];
  expansion_priority?: string[];
  must_not_spend_words_on?: string[];
}

export interface ScenePacketBody {
  scene_no?: number;
  scene_job?: string;
  scene_type?: string;
  chapter_position?: string;
  word_budget?: SceneWordBudget;
  known_before_scene?: { reader?: string[]; pov?: string[]; omniscient_author?: string[] };
  learned_during_scene?: {
    reader_must_learn?: string[];
    reader_may_learn?: string[];
    reader_may_infer_only?: string[];
  };
  must_remain_hidden?: { reader?: string[]; pov?: string[]; all_surface_prose?: string[] };
  pov_permissions?: {
    may_notice?: string[];
    may_infer?: string[];
    must_not_know?: string[];
    may_be_wrong_about?: string[];
  };
  intentional_mysteries?: {
    mystery?: string;
    desired_reader_effect?: string;
    do_not_explain?: boolean;
  }[];
  reviewer_false_positive_traps?: string[];
  required_beats?: string[];
  forbidden_beats?: string[];
  exit_state?: string;
  tone_pressure?: string;
  phrases_to_avoid_echoing?: string[];
  reviewer_instructions?: Record<string, string[]>;
  // Author-emitted provenance: each knowledge claim it drew from a canon snippet, tagged with that
  // snippet's handle (resolve against ScenePacketOut.sources to get the file + heading).
  claim_sources?: { claim?: string; source_id?: string | null }[];
  blocked_reason?: string;
}

// One retrieved canon/owner chunk this packet was derived from. `handle` (e.g. "C1") is what the
// author cites in claim_sources; the rest resolves it to a real location in the canon.
export interface SceneSource {
  handle?: string;
  id?: string;
  doc_path?: string;
  heading_path?: string;
  owner_topic?: string | null;
  retrieval_reason?: string;
  score?: number;
}

export type ScenePacketOut = Omit<
  S["ScenePacketOut"],
  "body" | "qa_warnings" | "status" | "sources"
> & {
  body: ScenePacketBody;
  qa_warnings: PacketWarnings | null;
  status: ScenePacketStatus | string;
  // Re-typed from the generated JSONB `unknown[]` to the concrete provenance-legend shape the UI reads.
  sources?: SceneSource[] | null;
  blocked_reason?: string | null;
  blocker_source?: string | null;
};

export type ScenePacketUpdateIn = Omit<S["ScenePacketUpdateIn"], "body"> & {
  body?: ScenePacketBody | null;
};

// Production-orchestration DTOs (Production tab) — now generated 1:1 from shared/schemas.py.
export type ChapterSequenceOut = S["ChapterSequenceOut"];
export type ArtifactOut = S["ArtifactOut"];
export type ArtifactDependencyOut = S["ArtifactDependencyOut"];
export type AgentRunOut = S["AgentRunOut"];
export type AgentEventOut = S["AgentEventOut"];
export type IssueOut = S["IssueOut"];
export type IssueDecisionOut = S["IssueDecisionOut"];
export type RepairTaskOut = S["RepairTaskOut"];
export type RepairAttemptOut = S["RepairAttemptOut"];
export type RepairVerificationOut = S["RepairVerificationOut"];
export type ProductionRunOut = S["ProductionRunOut"];

// KEPT hand-written: generated ProductionRunCreateIn marks `mode` and `auto_triage` required (they carry
// server defaults, which openapi-typescript emits as required). Callers omit them (e.g. ProductionScreen
// calls startProductionRun({ chapter_id, auto_triage }) with no `mode`), so re-exporting the generated
// shape would break those call sites. Optionality realignment is a deferred contract decision.
export interface ProductionRunCreateIn {
  chapter_id: string;
  mode?: string;
  target_words?: number | null;
  hard_max_words?: number | null;
  auto_triage?: boolean;
}

export type ProductionRunActionOut = S["ProductionRunActionOut"];
export type ProductionRunDetailOut = S["ProductionRunDetailOut"];

// --- live pipeline dashboard (GET /books/{book_id}/pipeline) --------------------------------------
// Now generated from shared/schemas.py PipelineStatusOut — re-exported below. Every `reason`/
// `suggested_action` string is pre-computed server-side; `action_kind` is the machine key this screen
// maps to an endpoint call or deep-link (approve_apply | verify | decide_issue | resume |
// align_scene_count | retry | draft_missing | none). The pipeline never assigns
// blocked/failed/rejected/cancelled to a run/task, so those states are not modelled.
export type PipelineJobOut = S["PipelineJobOut"];
export type PipelineAgentRunOut = S["PipelineAgentRunOut"];
export type PipelineRunRef = S["PipelineRunRef"];
export type PipelineRepairTaskRef = S["PipelineRepairTaskRef"];
export type PipelineIssueRef = S["PipelineIssueRef"];
export type PipelineCompletedRef = S["PipelineCompletedRef"];
export type SweeperStatusOut = S["SweeperStatusOut"];
export type PipelineNowOut = S["PipelineNowOut"];
export type PipelineQueueOut = S["PipelineQueueOut"];
export type PipelineWaitingOut = S["PipelineWaitingOut"];
export type PipelineBlockedOut = S["PipelineBlockedOut"];
export type PipelineCompletedOut = S["PipelineCompletedOut"];
export type PipelineStatusOut = S["PipelineStatusOut"];
