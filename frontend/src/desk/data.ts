import type {
  Annotation,
  BoardScene,
  Chapter,
  ConflictDef,
  DiffRowData,
  Entity,
  LedgerCatDef,
  LedgerChar,
  Marker,
  MsChapter,
  QueueScene,
  StatDef,
  Suggestion,
  Thread,
  TimelineScene,
} from "./types";

// All demo fixtures, copied 1:1 from the prototype's DCLogic class fields and renderVals() locals.

// Review queue for j / k navigation.
export const QUEUE: QueueScene[] = [
  { no: 7, title: "The Warded Door", words: "612", version: 3, status: "awaiting" },
  { no: 8, title: "What the Seal Kept", words: "487", version: 1, status: "awaiting" },
  { no: 6, title: "Vael's Ledger", words: "610", version: 2, status: "note" },
];

export const BOARD_SCENES: Record<string, BoardScene> = {
  sc5: { no: 5, title: "Threadbound", words: 540, status: "approved" },
  sc6: { no: 6, title: "Vael's Ledger", words: 610, status: "approved" },
  sc7: { no: 7, title: "The Warded Door", words: 612, status: "awaiting" },
  sc8: { no: 8, title: "What the Seal Kept", words: 487, status: "awaiting" },
  sc9: { no: 9, title: "The Stairwell Descent", words: 0, status: "drafting" },
};

export const INITIAL_BOARD = ["sc5", "sc6", "sc7", "sc8", "sc9"];

export const CHAPTERS: Chapter[] = [
  { no: 1, title: "Threadbound", pov: "Soren", target: 1800, words: 1740, approved: 1.0 },
  { no: 2, title: "The Warded Door", pov: "Soren", target: 2100, words: 1290, approved: 0.4 },
  { no: 3, title: "What the Oracle Kept", pov: "Lyra", target: 1900, words: 0, approved: 0 },
];

// Every scene placed on the timeline, by chapter and POV lane.
export const TIMELINE_SCENES: TimelineScene[] = [
  { n: 1, ch: 1, pov: "Soren", status: "approved", flags: 0 },
  { n: 2, ch: 1, pov: "Soren", status: "approved", flags: 0 },
  { n: 3, ch: 1, pov: "Soren", status: "approved", flags: 0 },
  { n: 4, ch: 1, pov: "Soren", status: "approved", flags: 0 },
  { n: 5, ch: 2, pov: "Soren", status: "approved", flags: 0 },
  { n: 6, ch: 2, pov: "Soren", status: "approved", flags: 0 },
  { n: 7, ch: 2, pov: "Soren", status: "awaiting", flags: 2 },
  { n: 8, ch: 2, pov: "Lyra", status: "awaiting", flags: 0 },
  { n: 9, ch: 2, pov: "Soren", status: "drafting", flags: 0 },
  { n: 10, ch: 3, pov: "Lyra", status: "planned", flags: 0 },
  { n: 11, ch: 3, pov: "Lyra", status: "planned", flags: 0 },
];

export const LANES = ["Soren", "Lyra"];

// Plot / relationship threads followed across scenes.
export const LEDGER_THREADS: Thread[] = [
  {
    id: "t1", name: "Soren ⇄ Lyra", kind: "relationship", state: "sealed",
    note: "The sibling bond the ward severed — Soren's emotional spine.",
    beats: [
      { s: 1, label: "oath bound" }, { s: 5, label: "threadbound" }, { s: 7, label: "seal felt" },
      { s: 8, label: "her side", flag: true }, { s: 10, label: "reunion?" },
    ],
  },
  {
    id: "t2", name: "Soren ⇄ Master Vael", kind: "mentorship", state: "active",
    note: "Who taught the oath, and what he hasn't told Soren about its cost.",
    beats: [{ s: 1, label: "the cutting" }, { s: 6, label: "the ledger" }, { s: 7, label: "the scar" }],
  },
  {
    id: "t3", name: "The Oracle's Ledger", kind: "system", state: "contested",
    note: "The system-of-record itself — source of every continuity check.",
    beats: [
      { s: 3, label: "keeper wakes" }, { s: 7, label: "timing flag", flag: true },
      { s: 8, label: "thread flag", flag: true },
    ],
  },
  {
    id: "t4", name: "Ember Affinity", kind: "power", state: "rising",
    note: "Soren's magic line — escalates toward the Chapter 3 confrontation.",
    beats: [{ s: 4, label: "first spark" }, { s: 7, label: "ascension" }, { s: 9, label: "overflow" }],
  },
];

export const INITIAL_PROSE =
  `The corridor breathed cold around him. Soren pressed his palm to the warded door and the sigils woke — a slow amber pulse that had learned his blood three winters ago, when Master Vael first cut the oath into his hand.\n[BOX]\nThe number settled behind his eyes like a second heartbeat. He had chased it for a year, and now that it had come he felt only the draft from the stairwell and the weight of Lyra's silence on the other side of the seal.\n"You can stop pretending you don't hear me," she said. Her voice came through the ward thinned, as if read from a page. "They keyed it to you. Not to me."`;

// Canon facts surfaced as hover-cards over names in the prose.
export const ENTITIES: Record<string, Entity> = {
  soren: {
    name: "Soren Valecrest", role: "Ascendant · POV",
    rows: [["level", "15"], ["affinity", "Ember · Ward"], ["mana", "412 / 480"], ["marks", "Oathkeeper"]],
  },
  lyra: {
    name: "Lyra Valecrest", role: "Thread-sister", conflict: "c2",
    rows: [["status", "sealed behind ward"], ["last seen", "Scene 5"], ["thread", "Soren (active)"]],
  },
  vael: {
    name: "Master Vael", role: "Warden of oaths",
    rows: [["domain", "ledger-craft"], ["first seen", "Scene 1"], ["marks", "Keeper"]],
  },
};

// Tracked suggestions (track-changes): old text → new text.
export const SUGGESTIONS: Record<string, Suggestion> = {
  g1: { author: "Vael · editor", old: "palm", neu: "scarred palm", why: "plant the oath-scar earlier" },
  g2: { author: "You · pacing", old: "the draft from the stairwell and ", neu: "", why: "trims a repeated cold/draft beat" },
};

// Which spans in each paragraph carry an entity, conflict, annotation, or suggestion.
export const MARKERS: Record<number, Marker[]> = {
  0: [
    { find: "Soren", kind: "entity", id: "soren" },
    { find: "palm", kind: "sugg", id: "g1" },
    { find: "three winters ago", kind: "conflict", id: "c1" },
    { find: "Master Vael", kind: "entity", id: "vael" },
  ],
  1: [
    { find: "second heartbeat", kind: "anno", id: "a1" },
    { find: "the draft from the stairwell and ", kind: "sugg", id: "g2" },
    { find: "Lyra", kind: "entity", id: "lyra" },
  ],
};

// Continuity conflicts (shared by the inline hover cards and the rail's Continuity tab).
export const CONFLICTS: Record<string, ConflictDef> = {
  c1: {
    attribute: "oath · timing", context: "…that had learned his blood three winters ago",
    proseValue: "three winters", ledgerValue: "two winters",
  },
  c2: {
    attribute: "thread · Lyra", context: "Threadbound  Lyra (sealed)",
    proseValue: "sealed", ledgerValue: "active",
  },
};
export const CONFLICT_IDS = ["c1", "c2"];

// Single margin annotation (a1).
export const ANNOTATION: Annotation = {
  id: "a1", quote: "second heartbeat", author: "Vael · editor",
  note: "Echoes the chapter 1 'borrowed pulse' image — strong, but is it too soon to repeat the motif?",
};

// Inbox stats.
export const STATS: StatDef[] = [
  { label: "Manuscript", value: "4,210", suffix: "/ 8,000 words", hasBar: true, pct: "53%" },
  { label: "Scenes approved", value: "5", suffix: "/ 9 planned", hasBar: true, pct: "55%" },
  { label: "Awaiting you", value: "3", suffix: "scenes", note: "2 with continuity flags" },
  { label: "Oracle drafting", value: "1", suffix: "in progress", note: "Scene 9 · ~2 min left" },
];

// Versions / diff rows (v2 → v3).
export const DIFF_ROWS: DiffRowData[] = [
  { type: "same", l: "The corridor breathed cold around him.", r: "The corridor breathed cold around him." },
  { type: "change", l: "Soren pressed his hand to the door and the sigils woke.", r: "Soren pressed his palm to the warded door and the sigils woke —" },
  { type: "add", l: "", r: "a slow amber pulse that had learned his blood three winters ago," },
  { type: "change", l: "when Vael cut the oath.", r: "when Master Vael first cut the oath into his hand." },
  { type: "same", l: "", r: "" },
  { type: "change", l: "│ Mana          412 / 440 │", r: "│ Mana          412 / 480 │" },
  { type: "del", l: "He felt nothing in particular.", r: "" },
  { type: "change", l: "The number settled behind his eyes.", r: "The number settled behind his eyes like a second heartbeat." },
  { type: "add", l: "", r: "He had chased it for a year." },
  { type: "change", l: '"They keyed it to you."', r: '"They keyed it to you. Not to me."' },
];

// Manuscript reader.
export const MS_CHAPTERS: MsChapter[] = [
  {
    no: 1, title: "Threadbound", pov: "Soren",
    paras: [
      "They cut the oath into his hand on the coldest night of the year, and for the length of one breath Soren believed the cold was the worst of it. Then the ledger opened behind his eyes — not a vision, not a voice, only a column of facts he had never been told and could not now unknow — and he understood that the cold had only been the door.",
      'Vael held the knife to the candle until the blood on it dried to nothing. "It will ask you for things," the old warden said. "It will tell you what you are worth. Do not believe the number is you."',
      "But the number was already there, patient as a second heartbeat, and Soren was sixteen and the number said that he was small.",
    ],
  },
  {
    no: 2, title: "The Warded Door", pov: "Soren",
    paras: [
      "The corridor breathed cold around him. Soren pressed his palm to the warded door and the sigils woke — a slow amber pulse that had learned his blood three winters ago, when Master Vael first cut the oath into his hand.",
      "The number settled behind his eyes like a second heartbeat. He had chased it for a year, and now that it had come he felt only the draft from the stairwell and the weight of Lyra's silence on the other side of the seal.",
    ],
  },
];

// World ledger.
export const LEDGER_CATS: LedgerCatDef[] = [
  { id: "characters", label: "Characters", count: 7 },
  { id: "threads", label: "Threads", count: 4 },
  { id: "locations", label: "Locations", count: 5 },
  { id: "items", label: "Marks & items", count: 9 },
];

export const LEDGER_CHARS: LedgerChar[] = [
  {
    initial: "S", name: "Soren Valecrest", role: "Ascendant · POV",
    attrs: [
      { k: "level", v: "15" }, { k: "affinity", v: "Ember · Ward" }, { k: "mana", v: "412 / 480" },
      { k: "marks", v: "Oathkeeper, Emberborn" }, { k: "threads", v: "Lyra (sealed)" },
    ],
  },
  {
    initial: "L", name: "Lyra Valecrest", role: "Thread-sister",
    attrs: [
      { k: "level", v: "—" }, { k: "status", v: "sealed behind ward" }, { k: "last seen", v: "Scene 5" },
      { k: "threads", v: "Soren (active)" },
    ],
  },
  {
    initial: "V", name: "Master Vael", role: "Warden of oaths",
    attrs: [
      { k: "role", v: "mentor" }, { k: "domain", v: "ledger-craft" }, { k: "first seen", v: "Scene 1" },
      { k: "marks", v: "Keeper" },
    ],
  },
  {
    initial: "K", name: "Kestrel", role: "Unbound",
    attrs: [
      { k: "level", v: "12" }, { k: "affinity", v: "Storm" }, { k: "allegiance", v: "unknown" },
      { k: "first seen", v: "Scene 8" },
    ],
  },
];
