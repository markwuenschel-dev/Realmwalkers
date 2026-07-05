// UI-state types for the Writers' Desk. (Server/wire types live in desk/api/types.ts; the Desk no
// longer carries any fixture data interfaces — every screen reads live data.)

export type Screen =
  | "scene"
  | "inbox"
  | "chapters"
  | "production"
  | "pipeline"
  | "packets"
  | "diff"
  | "manuscript"
  | "ledger"
  | "docs"
  | "telemetry"
  | "settings";
export type Tab = "continuity" | "notes" | "changes";
export type Mode = "reading" | "suggesting" | "editing";
// Single source of truth lives with the wire DTOs; re-exported here so UI-state consumers (state.ts)
// and screens that talk to the API agree on the member set.
export type { DecisionKind } from "./api/types";
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
