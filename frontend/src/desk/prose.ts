import type { Marker, MarkerKind } from "./types";

// seg() splits prose into paragraphs; tokenize() slices a paragraph around its annotation markers
// (entity names, continuity-flag spans). parseBlocks()/parseInline() are a compact Markdown subset
// for the reading view and the canon-doc viewer — see below.

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

// ── Block-level structure ────────────────────────────────────────────────────
// parseBlocks() segments prose/markdown into renderable blocks so the reading view
// (and the canon-doc viewer + the PDF path) draw tables, lists, callouts, and
// monospace stat windows instead of flattening everything into paragraphs. Pure:
// text in, blocks out, no I/O. The drafter's ```stat``` fences are pre-rendered to
// box-drawing art by the backend (workers/stat_render.py), so a stat window arrives
// as a run of box-drawing lines, not a fence — both forms are handled.

export type Align = "left" | "center" | "right";
export type Tone = "note" | "info" | "good" | "warn" | "bad";

export type UiRole =
  | "system"
  | "warning"
  | "combat"
  | "damage"
  | "healing"
  | "defense"
  | "resource"
  | "progression"
  | "xp"
  | "crafting"
  | "insight"
  | "corruption"
  | "name"
  | "vow"
  | "item";

export type MagicDomain =
  | "fire"
  | "water"
  | "air"
  | "earth"
  | "light"
  | "shadow"
  | "life"
  | "death"
  | "runic"
  | "blood"
  | "spirit"
  | "mind"
  | "force"
  | "chaos"
  | "celestial"
  | "void"
  | "planar"
  | "time"
  | "entropy"
  | "eldritch"
  | "aether";

export type CreatureKind =
  | "mortal"
  | "beast"
  | "monster"
  | "demon"
  | "archdemon"
  | "angel"
  | "archangel"
  | "undead"
  | "dragon"
  | "construct"
  | "spirit"
  | "fae"
  | "celestial"
  | "voidborn"
  | "eldritch"
  | "xyloryn"
  | "nhal";

export type Intensity = "subtle" | "standard" | "strong" | "apex";

export type InterfaceSpec = {
  role?: UiRole;
  domain?: MagicDomain;
  creature?: CreatureKind;
  intensity?: Intensity;
  skill?: string;
  tier?: string;
};

export type ProseBlock =
  | { kind: "p"; text: string; n: number }
  | { kind: "heading"; level: number; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "callout"; tone: Tone; title: string | null; lines: string[] }
  | { kind: "hr" }
  | { kind: "stat"; lines: string[] } // pre-rendered box-drawing window
  | { kind: "code"; lines: string[]; lang: string } // ``` fenced block
  | { kind: "interface"; spec: InterfaceSpec; lines: string[] } // ``` + @interface directive
  | { kind: "table"; head: string[]; rows: string[][]; align: Align[] };

const BOX = /^\s*[┌│├└]/; // first non-space char of a rendered stat-window line
const FENCE = /^\s*```/;
const FENCE_CLOSE = /^\s*```\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const HR = /^\s*([-*_])(?:\s*\1){2,}\s*$/; // ---, ***, ___, - - -
const UL = /^\s*[-*+]\s+(.*)$/;
const OL = /^\s*\d+[.)]\s+(.*)$/;
const BQ = /^\s*>\s?(.*)$/;

// GitHub admonitions + the Realmwalkers status tags → a callout tone (DESIGN export note §2/§5).
const ADMON: Record<string, Tone> = {
  note: "note",
  tip: "good",
  important: "info",
  info: "info",
  warning: "warn",
  caution: "bad",
  danger: "bad",
  lock: "note",
  working: "info",
  open: "warn",
  override: "bad",
  decision: "note",
  halt: "bad",
  fail: "bad",
  pass: "good",
};

// A GFM table cell: outer pipes stripped, split on the rest. We do not unescape "\|"
// (no canon table escapes pipes today); add that here if one ever does.
function splitCells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

// A delimiter row separates a pipe table's header from its body: every cell is dashes
// with optional leading/trailing colons (alignment). Requires a pipe, so a bare "---"
// thematic break is never mistaken for a one-column table.
function isDelimiter(line: string): boolean {
  if (!line.includes("|")) return false;
  const cells = splitCells(line);
  return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c));
}

function alignOf(cell: string): Align {
  const l = cell.startsWith(":");
  const r = cell.endsWith(":");
  return l && r ? "center" : r ? "right" : "left";
}

function makeCallout(inner: string[]): ProseBlock {
  let tone: Tone = "note";
  let title: string | null = null;
  let lines = inner.slice();
  const first = (lines[0] ?? "").trim();
  let m = /^\[!(\w+)\]\s*(.*)$/.exec(first); // GitHub admonition: > [!WARNING]
  if (m) {
    tone = ADMON[m[1].toLowerCase()] ?? "note";
    title = m[1][0].toUpperCase() + m[1].slice(1).toLowerCase();
    lines = m[2] ? [m[2], ...lines.slice(1)] : lines.slice(1);
  } else if ((m = /^\[(LOCK|WORKING|OPEN|OVERRIDE)\]/i.exec(first))) {
    tone = ADMON[m[1].toLowerCase()] ?? "note";
    title = m[1].toUpperCase();
  }
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return { kind: "callout", tone, title, lines };
}

function collect(lines: string[], i: number, re: RegExp): [string[], number] {
  const items: string[] = [];
  while (i < lines.length && re.test(lines[i])) {
    items.push(lines[i].replace(re, "$1"));
    i++;
  }
  return [items, i];
}

const INTERFACE_DIRECTIVE = /^@interface\s+(.+)$/;

const UI_ROLES = new Set<UiRole>([
  "system",
  "warning",
  "combat",
  "damage",
  "healing",
  "defense",
  "resource",
  "progression",
  "xp",
  "crafting",
  "insight",
  "corruption",
  "name",
  "vow",
  "item",
]);

const MAGIC_DOMAINS = new Set<MagicDomain>([
  "fire",
  "water",
  "air",
  "earth",
  "light",
  "shadow",
  "life",
  "death",
  "runic",
  "blood",
  "spirit",
  "mind",
  "force",
  "chaos",
  "celestial",
  "void",
  "planar",
  "time",
  "entropy",
  "eldritch",
  "aether",
]);

const CREATURE_KINDS = new Set<CreatureKind>([
  "mortal",
  "beast",
  "monster",
  "demon",
  "archdemon",
  "angel",
  "archangel",
  "undead",
  "dragon",
  "construct",
  "spirit",
  "fae",
  "celestial",
  "voidborn",
  "eldritch",
  "xyloryn",
  "nhal",
]);

const INTENSITIES = new Set<Intensity>(["subtle", "standard", "strong", "apex"]);

function asEnum<T extends string>(value: string, allowed: Set<T>): T | undefined {
  return allowed.has(value as T) ? (value as T) : undefined;
}

/** Parse `@interface role=insight creature=archdemon …` into a typed InterfaceSpec. */
export function parseInterfaceSpec(raw: string): InterfaceSpec {
  const spec: InterfaceSpec = {};
  for (const part of raw.trim().split(/\s+/)) {
    const eq = part.indexOf("=");
    if (eq <= 0) continue;
    const key = part.slice(0, eq);
    const value = part.slice(eq + 1);
    switch (key) {
      case "role":
        spec.role = asEnum(value, UI_ROLES);
        break;
      case "domain":
        spec.domain = asEnum(value, MAGIC_DOMAINS);
        break;
      case "creature":
        spec.creature = asEnum(value, CREATURE_KINDS);
        break;
      case "intensity":
        spec.intensity = asEnum(value, INTENSITIES);
        break;
      case "skill":
        spec.skill = value;
        break;
      case "tier":
        spec.tier = value;
        break;
    }
  }
  return spec;
}

export function parseBlocks(text: string): ProseBlock[] {
  const out: ProseBlock[] = [];
  const lines = text.split("\n");
  let p = 0; // paragraph index — drop-cap parity with seg()
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }

    // fenced code block — collect to the closing fence (or EOF if unterminated)
    if (FENCE.test(line)) {
      const lang = line.trim().replace(/^`+/, "").trim();
      const start = i + 1;
      let j = start;
      while (j < lines.length && !FENCE_CLOSE.test(lines[j])) j++;
      const inner = lines.slice(start, j);
      const iface = INTERFACE_DIRECTIVE.exec((inner[0] ?? "").trim());
      if (iface) {
        out.push({
          kind: "interface",
          spec: parseInterfaceSpec(iface[1]),
          lines: inner.slice(1),
        });
      } else {
        out.push({ kind: "code", lines: inner, lang });
      }
      i = j < lines.length ? j + 1 : j; // step past the closing fence
      continue;
    }

    const h = HEADING.exec(line);
    if (h) {
      out.push({ kind: "heading", level: h[1].length, text: h[2].trim() });
      i++;
      continue;
    }

    if (HR.test(line)) {
      out.push({ kind: "hr" });
      i++;
      continue;
    }

    // stat window — a contiguous run of box-drawing lines
    if (BOX.test(line)) {
      const start = i;
      while (i < lines.length && BOX.test(lines[i])) i++;
      out.push({ kind: "stat", lines: lines.slice(start, i) });
      continue;
    }

    // blockquote → callout box
    if (BQ.test(line)) {
      const [inner, next] = collect(lines, i, BQ);
      i = next;
      out.push(makeCallout(inner));
      continue;
    }

    // pipe table — a header row immediately followed by a delimiter row
    if (line.includes("|") && i + 1 < lines.length && isDelimiter(lines[i + 1])) {
      const head = splitCells(line);
      const align = splitCells(lines[i + 1]).map(alignOf);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitCells(lines[i]));
        i++;
      }
      out.push({ kind: "table", head, rows, align });
      continue;
    }

    // lists (one level; nested sublists are flattened in v1)
    if (UL.test(line)) {
      const [items, next] = collect(lines, i, UL);
      i = next;
      out.push({ kind: "ul", items });
      continue;
    }
    if (OL.test(line)) {
      const [items, next] = collect(lines, i, OL);
      i = next;
      out.push({ kind: "ol", items });
      continue;
    }

    // ordinary paragraph — one per non-blank line, matching seg()
    out.push({ kind: "p", text: line.trim(), n: p++ });
    i++;
  }
  return out;
}

// ── Inline formatting ────────────────────────────────────────────────────────
// A flat (non-nesting) inline pass over a single line: `code`, **strong**, *em*,
// and [text](href). Enough to render the canon docs (heavy on bold/code/links)
// and italics in prose without pulling in a Markdown dependency.

export type Inline =
  | { t: "text"; s: string }
  | { t: "code"; s: string }
  | { t: "strong"; s: string }
  | { t: "em"; s: string }
  | { t: "link"; s: string; href: string };

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ t: "text", s: buf });
    buf = "";
  };
  let i = 0;
  while (i < text.length) {
    const rest = text.slice(i);
    let m: RegExpExecArray | null;
    if ((m = /^`([^`]+)`/.exec(rest))) {
      flush();
      out.push({ t: "code", s: m[1] });
    } else if ((m = /^\*\*([^*]+)\*\*/.exec(rest)) || (m = /^__([^_]+)__/.exec(rest))) {
      flush();
      out.push({ t: "strong", s: m[1] });
    } else if ((m = /^\[([^\]]+)\]\(([^)]+)\)/.exec(rest))) {
      flush();
      out.push({ t: "link", s: m[1], href: m[2] });
    } else if ((m = /^\*([^*\s](?:[^*]*[^*\s])?)\*/.exec(rest))) {
      flush();
      out.push({ t: "em", s: m[1] });
    } else if (
      // underscore emphasis only at word boundaries — avoids snake_case false positives
      (i === 0 || !/\w/.test(text[i - 1])) &&
      (m = /^_([^_\s](?:[^_]*[^_\s])?)_(?!\w)/.exec(rest))
    ) {
      flush();
      out.push({ t: "em", s: m[1] });
    } else {
      buf += text[i];
      i += 1;
      continue;
    }
    i += m[0].length;
  }
  flush();
  return out;
}

export type Token = { kind: "text"; text: string } | { kind: MarkerKind; id: string; text: string };

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
