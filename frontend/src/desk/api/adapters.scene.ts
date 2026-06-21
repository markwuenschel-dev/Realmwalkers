// Scene-specific API wire DTOs -> SceneScreen view-models. Pure functions, no React, no fetching —
// the same boundary discipline as ./adapters.ts (shared helpers re-exported from there). These map a
// live `GET /scenes/{id}` SceneDetail + its beat into the rail's conflict cards, the Notes list, the
// Changes list, and the review-pipeline rows that replace SceneScreen's old hardcoded fixtures.
import type {
  AnnotationOut,
  CharacterOut,
  Critique,
  SceneDetail,
  SuggestionOut,
} from "./client";
// BeatOut is not re-exported by the desk client; pull it from the same legacy DTO source the client
// re-exports from, keeping the wire-type boundary in one place.
import type { BeatOut } from "../../legacy/types";
import type { Marker } from "../types";
// Reuse the shared word-count helper (ignores rendered stat-box glyphs) rather than re-deriving it.
import { wordCount } from "./adapters";

export { wordCount };

// Reviewer names emitted by the worker lanes (src/dominion/workers/reviewers/*). The continuity
// reviewer is special: its hard-number mismatches feed the rail; everything else is advisory Notes.
const CONTINUITY = "continuity";

/** A continuity critique counts as a rail conflict only when its payload carries the hard-number
 * mismatch fields. Knowledge/POV advisories from the same reviewer lack these and stay in Notes. */
function isHardNumberConflict(c: Critique): boolean {
  if (c.reviewer !== CONTINUITY) return false;
  const p = c.payload;
  if (!p) return false;
  return p.prose_value != null && p.ledger_value != null;
}

function payloadStr(c: Critique, key: string): string {
  const v = c.payload?.[key];
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

// --- continuity rail ------------------------------------------------------------------------------

export interface ConflictCard {
  id: string;
  attribute: string;
  context: string;
  proseValue: string;
  ledgerValue: string;
}

/** Continuity critiques with hard-number mismatches -> the rail's conflict cards. The card id is the
 * critique id so the resolve handler can POST /scenes/{id}/continuity/resolve with `critique_id`. */
export function continuityConflicts(scene: SceneDetail | null): ConflictCard[] {
  if (!scene) return [];
  return scene.critiques.filter(isHardNumberConflict).map((c) => {
    const character = payloadStr(c, "character");
    const attribute = payloadStr(c, "attribute");
    return {
      id: c.id,
      // "Soren · level" style label; fall back to the reviewer if the payload is sparse.
      attribute: [character, attribute].filter(Boolean).join(" · ") || c.reviewer,
      context: payloadStr(c, "context_sentence") || c.note || "",
      proseValue: payloadStr(c, "prose_value"),
      ledgerValue: payloadStr(c, "ledger_value"),
    };
  });
}

// --- Notes tab ------------------------------------------------------------------------------------

export interface ReviewerNote {
  id: string;
  reviewer: string;
  severity: string;
  color: string;
  note: string;
}

const SEVERITY_LABEL: Record<string, string> = {
  hard: "hard",
  warn: "advisory",
  info: "info",
};

/** Every critique that is NOT a rail conflict -> the advisory Notes list. This is the non-continuity
 * reviewers (pacing/voice/state_drift/combat/sensory/dialogue) plus any continuity advisory (e.g. the
 * POV-knowledge warn flag) that lacks the hard-number payload. */
export function reviewerNotes(
  scene: SceneDetail | null,
  colors: { warn: string; info: string; good: string; bad: string },
): ReviewerNote[] {
  if (!scene) return [];
  return scene.critiques
    .filter((c) => !isHardNumberConflict(c))
    .map((c) => ({
      id: c.id,
      reviewer: c.reviewer,
      severity: SEVERITY_LABEL[c.severity] ?? c.severity,
      color: c.severity === "hard" ? colors.bad : c.severity === "warn" ? colors.warn : colors.info,
      note: (c.note ?? "").trim() || "(no note)",
    }));
}

// --- review pipeline row --------------------------------------------------------------------------

export interface PipelinePass {
  label: string;
  status: string;
  dot: string;
}

const SEVERITY_RANK: Record<string, number> = { info: 1, warn: 2, hard: 3 };

/** `passes_run` (the reviewers that actually ran) joined with each reviewer's worst critique severity.
 * "draft" is shown first as a completed step; each pass then reports its flag count + a status dot. */
export function pipelinePasses(
  scene: SceneDetail | null,
  colors: { good: string; warn: string; bad: string },
): PipelinePass[] {
  const draft: PipelinePass = { label: "draft", status: "done", dot: colors.good };
  if (!scene) return [draft];

  // worst severity + flag count per reviewer, from the critiques.
  const worst = new Map<string, string>();
  const counts = new Map<string, number>();
  for (const c of scene.critiques) {
    counts.set(c.reviewer, (counts.get(c.reviewer) ?? 0) + 1);
    const prev = worst.get(c.reviewer);
    if (!prev || (SEVERITY_RANK[c.severity] ?? 0) > (SEVERITY_RANK[prev] ?? 0)) {
      worst.set(c.reviewer, c.severity);
    }
  }

  const passes = (scene.passes_run ?? []).map((reviewer): PipelinePass => {
    const sev = worst.get(reviewer);
    const n = counts.get(reviewer) ?? 0;
    if (!sev || n === 0) return { label: reviewer, status: "clean", dot: colors.good };
    const noun = sev === "hard" ? "flag" : "note";
    const status = `${n} ${noun}${n === 1 ? "" : "s"}`;
    const dot = sev === "hard" ? colors.bad : sev === "warn" ? colors.warn : colors.good;
    return { label: reviewer, status, dot };
  });

  return [draft, ...passes];
}

// --- Changes tab ----------------------------------------------------------------------------------

export interface StateChange {
  key: string;
  glyph: string;
  color: string;
  label: string;
  detail: string;
}

/** The matching beat's `expected_state_changes` (a flat-ish key -> value map) -> the Changes rows.
 * Values may be scalars or small objects; we render a compact "key  value" line either way. */
export function stateChanges(
  beat: BeatOut | null,
  colors: { good: string; accent: string },
): StateChange[] {
  const changes = beat?.expected_state_changes;
  if (!changes) return [];
  return Object.entries(changes).map(([key, value]) => ({
    key,
    glyph: "△",
    color: colors.good,
    label: prettyLabel(key),
    detail: renderValue(value),
  }));
}

function prettyLabel(key: string): string {
  // "soren.level" / "soren_level" -> "Soren · level"
  const parts = key.split(/[.·]/).flatMap((p) => p.split("_")).filter(Boolean);
  if (parts.length === 0) return key;
  const [head, ...rest] = parts;
  const cap = head.charAt(0).toUpperCase() + head.slice(1);
  return rest.length ? `${cap} · ${rest.join(" ")}` : cap;
}

function renderValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    // common {from, to} delta shape -> "from → to"
    if ("from" in obj || "to" in obj) {
      return `${renderValue(obj.from)} → ${renderValue(obj.to)}`;
    }
    return Object.entries(obj)
      .map(([k, v]) => `${k} ${renderValue(v)}`)
      .join(", ");
  }
  return String(value);
}

// --- inline prose anchoring (graceful degradation) ------------------------------------------------

/** The substring to underline inline for a continuity conflict, if the live data supports it. The
 * worker emits `context_sentence` today; `span` is reserved for a later PR. Returns null when neither
 * is present so the prose simply renders un-annotated rather than guessing. */
export function conflictSpan(c: Critique): string | null {
  const span = payloadStr(c, "span");
  if (span) return span;
  const context = payloadStr(c, "context_sentence");
  if (context) return context;
  return null;
}

/** Find the beat for a scene by matching `scene_no`. The Changes tab needs the beat tied to THIS scene
 * (beats are fetched per chapter). */
export function beatForScene(beats: BeatOut[] | null, sceneNo: number): BeatOut | null {
  if (!beats) return null;
  return beats.find((b) => b.scene_no === sceneNo) ?? null;
}

// --- entity hover-cards (PR-B) --------------------------------------------------------------------

export interface EntityCard {
  id: string;
  name: string;
  role: string;
  rows: { k: string; v: string }[];
}

/** Live characters -> hover-card models, keyed by the name we anchor markers on. */
export function entityCards(characters: CharacterOut[]): Map<string, EntityCard> {
  const m = new Map<string, EntityCard>();
  for (const c of characters) {
    m.set(c.character, {
      id: c.character,
      name: c.character,
      role: c.role || "",
      rows: Object.entries(c.stats).map(([k, v]) => ({ k, v: String(v) })),
    });
  }
  return m;
}

/** Entity markers, assembled client-side from character names — anchored wherever the name occurs. */
export function entityMarkers(characters: CharacterOut[]): Marker[] {
  return characters
    .filter((c) => c.character.trim())
    .map((c) => ({ find: c.character, kind: "entity", id: c.character }));
}

// --- annotations (PR-C) ---------------------------------------------------------------------------

export interface NoteCard {
  id: string;
  quote: string;
  author: string;
  note: string;
}

export function annotationCards(anns: AnnotationOut[]): NoteCard[] {
  return anns.map((a) => ({
    id: a.id, quote: a.quote || "", author: a.author || "—", note: a.note || "",
  }));
}

/** `anno` markers anchored on each annotation's quote (skips quote-less notes). */
export function annoMarkers(anns: AnnotationOut[]): Marker[] {
  return anns
    .filter((a) => (a.quote || "").trim())
    .map((a) => ({ find: a.quote as string, kind: "anno", id: a.id }));
}

// --- suggestions / track-changes (PR-C) -----------------------------------------------------------

export interface SuggestionCard {
  id: string;
  author: string;
  why: string;
  old: string;
  neu: string;
  status: string;   // pending | accepted | rejected (server truth)
}

export function suggestionCards(suggs: SuggestionOut[]): SuggestionCard[] {
  return suggs.map((s) => ({
    id: s.id, author: s.author || "—", why: s.why || "",
    old: s.quote || "", neu: s.new_text || "", status: s.status,
  }));
}

/** `sugg` markers anchored on each suggestion's old text (skips suggestions with no anchor). */
export function suggMarkers(suggs: SuggestionOut[]): Marker[] {
  return suggs
    .filter((s) => (s.quote || "").trim())
    .map((s) => ({ find: s.quote as string, kind: "sugg", id: s.id }));
}
