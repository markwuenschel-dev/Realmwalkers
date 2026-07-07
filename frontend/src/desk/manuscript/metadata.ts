// Renderer-neutral export/provenance metadata. Resolved ONCE from the book's persisted fields (+ the
// author name the export UI holds) and threaded through the whole pipeline. NO emitter hard-codes any
// of this — the old "BOOK ONE" / "Dominion Realm" / book:1 / litrpg_ui:true literals are gone. Absent
// fields stay `undefined`; NOTHING here defaults to a project identity, so a standalone or new-series
// book emits no series line rather than silently inheriting Dominion.

import type { ManuscriptOut } from "../api/types";

export interface ExportMetadata {
  title: string;
  /** e.g. "Dominion Realm"; undefined = standalone (no series line rendered). */
  series?: string;
  /** Position in the series; drives the spelled-out "BOOK ONE" line. undefined = omit. */
  bookNumber?: number;
  /** Real book subtitle (distinct from the reader-mode "in reading order" line, which is a preset
   *  default, not book metadata). */
  subtitle?: string;
  /** Shunn byline + manifest attribution; supplied by the export UI (persisted author name). */
  author?: string;
}

const ONES = [
  "Zero",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
  "Ten",
  "Eleven",
  "Twelve",
];

/** "BOOK ONE" for 1..12 (spelled-out is the print convention), "BOOK 13" beyond. Returns undefined for
 *  an absent/invalid number so callers can simply omit the line. */
export function bookNumberLabel(n: number | undefined): string | undefined {
  if (n == null || !Number.isInteger(n) || n < 0) return undefined;
  const word = n < ONES.length ? ONES[n] : String(n);
  return `BOOK ${word.toUpperCase()}`;
}

const clean = (s: string | null | undefined): string | undefined => {
  const t = (s ?? "").trim();
  return t || undefined;
};

/** Resolve ExportMetadata from a manuscript payload + the author name the export UI holds. Empty/null
 *  fields collapse to `undefined` (never a placeholder string), so downstream inclusion checks are a
 *  plain truthiness test. `title` is the only guaranteed field ("Untitled" fallback). */
export function resolveExportMetadata(
  ms: Pick<ManuscriptOut, "title" | "series" | "book_no" | "subtitle">,
  author?: string | null,
): ExportMetadata {
  return {
    title: clean(ms.title) ?? "Untitled",
    series: clean(ms.series),
    bookNumber: ms.book_no ?? undefined,
    subtitle: clean(ms.subtitle),
    author: clean(author),
  };
}
