// The Reader export's production-sequence planner — PURE (no `docx` dependency) so it's unit-testable.
// It decides WHAT front matter the book format emits and in WHAT order: the generated pages (half-title,
// title page, Table of Contents) interleaved with the authored front-matter sections by their canonical
// publishing rank (shared/chapter_order.py ↔ labels.SECTION_ORDER), so Copyright → Dedication → CONTENTS
// → Preface fall in book order. Back matter needs no planning — it already sorts last by `position` and
// flows through the normal body walk. The Reader emitter (lib/docx.ts) renders each planned item.

import { GENERATED_SECTION, sectionRank } from "./labels";
import type { ExportPolicy } from "./presets";
import {
  spineChapters,
  type ManuscriptSpine,
  type SpineChapterNode,
  type SpineNode,
} from "./spine";

/** One planned front-matter emission: a generated page or an authored front-matter section chapter. */
export type ReaderFrontItem =
  | { type: "half_title" }
  | { type: "title_page" }
  | { type: "toc"; entries: string[] }
  | { type: "section"; node: SpineChapterNode };

export interface ReaderProductionPlan {
  /** Half-title, title page, authored front-matter sections and the Contents page, in canonical order. */
  front: ReaderFrontItem[];
  /** Everything after the front matter, in spine order (front-matter chapters removed — they're in `front`). */
  body: SpineNode[];
}

const isFrontMatter = (n: SpineNode): n is SpineChapterNode =>
  n.type === "chapter" && n.kind === "front_matter";

const chapterHasProse = (c: SpineChapterNode): boolean => c.scenes.some((s) => s.hasProse);

/**
 * Build the Reader emission plan (see module comment). Generated pages are policy-gated; the title page
 * is always present for the book format. Everything is sorted by canonical publishing rank, so a section
 * added later still lands in its correct slot.
 */
export function planReaderProduction(
  spine: ManuscriptSpine,
  policy: ExportPolicy,
): ReaderProductionPlan {
  const ranked: { rank: number; item: ReaderFrontItem }[] = [];

  if (policy.includeHalfTitle) {
    ranked.push({ rank: sectionRank(GENERATED_SECTION.halfTitle), item: { type: "half_title" } });
  }
  // The title page is intrinsic to the book format (not policy-gated); its rank places it after the
  // half-title and before any authored front matter.
  ranked.push({ rank: sectionRank(GENERATED_SECTION.titlePage), item: { type: "title_page" } });

  for (const node of spine.nodes) {
    if (isFrontMatter(node)) {
      ranked.push({ rank: sectionRank(node.sectionType), item: { type: "section", node } });
    }
  }

  if (policy.includeTableOfContents) {
    // Contents lists everything after it — prologue, chapters, epilogue, back matter — with prose only.
    const entries = spineChapters(spine)
      .filter((c) => c.kind !== "front_matter" && chapterHasProse(c))
      .map((c) => c.label);
    if (entries.length > 0) {
      ranked.push({
        rank: sectionRank(GENERATED_SECTION.tableOfContents),
        item: { type: "toc", entries },
      });
    }
  }

  // Stable sort by rank keeps equal-rank items in their discovery order (spine order for sections).
  ranked.sort((a, b) => a.rank - b.rank);

  return {
    front: ranked.map((r) => r.item),
    body: spine.nodes.filter((n) => !isFrontMatter(n)),
  };
}
