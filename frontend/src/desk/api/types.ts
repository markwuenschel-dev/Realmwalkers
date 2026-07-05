// Wire types for the Writers' Desk — generated from OpenAPI (see openapi.json at repo root).
// Hand-maintained extensions below cover nested JSON bodies OpenAPI cannot express (dict[str, Any]).

import type { components } from "./generated";

type S = components["schemas"];

// --- generated re-exports (flat API DTOs) -------------------------------------------------------

export type ModelSettingOut = S["ModelSettingOut"];
export type ModelSettingsOut = S["ModelSettingsOut"];
export type AgentOpsOut = S["AgentOpsOut"];
export type TelemetryDeleteOut = {
  deleted_calls: number;
};
export type AgentGlobalsOut = S["AgentGlobalsOut"];
export type AgentOpsAgentOut = S["AgentOpsAgentOut"];
export type AgentPresetOut = S["AgentPresetOut"];
export type AgentPolicyOut = S["AgentPolicyOut"];
export type AgentStatsListOut = S["AgentStatsListOut"];
export type AgentStatsOut = S["AgentStatsOut"];
export type SmokeTestOut = S["SmokeTestOut"];
export type PipelineEstimateOut = S["PipelineEstimateOut"];
export type CritiqueOut = S["CritiqueOut"];
export type SceneOut = Omit<S["SceneOut"], "prose"> & { prose: string | null };
export type SceneDetail = Omit<S["SceneDetail"], "prose"> & { prose: string | null };
export type SceneVersionOut = S["SceneVersionOut"];
export type DecisionIn = S["DecisionIn"];
export type ContinuityResolveIn = S["ContinuityResolveIn"];
export type BookOut = S["BookOut"];
export type BookIn = S["BookIn"];
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
export type RetryFailedOut = S["RetryFailedOut"] & {
  requested?: number;
  skipped?: DraftQueueBlockerOut[];
};
export type ClearFailedOut = S["ClearFailedOut"];
export type DeleteSceneOut = S["DeleteSceneOut"];
export type ClearDraftScenesOut = S["ClearDraftScenesOut"];

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
export type CanonIngestOut = S["CanonIngestOut"];
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
// `run_total` is the count of all run rows before the limit/offset page slice (the wire schema
// returns it now, but the generated DTO predates it).
export type BookTelemetryOut = S["BookTelemetryOut"] & { run_total: number };
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

// --- batch runs (POST /runs/batch — new endpoint, not yet in OpenAPI) ----------------------------
// Stage several chapters and plan them all in one call; `auto_draft` runs gate 1 → draft unattended.

export interface BatchChapterSpec {
  chapter_no: number;
  pov: string;
  outline: string;
  max_beats?: number | null;
  target_words?: number | null;
}

export interface BatchRunStart {
  book_id: string;
  chapters: BatchChapterSpec[];
  gate_mode?: GateMode | string;
  token_budget?: number | null;
  auto_draft: boolean;
}

export interface BatchChapterResult {
  chapter_id: string;
  chapter_no: number;
  pov: string;
  beat_count: number;
  queued_jobs: number;
}

export interface BatchRunOut {
  run_id: string;
  results: BatchChapterResult[];
}

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

export interface ChapterSequenceOut {
  id: string;
  book_id: string;
  chapter_id: string;
  chapter_packet_id: string;
  status: string;
  target_words?: number | null;
  max_words?: number | null;
  hard_max_words?: number | null;
  target_scene_count?: number | null;
  hard_max_scene_count?: number | null;
  body: Record<string, unknown>;
  qa_verdict?: string | null;
  qa_warnings?: Record<string, unknown> | null;
  source_hash?: string | null;
  stale_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ArtifactOut {
  id: string;
  production_run_id?: string | null;
  artifact_type: string;
  domain_table?: string | null;
  domain_id?: string | null;
  version: number;
  status: string;
  body: Record<string, unknown>;
  content_hash: string;
  created_by_agent_run_id?: string | null;
  created_at: string;
}

export interface ArtifactDependencyOut {
  id: string;
  artifact_id: string;
  depends_on_artifact_id: string;
  dependency_kind: string;
  dependency_hash?: string | null;
  created_at: string;
}

export interface AgentRunOut {
  id: string;
  production_run_id: string;
  agent_name: string;
  agent_role: string;
  model?: string | null;
  status: string;
  stage: string;
  input_artifact_ids: string[];
  output_artifact_ids?: string[] | null;
  prompt_hash?: string | null;
  input_hash?: string | null;
  output_hash?: string | null;
  token_input?: number | null;
  token_output?: number | null;
  cost_estimate?: number | null;
  duration_ms?: number | null;
  error?: string | null;
  payload_json?: Record<string, unknown> | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface AgentEventOut {
  id: string;
  production_run_id: string;
  agent_run_id?: string | null;
  event_type: string;
  stage?: string | null;
  message?: string | null;
  payload_json?: Record<string, unknown> | null;
  created_at: string;
}

export interface IssueOut {
  id: string;
  production_run_id: string;
  chapter_id: string;
  artifact_type: string;
  artifact_id: string;
  scene_id?: string | null;
  scene_no?: number | null;
  validator: string;
  issue_kind: string;
  severity: string;
  quote?: string | null;
  span_start?: number | null;
  span_end?: number | null;
  claim: string;
  contract_reference?: string | null;
  recommended_action: string;
  confidence?: number | null;
  auto_repair_allowed: boolean;
  status: string;
  payload_json?: Record<string, unknown> | null;
  created_at: string;
}

export interface IssueDecisionOut {
  id: string;
  issue_id: string;
  decided_by: string;
  decision: string;
  reason?: string | null;
  agent_run_id?: string | null;
  created_at: string;
}

export interface RepairTaskOut {
  id: string;
  production_run_id: string;
  chapter_id: string;
  scene_id?: string | null;
  scene_no?: number | null;
  repair_kind: string;
  authority_level: string;
  status: string;
  issue_ids: string[];
  target_spans?: Record<string, unknown> | null;
  instructions: string;
  preserve: string[];
  must_change: string[];
  must_not_change: string[];
  allowed_operations: string[];
  forbidden_operations: string[];
  word_delta_target?: number | null;
  requires_human_approval: boolean;
  // Stamped by Approve & apply — distinguishes an approval-hold from a conflict-hold.
  human_approved_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepairAttemptOut {
  id: string;
  repair_task_id: string;
  agent_run_id?: string | null;
  attempt_no: number;
  model: string;
  patch_json?: Record<string, unknown> | null;
  revised_text?: string | null;
  change_summary?: string | null;
  issues_addressed: string[];
  new_risks: string[];
  word_count_before?: number | null;
  word_count_after?: number | null;
  created_at: string;
}

export interface RepairVerificationOut {
  id: string;
  repair_attempt_id: string;
  agent_run_id?: string | null;
  verdict: string;
  resolved_issue_ids: string[];
  remaining_issue_ids: string[];
  new_issues_json?: Record<string, unknown>[] | null;
  target_issue_resolved: boolean;
  canon_preserved: boolean;
  scene_outcome_preserved: boolean;
  voice_preserved: boolean;
  required_beats_preserved: boolean;
  reader_state_preserved: boolean;
  regression_score: number;
  reason?: string | null;
  payload_json?: Record<string, unknown> | null;
  created_at: string;
}

export interface ProductionRunOut {
  id: string;
  book_id: string;
  chapter_id: string;
  status: string;
  mode: string;
  target_words?: number | null;
  hard_max_words?: number | null;
  current_stage?: string | null;
  source_hash?: string | null;
  settings_json?: Record<string, unknown> | null;
  summary_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ProductionRunCreateIn {
  chapter_id: string;
  mode?: string;
  target_words?: number | null;
  hard_max_words?: number | null;
  auto_triage?: boolean;
}

export interface ProductionRunActionOut {
  run: ProductionRunOut;
  issue_count: number;
  repair_task_count: number;
  latest_verification?: RepairVerificationOut | null;
}

export interface ProductionRunDetailOut {
  run: ProductionRunOut;
  chapter_sequence?: ChapterSequenceOut | null;
  artifacts: ArtifactOut[];
  dependencies: ArtifactDependencyOut[];
  agent_runs: AgentRunOut[];
  events: AgentEventOut[];
  issues: IssueOut[];
  issue_decisions: IssueDecisionOut[];
  repair_tasks: RepairTaskOut[];
  repair_attempts: RepairAttemptOut[];
  repair_verifications: RepairVerificationOut[];
}
