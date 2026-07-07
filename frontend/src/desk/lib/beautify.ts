// Manuscript-prose "beautify" pre-parse. Runs on scene prose BEFORE parseBlocks in the reading
// outputs (on-screen manuscript + Reader/Shunn DOCX) so hand-pasted text reads like a novel:
// hard-wrapped paragraphs are re-flowed, punctuation is typeset, and stray markdown escapes are
// stripped. Pure and non-destructive — the stored prose is never mutated.
//
// Structural blocks are passed through byte-for-byte, using the SAME detection parseBlocks uses
// (imported from ../prose): fenced ```/@interface, box-drawing stat windows, tables, lists, headings,
// horizontal rules, and blockquote callouts are line-significant and must never be unwrapped or
// re-punctuated. For agent prose (already one line per blank-line-separated paragraph) the re-flow is
// a no-op; only punctuation is normalized.

import { BOX, BQ, FENCE, FENCE_CLOSE, HEADING, HR, OL, UL } from "../prose";

const ESCAPABLE = new Set("\\`*_{}[]()#+-.!&<>|~\"'/:;=?@^$%".split(""));

/** Drop markdown escape backslashes before ASCII punctuation (`pass\!` → `pass!`, `R\&D` → `R&D`). */
function stripEscapes(s: string): string {
  return s.replace(/\\(.)/g, (m, c: string) => (ESCAPABLE.has(c) ? c : m));
}

/** Straight quotes → curly, `--`/`---` → em dash, `...` → ellipsis. */
function typeset(s: string): string {
  return s
    .replace(/-{2,3}/g, "—")
    .replace(/\.\.\./g, "…")
    .replace(/(^|[\s([{<—])"/g, "$1“")
    .replace(/"/g, "”")
    .replace(/(\p{L}|\p{N})'(\p{L})/gu, "$1’$2") // don't → don’t, keeper's → keeper’s
    .replace(/(^|[\s([{<—])'/g, "$1‘")
    .replace(/'/g, "’");
}

/** Apply text transforms only OUTSIDE inline `code` spans (backtick-delimited). */
function outsideCode(s: string, fn: (t: string) => string): string {
  return s
    .split(/(`[^`]*`)/g)
    .map((seg) => (seg.startsWith("`") && seg.endsWith("`") && seg.length >= 2 ? seg : fn(seg)))
    .join("");
}

function cleanProse(s: string): string {
  return outsideCode(s, (seg) => typeset(stripEscapes(seg)));
}

function isDelimiterRow(line: string): boolean {
  return /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-");
}

/** A blank-line-delimited run that must be preserved verbatim (never unwrapped or re-punctuated). */
function isStructuralRun(run: string[]): boolean {
  const first = run[0];
  if (
    BOX.test(first) ||
    HEADING.test(first) ||
    HR.test(first) ||
    UL.test(first) ||
    OL.test(first) ||
    BQ.test(first)
  ) {
    return true;
  }
  // pipe table = header row immediately followed by a delimiter row (matches parseBlocks)
  return run.length >= 2 && first.includes("|") && isDelimiterRow(run[1]);
}

export function beautify(input: string): string {
  const lines = input.replace(/\r\n?/g, "\n").split("\n");
  const segments: string[] = [];
  let i = 0;
  while (i < lines.length) {
    if (!lines[i].trim()) {
      i++;
      continue;
    }
    // Fenced block (```/@interface/code): verbatim, may span internal blank lines.
    if (FENCE.test(lines[i])) {
      const start = i;
      let j = i + 1;
      while (j < lines.length && !FENCE_CLOSE.test(lines[j])) j++;
      const closed = j < lines.length;
      segments.push(lines.slice(start, closed ? j + 1 : j).join("\n"));
      i = closed ? j + 1 : j;
      continue;
    }
    // A run of consecutive non-blank lines.
    const start = i;
    while (i < lines.length && lines[i].trim()) i++;
    const run = lines.slice(start, i);
    if (isStructuralRun(run)) {
      segments.push(run.join("\n")); // stat window / list / table / heading / rule / callout — verbatim
    } else {
      segments.push(cleanProse(run.join(" ").replace(/\s+/g, " ").trim())); // re-flow + typeset one paragraph
    }
  }
  return segments.join("\n\n");
}
