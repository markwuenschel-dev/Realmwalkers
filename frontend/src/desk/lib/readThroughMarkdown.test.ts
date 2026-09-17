import { describe, expect, it } from "vitest";
import type { ReadThroughNoteOut, ReadThroughOut } from "../api/types";
import {
  bookPassLabel,
  chaptersNotReadByBookPass,
  readThroughMarkdown,
} from "./readThroughMarkdown";

// Synthetic prose only.
const chapter = (id: string, position: number, label: string, status: string, extra = {}) => ({
  id,
  position,
  label,
  text: `${label} text.`,
  word_count: 2,
  status,
  digest: null,
  notes_dropped: 0,
  notes_capped: false,
  model_used: status === "done" ? "gpt-5.6-luna" : null,
  attempts: 1,
  error: null,
  ...extra,
});

const note = (over: Partial<ReadThroughNoteOut>): ReadThroughNoteOut => ({
  id: "note",
  read_through_id: "rt1",
  chapter_id: "c2",
  position: 0,
  category: "setup_payoff",
  priority: "medium",
  title: "The lantern never pays off",
  observation: "The lantern is lit with ceremony and then forgotten.",
  recommendation: "Return to it once before the chapter closes.",
  anchor_role: "evidence",
  anchors: [],
  scope_chapter_ids: [],
  status: "open",
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
  ...over,
});

const RT: ReadThroughOut = {
  id: "rt1",
  book_id: "b1",
  title: "Read-through of four chapters",
  status: "partial",
  error: null,
  created_at: "2026-09-15T14:03:00Z",
  started_at: "2026-09-15T14:03:05Z",
  finished_at: "2026-09-15T14:20:00Z",
  deadline_at: "2026-09-15T17:03:00Z",
  settings_snapshot: {},
  voice_guide_used: false,
  attempt_allowance: 10,
  attempts_used: 6,
  tokens_charged: 123456,
  accounting_gap: true,
  stop_requested: false,
  book_pass_status: "done",
  book_pass_error: null,
  book_input_mode: "digests",
  book_chapter_ids: ["c1", "c2"],
  book_model_used: "gpt-5.6-terra",
  chapters: [
    chapter("c1", 0, "Prologue: Salt", "done"),
    chapter("c2", 1, "The Harbour", "done", { notes_dropped: 2, notes_capped: true }),
    chapter("c3", 2, "Low Tide", "failed", { error: "rate limited" }),
    chapter("c4", 3, "The Crossing", "skipped"),
  ],
  notes: [
    note({
      id: "n-low",
      priority: "low",
      position: 1,
      title: "Minor echo",
      anchors: [
        {
          chapter_id: "c2",
          state: "unlocated",
          text_quoted: "a line the chapter never had",
          segments: [],
          candidates: [],
          candidate_count: 0,
        },
      ],
    }),
    note({
      id: "n-high",
      priority: "high",
      position: 0,
      status: "done",
      anchors: [
        {
          chapter_id: "c2",
          state: "ambiguous",
          text_quoted: "the lantern swung",
          segments: [],
          candidates: [
            [{ start: 0, end: 17, text: "the lantern swung" }],
            [{ start: 40, end: 57, text: "the lantern swung" }],
          ],
          candidate_count: 4,
        },
      ],
    }),
    note({
      id: "n-book",
      chapter_id: null,
      category: "structure",
      priority: "high",
      title: "The harbour arrives too late",
      anchor_role: "location",
      scope_chapter_ids: ["c1", "c2"],
      anchors: [
        {
          chapter_id: "c1",
          state: "located",
          text_quoted: "salt on the rail",
          segments: [{ start: 3, end: 19, text: "salt on the rail" }],
          candidates: [],
          candidate_count: 0,
        },
        // One quote repeated across two chapters: one anchor per chapter, total count on each.
        {
          chapter_id: "c1",
          state: "ambiguous",
          text_quoted: "the tide turned",
          segments: [],
          candidates: [[{ start: 0, end: 15, text: "the tide turned" }]],
          candidate_count: 3,
        },
        {
          chapter_id: "c2",
          state: "ambiguous",
          text_quoted: "the tide turned",
          segments: [],
          candidates: [
            [{ start: 0, end: 15, text: "the tide turned" }],
            [{ start: 30, end: 45, text: "the tide turned" }],
          ],
          candidate_count: 3,
        },
      ],
    }),
  ],
};

describe("readThroughMarkdown", () => {
  const md = readThroughMarkdown(RT);

  it("leads with title, status, snapshot date, models and voice guide", () => {
    expect(md.startsWith("# Read-through of four chapters\n")).toBe(true);
    expect(md).toContain("**Status:** Partial — some chapters were not read");
    expect(md).toContain("**Snapshot:** 2026-09-15 14:03 UTC");
    expect(md).toContain("**Models:** gpt-5.6-luna, gpt-5.6-terra");
    expect(md).toContain("**Voice guide:** not loaded");
  });

  it("exports failed and skipped chapters with their labels and errors", () => {
    expect(md).toContain("- Chapters done (2): Prologue: Salt; The Harbour");
    expect(md).toContain("- Chapters failed (1): Low Tide — rate limited");
    expect(md).toContain("- Chapters skipped (1): The Crossing");
  });

  it("says the cross-chapter notes came from summaries and names the chapters it did not read", () => {
    expect(md).toContain(
      "- Book pass: Cross-chapter notes from summaries (digests), not the manuscript",
    );
    expect(md).toContain("- Chapters the book pass did not read: Low Tide; The Crossing");
  });

  it("carries the accounting gap and per-chapter dropped/capped counts", () => {
    expect(md).toContain("Some model spend for this run was not recorded.");
    expect(md).toContain(
      "- The Harbour: 2 notes dropped (no quote could be located); more notes existed than were kept",
    );
  });

  it("puts book notes before chapter notes, and orders chapter notes by priority", () => {
    const book = md.indexOf("## Book notes");
    const harbour = md.indexOf("## The Harbour");
    expect(book).toBeGreaterThan(-1);
    expect(md.indexOf("The harbour arrives too late")).toBeGreaterThan(book);
    expect(md.indexOf("The harbour arrives too late")).toBeLessThan(harbour);
    expect(md.indexOf("### High · Setup payoff")).toBeLessThan(
      md.indexOf("### Low · Setup payoff"),
    );
  });

  it("exports anchors: located quote with its chapter, ambiguous count, unlocated", () => {
    expect(md).toContain("**Where this applies:**");
    expect(md).toContain('- "salt on the rail" (Prologue: Salt)');
    expect(md).toContain('- "the lantern swung" — appears 4 times');
    expect(md).toContain('- "a line the chapter never had" — could not be located');
  });

  it("names the chapter of each book-note anchor of a quote repeated across chapters", () => {
    expect(md).toContain('- "the tide turned" — appears 3 times (Prologue: Salt)');
    expect(md).toContain('- "the tide turned" — appears 3 times (The Harbour)');
  });

  it("includes each note's recommendation and status", () => {
    expect(md).toContain("**Recommendation:** Return to it once before the chapter closes.");
    expect(md).toContain("The lantern never pays off _(done)_");
    expect(md).toContain("Minor echo _(open)_");
  });

  it("marks unread chapters in their own section", () => {
    expect(md).toContain("## Low Tide\n\n_Not read (failed)._");
  });
});

describe("book pass coverage", () => {
  it("names the full-text mode plainly and lists nothing unread when every chapter was read", () => {
    const full = {
      ...RT,
      book_input_mode: "full_text",
      book_chapter_ids: ["c1", "c2", "c3", "c4"],
    };
    expect(bookPassLabel(full, "banner")).toBe("Cross-chapter notes from full text");
    expect(chaptersNotReadByBookPass(full)).toEqual([]);
  });

  it("does not claim unread chapters for a pass that never ran", () => {
    const skipped = { ...RT, book_pass_status: "skipped", book_chapter_ids: [] };
    expect(chaptersNotReadByBookPass(skipped)).toEqual([]);
    expect(bookPassLabel(skipped, "banner")).toBe("Cross-chapter pass skipped");
  });
});
