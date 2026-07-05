// The ONE severity→presentation map for the unified vocabulary (info | warn | repair | block).
// Replaces the per-screen maps that disagreed with each other (one spoke hard/warn/info, another
// block/warn/info with no `repair` key). Legacy rows and JSON snapshots inside artifact bodies still
// say "hard" — the pre-unification spelling of block — so it renders identically forever.
import type { ChipTone } from "../components/ui";

export function severityChipTone(severity: string): ChipTone {
  switch (severity) {
    case "block":
    case "hard":
      return "bad";
    case "repair":
    case "warn":
      return "warn";
    default:
      return "info";
  }
}

/** CSS var name (without `var()`) for severity accents. `fallback` covers unknown/absent severities
 * so call sites keep their local default (e.g. deterministic-validation lists assume warn). */
export function severityVar(severity: string, fallback = "--dim"): string {
  switch (severity) {
    case "block":
    case "hard":
      return "--bad";
    case "repair":
    case "warn":
      return "--warn";
    case "info":
      return "--dim";
    default:
      return fallback;
  }
}
