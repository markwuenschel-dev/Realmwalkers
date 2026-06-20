import type { Marker, MarkerKind } from "./types";

// The drafter emits an aligned Unicode stat box inside the prose; it only reads as aligned in a
// monospace context. box() rebuilds it, seg() splits prose into paragraphs + the [BOX] placeholder,
// and tokenize() slices a paragraph around its annotation markers. All copied 1:1 from the prototype.

export function box(): string {
  const inner = 46;
  const rows: [string, string][] = [
    ["Bearer", "Soren Valecrest"],
    ["Level", "14  →  15"],
    ["Mana", "412 / 480"],
    ["Affinity", "Ember · Ward"],
    ["Threadbound", "Lyra  (sealed)"],
    ["Marks", "Oathkeeper, Emberborn"],
  ];
  const title = "ASCENDANT · THREAD-LEDGER";
  const top = "┌" + ("─ " + title + " ").padEnd(inner, "─") + "┐";
  const body = rows.map(([k, v]) => "│ " + (k.padEnd(14) + v).padEnd(inner - 2) + " │");
  const bot = "└" + "─".repeat(inner) + "┘";
  return [top, ...body, bot].join("\n");
}

export type Block = { kind: "box" } | { kind: "p"; text: string; n: number };

export function seg(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split("\n");
  let idx = 0;
  for (const ln of lines) {
    const t = ln.trim();
    if (!t) continue;
    if (t === "[BOX]") {
      blocks.push({ kind: "box" });
      continue;
    }
    blocks.push({ kind: "p", text: t, n: idx++ });
  }
  return blocks;
}

export type Token =
  | { kind: "text"; text: string }
  | { kind: MarkerKind; id: string; text: string };

export function tokenize(text: string, ms?: Marker[]): Token[] {
  const found: (Marker & { i: number; end: number })[] = [];
  (ms || []).forEach((m) => {
    const i = text.indexOf(m.find);
    if (i >= 0) found.push({ ...m, i, end: i + m.find.length });
  });
  found.sort((a, b) => a.i - b.i);

  const out: Token[] = [];
  let cur = 0;
  for (const m of found) {
    if (m.i < cur) continue;
    if (m.i > cur) out.push({ kind: "text", text: text.slice(cur, m.i) });
    out.push({ kind: m.kind, id: m.id, text: text.slice(m.i, m.end) });
    cur = m.end;
  }
  if (cur < text.length) out.push({ kind: "text", text: text.slice(cur) });
  return out;
}
