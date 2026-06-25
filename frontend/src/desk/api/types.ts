// Wire types for the Writers' Desk — mirror the FastAPI DTOs in src/dominion/shared/schemas.py.
// Kept self-contained (the Desk no longer reads any fixtures from desk/data.ts).

export interface CritiqueOut {
  id: string;
  reviewer: string;
  severity: "info" | "warn" | "hard" | string;
  note: string | null;
  payload: Record<string, unknown> | null;
}

export interface SceneOut {
  id: string;
  chapter_id: string;
  scene_no: number;
  version: number;
  status: string;
  prose: string | null;
  prose_source: string;
  passes_run: string[] | null;
  token_count: number | null;
  model: string | null;
  created_at: string;
}

export interface SceneDetail extends SceneOut {
  critiques: CritiqueOut[];
  is_exemplar: boolean; // curated voice exemplar for this POV?
}

export interface SceneVersionOut extends SceneOut {
  agent_original: string | null;
}

export type DecisionKind = "approve" | "deny" | "revise";

export interface DecisionIn {
  decision: DecisionKind;
  target_pass?: string | null;
  feedback?: string | null;
  edited_prose?: string | null;
}

export interface ContinuityResolveIn {
  critique_id: string;
  choice: "use_prose" | "use_ledger" | "edit";
}

export interface BookOut {
  id: string;
  title: string;
  premise: string | null;
  created_at: string;
}

export interface BookIn {
  title: string;
  premise?: string | null;
}

export interface ChapterOut {
  id: string;
  book_id: string;
  chapter_no: number;
  title: string | null;
  pov: string;
  outline: string | null;
  status: string;
}

export interface ChapterUpdateIn {
  title?: string | null;
}

export interface BeatOut {
  id: string;
  chapter_id: string;
  scene_seed_id: string | null;   // set when the beat was derived from a packet scene_seed (Phase 2)
  scene_no: number;
  beat_text: string | null;
  characters_present: string[] | null;
  tags: string[] | null;
  expected_state_changes: Record<string, Record<string, unknown>> | null;
  knowledge_injections: string[] | null;
  target_words: number | null;
  status: string;
}

export interface BeatUpdateIn {
  beat_text?: string | null;
  characters_present?: string[] | null;
  tags?: string[] | null;
  knowledge_injections?: string[] | null;
  target_words?: number | null;
}

export interface BeatCreateIn {
  scene_no: number;
  beat_text?: string | null;
  characters_present?: string[] | null;
  tags?: string[] | null;
  target_words?: number | null;
}

export interface RunStartIn {
  book_id: string;
  chapter_no: number;
  pov: string;
  outline: string;
  max_beats?: number | null;
  target_words?: number | null;
}

export interface RunStartOut {
  run_id: string;
  chapter_id: string;
  chapter_no: number;
  pov: string;
  beats: BeatOut[];
}

export interface ManuscriptScene {
  scene_no: number;
  prose: string | null;
}

export interface ManuscriptChapter {
  chapter_no: number;
  title: string | null;
  pov: string;
  scenes: ManuscriptScene[];
}

export interface ManuscriptOut {
  book_id: string;
  title: string;
  chapters: ManuscriptChapter[];
}

export interface ActiveScene {
  chapter_no: number | null;
  scene_no: number | null;
  phase: string | null;
  elapsed_s: number | null;
}

export interface JobsStatusOut {
  running: boolean;
  queued: number;
  failed: number;
  active_scene: ActiveScene | null;
}

export interface FailedJobOut {
  id: string;
  chapter_no: number | null;
  scene_no: number | null;
  last_error: string | null;   // why this job died — surfaced on the failed card so it isn't a mystery
}

// A timestamped line in the Desk's live activity feed (drafting phases, queue transitions).
export interface ActivityEntry {
  id: string;
  ts: number;
  text: string;
}

export interface DraftNextOut {
  scheduled: boolean;
  queued: number;
  running: boolean;
}

export interface RetryFailedOut {
  requeued: number;
  scheduled: boolean;
  queued: number;
  running: boolean;
}

export interface CharacterStateOut {
  character: string;
  stats: Record<string, unknown>;
  provisional: boolean;
  is_pov: boolean;
  body: string | null;
}

export interface CanonEntityOut {
  id: string;
  kind: string | null;
  name: string | null;
  body: string | null;
}

export interface CanonEntityIn {
  kind?: string | null;
  name?: string | null;
  body?: string | null;
}

export interface CanonEntityUpdateIn {
  kind?: string | null;
  name?: string | null;
  body?: string | null;
}

export interface CharacterStateIn {
  stats: Record<string, unknown>;
  body?: string | null;
}

export interface CanonIngestOut {
  indexed: number;
}

export interface ThreadBeatOut {
  id: string;
  scene_no: number;
  label: string | null;
  flag: boolean;
}

export interface ThreadOut {
  id: string;
  name: string;
  kind: string | null;
  state: string | null;
  note: string | null;
  beats: ThreadBeatOut[];
}

export interface ThreadIn {
  name: string;
  kind?: string | null;
  state?: string | null;
  note?: string | null;
}

export interface ThreadBeatIn {
  scene_no: number;
  label?: string | null;
  flag?: boolean;
}

export interface AnnotationOut {
  id: string;
  scene_id: string;
  version: number | null;
  quote: string | null;
  author: string | null;
  note: string | null;
  created_at: string;
}

export interface AnnotationIn {
  note: string;
  quote?: string | null;
  author?: string | null;
}

export type SuggestionStatus = "pending" | "accepted" | "rejected";

export interface SuggestionOut {
  id: string;
  scene_id: string;
  version: number | null;
  quote: string;
  new_text: string | null;
  author: string | null;
  why: string | null;
  status: SuggestionStatus;
  created_at: string;
}

export interface SuggestionIn {
  quote: string;
  new_text?: string | null;
  author?: string | null;
  why?: string | null;
}

// --- distilled voice/dialogue rules (LEARNING_FROM_EDITS Tier 3) ---
export type RuleKind = "voice" | "dialogue";
export type RuleProposalStatus = "pending" | "accepted" | "rejected";

export interface RuleProposalOut {
  id: string;
  book_id: string;
  pov: string;
  kind: RuleKind | string;
  rule_text: string;
  rationale: string | null;
  source_pair_ids: string[] | null;
  status: RuleProposalStatus | string;
  created_at: string;
}

export interface RuleProposalDecisionIn {
  status: RuleProposalStatus;
  rule_text?: string | null; // optional author edit applied on accept
}

// --- canon / planning / style docs (read-only Domain-B markdown) ---
export interface DocMeta {
  path: string; // id, relative to the docs root (e.g. "canon/timeline/master_timeline.md")
  title: string;
  category: string; // "canon" | "planning" | "style"
}

export interface DocDetail extends DocMeta {
  content: string; // raw markdown
}

// --- contract-first drafting: chapter knowledge packets (Phase 1) ---
export interface PacketClaim {
  claim: string;
  source_strength: string; // LOCKED_CANON | DERIVED_FROM_OUTLINE | PLAUSIBLE_INFERENCE | UNRESOLVED | FORBIDDEN
  source_id: string | null; // resolved canon id, "OUTLINE", or null (inference)
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
  seed_id: string; // server-minted stable id (the sync key for later phases)
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
}

export interface PacketWarnings {
  residual_risks?: string[];
  issues?: { kind?: string; detail?: string }[];
  blocked_reason?: string;
}

export interface PacketOut {
  id: string;
  book_id: string;
  chapter_id: string;
  status: string; // proposed | approved | blocked
  confidence: string | null; // green | yellow | red
  qa_verdict: string | null;
  qa_warnings: PacketWarnings | null;
  body: PacketBody;
  open_questions: { items?: string[] } | null;
  created_at: string;
}

export interface PacketUpdateIn {
  body?: PacketBody | null;
  open_questions?: { items?: string[] } | null;
  confidence?: string | null;
}
