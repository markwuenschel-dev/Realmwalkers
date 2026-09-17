// Place read-through anchors on a chapter snapshot for rendering.
//
// The server locates every quote (workers/read_through/anchors.py) and stores offsets in UTF-16 code
// units — exactly a JS string index — so this module only ever SLICES. It never searches the text
// for a quote: a search would silently pick the first occurrence of a repeated passage, which is the
// ambiguity the server deliberately refused to resolve. Instead:
// - every segment is checked with `text.slice(start, end) === segment.text`; a failure is reported
//   as a mismatch and that segment is not drawn;
// - an ambiguous anchor contributes every candidate placement, marked "candidate";
// - highlights from different anchors that cover the same text are both kept and reported.

import type { ReadThroughAnchorOut, ReadThroughNoteOut, ReadThroughSegmentOut } from "../api/types";

export type MarkKind = "located" | "candidate";

/** An anchor with the identity the wire shape lacks: the note it belongs to and its index there. */
export interface KeyedAnchor {
  anchorId: string;
  noteId: string;
  anchor: ReadThroughAnchorOut;
}

export interface Mark {
  anchorId: string;
  noteId: string;
  kind: MarkKind;
  /** 0 for a located anchor; the candidate index for an ambiguous one. */
  placement: number;
}

/** A run of paragraph text with every mark covering it. Offsets are absolute into the chapter. */
export interface Piece {
  start: number;
  end: number;
  text: string;
  marks: Mark[];
}

export interface Paragraph {
  index: number;
  /** Absolute offset of the paragraph's first code unit (the "\n" separators belong to no paragraph). */
  start: number;
  end: number;
  pieces: Piece[];
}

export interface Mismatch {
  anchorId: string;
  noteId: string;
  kind: MarkKind;
  placement: number;
  segment: number;
  start: number;
  end: number;
  expected: string;
  /** What the snapshot holds at [start, end); null when the offsets are out of range. */
  actual: string | null;
}

export interface Overlap {
  start: number;
  end: number;
  anchorIds: string[];
}

export interface AnchorLayout {
  paragraphs: Paragraph[];
  mismatches: Mismatch[];
  overlaps: Overlap[];
  /** anchorId → `${paragraphIndex}:${pieceIndex}` of its earliest drawn piece (the scroll target). */
  firstPiece: Map<string, string>;
}

export const anchorKey = (noteId: string, index: number): string => `${noteId}:${index}`;

/**
 * The chapter an anchor can actually be drawn in, or null when nothing can be highlighted.
 *
 * A book-note quote found in several chapters arrives as one ambiguous anchor per chapter, each with
 * its own `chapter_id` and that chapter's candidates. Rows written before that contract carry an
 * ambiguous anchor with `chapter_id: null` and no candidates — there is nothing to draw, so the UI
 * must neither offer a click nor claim a highlight for it.
 */
export function anchorHighlightChapter(
  note: Pick<ReadThroughNoteOut, "chapter_id">,
  anchor: ReadThroughAnchorOut,
): string | null {
  const chapterId = anchor.chapter_id ?? note.chapter_id ?? null;
  if (!chapterId) return null;
  if (anchor.state === "located") return (anchor.segments ?? []).length > 0 ? chapterId : null;
  if (anchor.state === "ambiguous") {
    return (anchor.candidates ?? []).some((c) => c.length > 0) ? chapterId : null;
  }
  return null;
}

/** Every anchor that lives in `chapterId`, whichever note (chapter or book) carries it. */
export function anchorsForChapter(
  notes: readonly ReadThroughNoteOut[],
  chapterId: string,
): KeyedAnchor[] {
  const out: KeyedAnchor[] = [];
  for (const note of notes) {
    note.anchors.forEach((anchor, i) => {
      if ((anchor.chapter_id ?? note.chapter_id) === chapterId) {
        out.push({ anchorId: anchorKey(note.id, i), noteId: note.id, anchor });
      }
    });
  }
  return out;
}

/** Split on "\n", recording each paragraph's absolute [start, end). Nothing is trimmed. */
export function splitParagraphs(text: string): { start: number; end: number }[] {
  const out: { start: number; end: number }[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === 10) {
      out.push({ start, end: i });
      start = i + 1;
    }
  }
  out.push({ start, end: text.length });
  return out;
}

interface Interval {
  start: number;
  end: number;
  mark: Mark;
}

function placements(anchor: ReadThroughAnchorOut): [MarkKind, ReadThroughSegmentOut[][]] | null {
  if (anchor.state === "located") return ["located", [anchor.segments ?? []]];
  if (anchor.state === "ambiguous") return ["candidate", anchor.candidates ?? []];
  return null; // unlocated (or an unknown state): nothing to draw
}

export function anchorSpans(text: string, anchors: readonly KeyedAnchor[]): AnchorLayout {
  const mismatches: Mismatch[] = [];
  const intervals: Interval[] = [];

  for (const { anchorId, noteId, anchor } of anchors) {
    const p = placements(anchor);
    if (!p) continue;
    const [kind, groups] = p;
    groups.forEach((segments, placement) => {
      segments.forEach((seg, segment) => {
        const mark: Mark = { anchorId, noteId, kind, placement };
        const inRange =
          Number.isInteger(seg.start) &&
          Number.isInteger(seg.end) &&
          seg.start >= 0 &&
          seg.start < seg.end &&
          seg.end <= text.length;
        const actual = inRange ? text.slice(seg.start, seg.end) : null;
        if (actual === null || actual !== seg.text) {
          mismatches.push({
            anchorId,
            noteId,
            kind,
            placement,
            segment,
            start: seg.start,
            end: seg.end,
            expected: seg.text,
            actual,
          });
          return;
        }
        intervals.push({ start: seg.start, end: seg.end, mark });
      });
    });
  }

  const bounds = splitParagraphs(text);
  const perParagraph: Interval[][] = bounds.map(() => []);
  for (const iv of intervals) {
    // Binary search for the paragraph holding iv.start, then walk forward: a segment that crosses
    // a "\n" becomes one clipped interval per paragraph it touches.
    let lo = 0;
    let hi = bounds.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (bounds[mid].start <= iv.start) lo = mid;
      else hi = mid - 1;
    }
    for (let pi = lo; pi < bounds.length && bounds[pi].start < iv.end; pi++) {
      const s = Math.max(iv.start, bounds[pi].start);
      const e = Math.min(iv.end, bounds[pi].end);
      if (s < e) perParagraph[pi].push({ start: s, end: e, mark: iv.mark });
    }
  }

  const firstPiece = new Map<string, string>();
  const paragraphs: Paragraph[] = bounds.map((b, index) => {
    const clipped = perParagraph[index];
    const points = new Set<number>([b.start, b.end]);
    for (const iv of clipped) {
      points.add(iv.start);
      points.add(iv.end);
    }
    const sorted = [...points].sort((x, y) => x - y);
    const pieces: Piece[] = [];
    for (let i = 0; i < sorted.length - 1; i++) {
      const start = sorted[i];
      const end = sorted[i + 1];
      const marks = clipped.filter((iv) => iv.start <= start && iv.end >= end).map((iv) => iv.mark);
      for (const m of marks) {
        if (!firstPiece.has(m.anchorId)) firstPiece.set(m.anchorId, `${index}:${pieces.length}`);
      }
      pieces.push({ start, end, text: text.slice(start, end), marks });
    }
    return { index, start: b.start, end: b.end, pieces };
  });

  return { paragraphs, mismatches, overlaps: findOverlaps(intervals), firstPiece };
}

/** Ranges where marks from two or more DIFFERENT anchors cover the same code units. */
function findOverlaps(intervals: readonly Interval[]): Overlap[] {
  const startsAt = new Map<number, string[]>();
  const endsAt = new Map<number, string[]>();
  for (const iv of intervals) {
    startsAt.set(iv.start, [...(startsAt.get(iv.start) ?? []), iv.mark.anchorId]);
    endsAt.set(iv.end, [...(endsAt.get(iv.end) ?? []), iv.mark.anchorId]);
  }
  const positions = [...new Set([...startsAt.keys(), ...endsAt.keys()])].sort((a, b) => a - b);
  const active = new Map<string, number>();
  const out: Overlap[] = [];
  let lastKey = "";
  for (let i = 0; i < positions.length - 1; i++) {
    const p = positions[i];
    for (const id of endsAt.get(p) ?? []) {
      const n = (active.get(id) ?? 0) - 1;
      if (n <= 0) active.delete(id);
      else active.set(id, n);
    }
    for (const id of startsAt.get(p) ?? []) active.set(id, (active.get(id) ?? 0) + 1);
    if (active.size < 2) continue;
    const ids = [...active.keys()].sort();
    const key = ids.join("|");
    const last = out[out.length - 1];
    if (last && last.end === p && key === lastKey) last.end = positions[i + 1];
    else out.push({ start: p, end: positions[i + 1], anchorIds: ids });
    lastKey = key;
  }
  return out;
}
