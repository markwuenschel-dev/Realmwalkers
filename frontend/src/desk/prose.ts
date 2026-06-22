import type { Marker, MarkerKind } from "./types";

// seg() splits prose into paragraphs; tokenize() slices a paragraph around its annotation markers
// (entity names, continuity-flag spans) so they can be rendered as hover-cards / highlights.

export type Block = { kind: "p"; text: string; n: number };

export function seg(text: string): Block[] {
  const blocks: Block[] = [];
  let idx = 0;
  for (const ln of text.split(/\n+/)) {
    const t = ln.trim();
    if (!t) continue;
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
