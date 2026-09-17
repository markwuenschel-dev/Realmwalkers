import { describe, expect, it } from "vitest";
import type { ReadThroughAnchorOut } from "../api/types";
import {
  anchorHighlightChapter,
  anchorSpans,
  anchorsForChapter,
  splitParagraphs,
  type KeyedAnchor,
} from "./anchorSpans";

// Offsets in these fixtures are built by string concatenation (`prefix.length`), never by searching
// the text — the module under test must not search either.

const located = (start: number, end: number, text: string): ReadThroughAnchorOut => ({
  chapter_id: "ch1",
  state: "located",
  text_quoted: text,
  segments: [{ start, end, text }],
  candidates: [],
  candidate_count: 0,
});

const keyed = (anchorId: string, anchor: ReadThroughAnchorOut): KeyedAnchor => ({
  anchorId,
  noteId: anchorId.split(":")[0],
  anchor,
});

const markedText = (layout: ReturnType<typeof anchorSpans>) =>
  layout.paragraphs.flatMap((p) =>
    p.pieces.filter((x) => x.marks.length > 0).map((x) => ({ paragraph: p.index, text: x.text })),
  );

describe("splitParagraphs", () => {
  it("keeps absolute offsets, including empty lines and a trailing newline", () => {
    expect(splitParagraphs("ab\n\ncd\n")).toEqual([
      { start: 0, end: 2 },
      { start: 3, end: 3 },
      { start: 4, end: 6 },
      { start: 7, end: 7 },
    ]);
  });
});

describe("anchorSpans", () => {
  it("maps server offsets onto the paragraph that holds them", () => {
    const p1 = "The ferry left at dawn.";
    const pre = "Nobody on the dock ";
    const quote = "waved it off";
    const text = `${p1}\n\n${pre}${quote}.`;
    const start = p1.length + 2 + pre.length;

    const layout = anchorSpans(text, [keyed("n1:0", located(start, start + quote.length, quote))]);

    expect(layout.mismatches).toEqual([]);
    expect(markedText(layout)).toEqual([{ paragraph: 2, text: quote }]);
    const para = layout.paragraphs[2];
    expect(para.start).toBe(p1.length + 2);
    expect(para.pieces.map((x) => x.text).join("")).toBe(`${pre}${quote}.`);
    expect(layout.firstPiece.get("n1:0")).toBe("2:1");
  });

  it("splits a segment that crosses a paragraph boundary into one span per paragraph", () => {
    const a = "First line ends here";
    const b = "second line starts here";
    const text = `Lead. ${a}\n${b} and goes on.`;
    const start = "Lead. ".length;
    const end = start + a.length + 1 + b.length;

    const layout = anchorSpans(text, [keyed("n1:0", located(start, end, `${a}\n${b}`))]);

    expect(markedText(layout)).toEqual([
      { paragraph: 0, text: a },
      { paragraph: 1, text: b },
    ]);
  });

  it("flags a segment whose text does not match the snapshot and draws nothing for it", () => {
    const text = "The bell rang twice.";
    const layout = anchorSpans(text, [keyed("n1:0", located(4, 8, "gong"))]);

    expect(layout.mismatches).toEqual([
      expect.objectContaining({ anchorId: "n1:0", expected: "gong", actual: "bell" }),
    ]);
    expect(markedText(layout)).toEqual([]);
  });

  it("flags out-of-range offsets as a mismatch rather than clamping them", () => {
    const layout = anchorSpans("Short.", [keyed("n1:0", located(3, 40, "rt."))]);
    expect(layout.mismatches[0]).toMatchObject({ actual: null, start: 3, end: 40 });
  });

  it("reports overlapping highlights from different anchors and keeps both", () => {
    const text = "She folded the map along its oldest crease.";
    const first = { s: 4, e: 17 }; // "folded the ma"
    const second = { s: 11, e: 24 }; // "the map along"
    const layout = anchorSpans(text, [
      keyed("n1:0", located(first.s, first.e, text.slice(first.s, first.e))),
      keyed("n2:0", located(second.s, second.e, text.slice(second.s, second.e))),
    ]);

    expect(layout.overlaps).toEqual([{ start: 11, end: 17, anchorIds: ["n1:0", "n2:0"] }]);
    const both = layout.paragraphs[0].pieces.find((x) => x.marks.length === 2);
    expect(both?.text).toBe("the ma");
    expect(layout.firstPiece.has("n1:0") && layout.firstPiece.has("n2:0")).toBe(true);
  });

  it("does not report two candidates of the same ambiguous anchor as an overlap", () => {
    const text = "haha ha";
    const anchor: ReadThroughAnchorOut = {
      chapter_id: "ch1",
      state: "ambiguous",
      text_quoted: "haha",
      segments: [],
      candidates: [[{ start: 0, end: 4, text: "haha" }], [{ start: 2, end: 6, text: "ha h" }]],
      candidate_count: 2,
    };
    expect(anchorSpans(text, [keyed("n1:0", anchor)]).overlaps).toEqual([]);
  });

  it("draws every candidate of an ambiguous anchor with the candidate kind", () => {
    const line = "The lamp went out.";
    const text = `${line}\n${line}`;
    const second = line.length + 1;
    const anchor: ReadThroughAnchorOut = {
      chapter_id: "ch1",
      state: "ambiguous",
      text_quoted: "The lamp went out.",
      segments: [],
      candidates: [
        [{ start: 0, end: line.length, text: line }],
        [{ start: second, end: second + line.length, text: line }],
      ],
      candidate_count: 2,
    };
    const layout = anchorSpans(text, [keyed("n1:0", anchor)]);
    const marks = layout.paragraphs.flatMap((p) => p.pieces.flatMap((x) => x.marks));
    expect(marks.map((m) => [m.kind, m.placement])).toEqual([
      ["candidate", 0],
      ["candidate", 1],
    ]);
  });

  it("slices correctly when astral characters precede the quote", () => {
    // "🕯️" and "🌊" are surrogate pairs: UTF-16 offsets run ahead of code-point offsets. A
    // code-point offset (what Python's str index would give) must be flagged, not "fixed".
    const pre = "🌊 The 🕯️ guttered; ";
    const quote = "she did not move";
    const text = `${pre}${quote}.`;
    const utf16 = pre.length;
    const codePoints = [...pre].length;
    expect(utf16).toBeGreaterThan(codePoints);

    const ok = anchorSpans(text, [keyed("n1:0", located(utf16, utf16 + quote.length, quote))]);
    expect(ok.mismatches).toEqual([]);
    expect(markedText(ok)).toEqual([{ paragraph: 0, text: quote }]);

    const wrongUnit = anchorSpans(text, [
      keyed("n1:0", located(codePoints, codePoints + quote.length, quote)),
    ]);
    expect(wrongUnit.mismatches).toHaveLength(1);
    expect(markedText(wrongUnit)).toEqual([]);
  });

  it("draws nothing for an unlocated anchor and reports no mismatch", () => {
    const anchor: ReadThroughAnchorOut = {
      chapter_id: "ch1",
      state: "unlocated",
      text_quoted: "words the chapter never had",
      segments: [],
      candidates: [],
      candidate_count: 0,
    };
    const layout = anchorSpans("Plain text.", [keyed("n1:0", anchor)]);
    expect(layout.mismatches).toEqual([]);
    expect(markedText(layout)).toEqual([]);
  });
});

describe("anchorsForChapter", () => {
  const base = {
    read_through_id: "rt",
    position: 0,
    category: "pacing",
    priority: "high",
    title: "t",
    observation: "o",
    recommendation: "r",
    anchor_role: "evidence",
    scope_chapter_ids: [],
    status: "open",
    created_at: "2026-09-15T10:00:00Z",
    updated_at: "2026-09-15T10:00:00Z",
  };

  // A book-note quote found in several chapters: one ambiguous anchor per chapter, each with that
  // chapter's candidates and the total count; plus a legacy row with no chapter and no candidates.
  const perChapter = (chapter_id: string | null): ReadThroughAnchorOut => ({
    chapter_id,
    state: "ambiguous",
    text_quoted: "x",
    segments: [],
    candidates: chapter_id ? [[{ start: 0, end: 1, text: "x" }]] : [],
    candidate_count: 3,
  });

  it("returns each per-chapter anchor of a repeated book-note quote only in its own chapter", () => {
    const notes = [
      {
        ...base,
        id: "b",
        chapter_id: null,
        anchors: [perChapter("ch1"), perChapter("ch2"), perChapter(null)],
      },
    ];
    expect(anchorsForChapter(notes, "ch1").map((k) => k.anchorId)).toEqual(["b:0"]);
    expect(anchorsForChapter(notes, "ch2").map((k) => k.anchorId)).toEqual(["b:1"]);
  });

  it("names the chapter an anchor can be drawn in, and none for a legacy no-chapter anchor", () => {
    const bookNote = { chapter_id: null };
    expect(anchorHighlightChapter(bookNote, perChapter("ch2"))).toBe("ch2");
    expect(anchorHighlightChapter(bookNote, perChapter(null))).toBeNull();
    expect(anchorHighlightChapter(bookNote, { ...perChapter("ch2"), candidates: [] })).toBeNull();
    // A chapter note's anchor may omit chapter_id; it lives in the note's chapter.
    expect(anchorHighlightChapter({ chapter_id: "ch1" }, located(0, 1, "x"))).toBe("ch1");
  });

  it("collects chapter-note anchors and book-note anchors that live in the chapter", () => {
    const inCh1 = located(0, 1, "x");
    const inCh2 = { ...located(0, 1, "x"), chapter_id: "ch2" };
    const notes = [
      { ...base, id: "a", chapter_id: "ch1", anchors: [{ ...inCh1, chapter_id: null }] },
      { ...base, id: "b", chapter_id: null, anchors: [inCh2, inCh1] },
    ];
    expect(anchorsForChapter(notes, "ch1").map((k) => k.anchorId)).toEqual(["a:0", "b:1"]);
  });
});
