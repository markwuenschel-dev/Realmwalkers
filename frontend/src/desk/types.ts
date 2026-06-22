// UI-state types for the Writers' Desk. (Server/wire types live in desk/api/types.ts; the Desk no
// longer carries any fixture data interfaces — every screen reads live data.)

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
