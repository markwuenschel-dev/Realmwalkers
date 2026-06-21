// Inbox-screen mappers: the book's scenes → the four status columns and the STATS tiles.
import { sceneTitle, wordCount } from "./adapters";
import { deskStatus } from "./adapters.chapters";
import type { SceneOut } from "./client";
import type { StatDef } from "../types";

export interface InboxCard {
  id: string;
  no: number;
  version: number;
  title: string;
  words: string;
}

export interface InboxColumn {
  key: "drafting" | "awaiting" | "revising" | "approved";
  title: string;
  cards: InboxCard[];
}

function toCard(s: SceneOut): InboxCard {
  return { id: s.id, no: s.scene_no, version: s.version, title: sceneTitle(s), words: wordCount(s.prose) + "w" };
}

// Group the gathered (latest-per-scene) scenes into the four board columns by display status.
// `superseded` scenes are excluded upstream by latest-per-scene selection / never surface here.
export function inboxColumns(scenes: SceneOut[]): InboxColumn[] {
  const cols: Record<InboxColumn["key"], InboxCard[]> = {
    drafting: [],
    awaiting: [],
    revising: [],
    approved: [],
  };
  for (const s of scenes) {
    if (s.status === "superseded") continue;
    const ds = deskStatus(s.status);
    if (ds === "drafting") cols.drafting.push(toCard(s));
    else if (ds === "awaiting") cols.awaiting.push(toCard(s));
    else if (ds === "revising") cols.revising.push(toCard(s));
    else if (ds === "approved") cols.approved.push(toCard(s));
  }
  const order = (c: InboxCard[]) => c.sort((a, b) => a.no - b.no);
  return [
    { key: "drafting", title: "Drafting", cards: order(cols.drafting) },
    { key: "awaiting", title: "Awaiting review", cards: order(cols.awaiting) },
    { key: "revising", title: "Revising", cards: order(cols.revising) },
    { key: "approved", title: "Approved", cards: order(cols.approved) },
  ];
}

// The scenes awaiting the writer, in the same order the board column shows them — used to map a card
// click to its index in the review queue (see openScene).
export function awaitingQueue(scenes: SceneOut[]): SceneOut[] {
  return scenes
    .filter((s) => deskStatus(s.status) === "awaiting" && s.status !== "superseded")
    .sort((a, b) => a.scene_no - b.scene_no);
}

// STATS tiles computed from the gathered data. We compute what the scene list supports (approval
// counts, awaiting count, word totals) and omit what it can't back (there is no word target or
// per-scene ETA in this data), rather than fabricate.
export function inboxStats(scenes: SceneOut[]): StatDef[] {
  const live = scenes.filter((s) => s.status !== "superseded");
  const planned = live.length;
  const approved = live.filter((s) => deskStatus(s.status) === "approved").length;
  const awaiting = live.filter((s) => deskStatus(s.status) === "awaiting").length;
  const drafting = live.filter((s) => deskStatus(s.status) === "drafting").length;
  const words = live.reduce((a, s) => a + wordCount(s.prose), 0);
  const approvedPct = planned ? Math.round((approved / planned) * 100) : 0;

  const stats: StatDef[] = [
    { label: "Manuscript", value: words.toLocaleString(), suffix: "words" },
    {
      label: "Scenes approved",
      value: String(approved),
      suffix: `/ ${planned} planned`,
      hasBar: true,
      pct: approvedPct + "%",
    },
    { label: "Awaiting you", value: String(awaiting), suffix: awaiting === 1 ? "scene" : "scenes" },
    { label: "Oracle drafting", value: String(drafting), suffix: "in progress" },
  ];
  return stats;
}
