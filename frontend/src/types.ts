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
