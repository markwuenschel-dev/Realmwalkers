// Shared types for the Writers' Desk port. State enums mirror the prototype's `state` object;
// data interfaces describe the demo fixtures in data.ts.

export type Screen = "scene" | "inbox" | "chapters" | "diff" | "manuscript" | "ledger";
export type Tab = "continuity" | "notes" | "changes";
export type Mode = "reading" | "suggesting" | "editing";
export type DecisionKind = "approve" | "revise" | "deny";
export type ChaptersView = "board" | "timeline";

export type Resolved = Record<string, "prose" | "ledger">;
export type SuggStatus = Record<string, "accepted" | "rejected">;

// --- prose annotation markers ---------------------------------------------------------------------
export type MarkerKind = "entity" | "sugg" | "conflict" | "anno";
export interface Marker {
  find: string;
  kind: MarkerKind;
  id: string;
}

// --- ledger / canon fixtures ----------------------------------------------------------------------
export interface Entity {
  name: string;
  role: string;
  conflict?: string;
  rows: [string, string][];
}

export interface Suggestion {
  author: string;
  old: string;
  neu: string;
  why: string;
}

export interface ConflictDef {
  attribute: string;
  context: string;
  proseValue: string;
  ledgerValue: string;
}

export interface Annotation {
  id: string;
  quote: string;
  author: string;
  note: string;
}

// --- review queue / board / chapters --------------------------------------------------------------
export interface QueueScene {
  no: number;
  title: string;
  words: string;
  version: number;
  status: string;
}

export interface BoardScene {
  no: number;
  title: string;
  words: number;
  status: string;
}

export interface Chapter {
  no: number;
  title: string;
  pov: string;
  target: number;
  words: number;
  approved: number;
}

export interface TimelineScene {
  n: number;
  ch: number;
  pov: string;
  status: string;
  flags: number;
}

export interface ThreadBeat {
  s: number;
  label: string;
  flag?: boolean;
}

export interface Thread {
  id: string;
  name: string;
  kind: string;
  state: string;
  note: string;
  beats: ThreadBeat[];
}

// --- inbox / diff / manuscript / ledger -----------------------------------------------------------
export interface StatDef {
  label: string;
  value: string;
  suffix: string;
  hasBar?: boolean;
  pct?: string;
  note?: string;
}

export type DiffType = "same" | "change" | "add" | "del";
export interface DiffRowData {
  type: DiffType;
  l: string;
  r: string;
}

export interface MsChapter {
  no: number;
  title: string;
  pov: string;
  paras: string[];
}

export interface LedgerChar {
  initial: string;
  name: string;
  role: string;
  attrs: { k: string; v: string }[];
}

export interface LedgerCatDef {
  id: string;
  label: string;
  count: number;
}
