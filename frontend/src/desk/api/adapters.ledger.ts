// Ledger view-model mappers (PR-B/PR-C): characters + canon + threads -> the desk's Ledger shapes.
import type { CanonOut, CharacterOut, ThreadOut } from "./client";
import type { LedgerCatDef, LedgerChar, Thread, ThreadBeat } from "../types";

/** A character's hard state -> a Ledger character card. */
export function toLedgerChar(c: CharacterOut): LedgerChar {
  return {
    initial: (c.character.trim()[0] || "?").toUpperCase(),
    name: c.character,
    role: c.role || "—",
    attrs: Object.entries(c.stats).map(([k, v]) => ({ k, v: String(v) })),
  };
}

/** A curated thread -> the Ledger thread card (beats: {scene_no,label,flag} -> {s,label,flag}). */
export function toLedgerThread(t: ThreadOut): Thread {
  const beats: ThreadBeat[] = (t.beats ?? []).map((b) => ({
    s: Number((b as { scene_no?: unknown }).scene_no ?? 0),
    label: String((b as { label?: unknown }).label ?? ""),
    flag: Boolean((b as { flag?: unknown }).flag),
  }));
  return {
    id: t.id,
    name: t.name,
    kind: t.kind || "thread",
    state: t.state || "—",
    note: t.note || "",
    beats,
  };
}

/** A canon entity -> a simple titled card (Ledger "Locations" / "Items"). */
export interface CanonCard {
  id: string;
  name: string;
  body: string;
}
export function toCanonCard(c: CanonOut): CanonCard {
  return { id: c.id, name: c.name || "Untitled", body: c.body || "" };
}

/** The sidebar categories with live counts. */
export function ledgerCats(counts: {
  characters: number;
  threads: number;
  locations: number;
  items: number;
}): LedgerCatDef[] {
  return [
    { id: "characters", label: "Characters", count: counts.characters },
    { id: "threads", label: "Threads", count: counts.threads },
    { id: "locations", label: "Locations", count: counts.locations },
    { id: "items", label: "Marks & items", count: counts.items },
  ];
}
