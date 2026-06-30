// Wire types for the Writers' Desk — generated from OpenAPI (see openapi.json at repo root).
// Hand-maintained extensions below cover nested JSON bodies OpenAPI cannot express (dict[str, Any]).

import type { components } from "./generated";

type S = components["schemas"];

// --- generated re-exports (flat API DTOs) -------------------------------------------------------

export type ModelSettingOut = S["ModelSettingOut"];
export type ModelSettingsOut = S["ModelSettingsOut"];
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
export type DraftNextOut = S["DraftNextOut"];
export type RetryFailedOut = S["RetryFailedOut"] & {
  requested?: number;
  skipped?: DraftQueueBlockerOut[];
};
export type ClearFailedOut = S["ClearFailedOut"];

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

export type DraftReadinessOut = {
  chapter_id: string;
  chapter_packet_approved: boolean;
  scene_packets: Record<string, unknown>;
  beats: Record<string, unknown>;
  jobs: Record<string, unknown>;
  draftable: boolean;
  blockers: DraftQueueBlockerOut[];
};
export type CharacterStateOut = S["CharacterStateOut"];
export type CanonEntityOut = S["CanonEntityOut"];
export type CanonEntityIn = S["CanonEntityIn"];
export type CanonEntityUpdateIn = S["CanonEntityUpdateIn"];
export type CharacterStateIn = S["CharacterStateIn"];
export type CanonIngestOut = S["CanonIngestOut"];
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

export type ScenePacketStatus = "proposed" | "approved" | "blocked" | "stale";

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
  detail?: string;
  severity?: "info" | "warn" | "block" | string;
}

export interface PacketWarnings {
  residual_risks?: string[];
  issues?: QaIssue[];
  blocked_reason?: string;
}

export type PacketOut = Omit<S["PacketOut"], "body" | "qa_warnings" | "open_questions"> & {
  body: PacketBody;
  qa_warnings: PacketWarnings | null;
  open_questions: { items?: string[]; resolved?: ResolvedQuestion[] } | null;
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
  blocked_reason?: string;
}

export type ScenePacketOut = Omit<S["ScenePacketOut"], "body" | "qa_warnings" | "status"> & {
  body: ScenePacketBody;
  qa_warnings: PacketWarnings | null;
  status: ScenePacketStatus | string;
  blocked_reason?: string | null;
  blocker_source?: string | null;
};

export type ScenePacketUpdateIn = Omit<S["ScenePacketUpdateIn"], "body"> & {
  body?: ScenePacketBody | null;
};
