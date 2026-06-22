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
  pov: string;
  outline: string | null;
  status: string;
}

export interface BeatOut {
  id: string;
  chapter_id: string;
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
}

export interface JobsStatusOut {
  running: boolean;
  queued: number;
  failed: number;
  active_scene: ActiveScene | null;
}

export interface DraftNextOut {
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
