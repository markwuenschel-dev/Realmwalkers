// Mirrors dominion/shared/schemas.py — keep in sync with the API DTOs.
export type Severity = "info" | "warn" | "hard";
export type Decision = "approve" | "deny" | "revise";
export type GateMode = "pause_each" | "draft_ahead";

export interface Critique {
  id: string;
  reviewer: string;
  severity: Severity;
  note?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface SceneOut {
  id: string;
  chapter_id: string;
  scene_no: number;
  version: number;
  status: string;
  prose?: string | null;
  prose_source: string;
  passes_run?: string[] | null;
  token_count?: number | null;
  model?: string | null;
  created_at: string;
}

export interface SceneDetail extends SceneOut {
  critiques: Critique[];
}

export interface DecisionIn {
  decision: Decision;
  target_pass?: string | null;
  feedback?: string | null;
  edited_prose?: string | null;
}

export interface ContinuityResolveIn {
  critique_id: string;
  choice: "use_prose" | "use_ledger" | "edit";
}

// --- Gate 1 (planning) + history + manuscript ----------------------------------------------------

export interface BookOut {
  id: string;
  title: string;
  premise?: string | null;
  created_at: string;
}

export interface BookCreate {
  title: string;
  premise?: string | null;
}

export interface ChapterOut {
  id: string;
  book_id: string;
  chapter_no: number;
  pov: string;
  outline?: string | null;
  status: string;
}

export interface BeatOut {
  id: string;
  chapter_id: string;
  scene_no: number;
  beat_text?: string | null;
  characters_present?: string[] | null;
  tags?: string[] | null;
  expected_state_changes?: Record<string, unknown> | null;
  knowledge_injections?: string[] | null;
  status: string;
}

export interface BeatUpdate {
  beat_text?: string | null;
  characters_present?: string[] | null;
  tags?: string[] | null;
  expected_state_changes?: Record<string, unknown> | null;
  knowledge_injections?: string[] | null;
}

export interface RunPlanIn {
  book_id: string;
  chapter_no: number;
  pov: string;
  outline: string;
  gate_mode?: GateMode;
  token_budget?: number | null;
}

export interface RunPlanOut {
  run_id: string;
  chapter_id: string;
  chapter_no: number;
  pov: string;
  beats: BeatOut[];
}

export interface ApproveBeatsOut {
  chapter_id: string;
  approved: number;
  jobs: string[];
}

export interface SceneVersionOut extends SceneOut {
  agent_original?: string | null;
}

export interface ManuscriptScene {
  scene_no: number;
  prose: string;
}

export interface ManuscriptChapter {
  chapter_no: number;
  pov: string;
  scenes: ManuscriptScene[];
}

export interface ManuscriptOut {
  book_id: string;
  title: string;
  chapters: ManuscriptChapter[];
}
