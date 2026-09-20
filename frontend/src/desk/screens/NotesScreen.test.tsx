import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotesScreen from "./NotesScreen";

// Synthetic prose only. Anchor offsets are built by concatenation, never by searching the text.

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("../api/data", () => ({
  useDeskData: () => ({ bookId: "book-1" }),
}));

const apiMock = vi.hoisted(() => ({
  readThroughs: vi.fn(),
  readThroughStatus: vi.fn(),
  readThrough: vi.fn(),
  startReadThrough: vi.fn(),
  stopReadThrough: vi.fn(),
  patchReadThroughNote: vi.fn(),
  suggestProseForNote: vi.fn(),
  deleteReadThrough: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ApiError: actual.ApiError, api: apiMock };
});

import { ApiError } from "../api/client";

// --- fixtures -----------------------------------------------------------------------------------

const SALT_PRE = "Prologue. There was ";
const SALT = "salt on the rail";
const C1_TEXT = `${SALT_PRE}${SALT}.`;

const LINE = "the lantern swung";
const C2_MID = `${LINE} over the quay.\n\nLater, `;
const C2_TEXT = `${C2_MID}${LINE} again.`;

const chapter = (
  id: string,
  position: number,
  label: string,
  status: string,
  text: string,
  extra = {},
) => ({
  id,
  position,
  label,
  text,
  word_count: text.split(/\s+/).length,
  status,
  digest: null,
  notes_dropped: 0,
  notes_capped: false,
  model_used: status === "done" ? "gpt-5.6-luna" : null,
  attempts: 1,
  error: null,
  ...extra,
});

const baseNote = {
  read_through_id: "rt-done",
  position: 0,
  category: "setup_payoff",
  priority: "medium",
  observation: "The lantern is lit with ceremony and then forgotten.",
  recommendation: "Return to it once before the chapter closes.",
  anchor_role: "evidence",
  scope_chapter_ids: [] as string[],
  status: "open",
  created_at: "2026-09-15T10:00:00Z",
  updated_at: "2026-09-15T10:00:00Z",
};

const HIGH_NOTE = {
  ...baseNote,
  id: "n-high",
  chapter_id: "c2",
  priority: "high",
  title: "The lantern never pays off",
  anchors: [
    {
      chapter_id: "c2",
      state: "ambiguous",
      text_quoted: LINE,
      segments: [],
      candidates: [
        [{ start: 0, end: LINE.length, text: LINE }],
        [{ start: C2_MID.length, end: C2_MID.length + LINE.length, text: LINE }],
      ],
      candidate_count: 4,
    },
  ],
};

const BOOK_NOTE = {
  ...baseNote,
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
      text_quoted: SALT,
      segments: [{ start: SALT_PRE.length, end: SALT_PRE.length + SALT.length, text: SALT }],
      candidates: [],
      candidate_count: 0,
    },
  ],
};

const RT = {
  id: "rt-done",
  book_id: "book-1",
  title: "Four chapters",
  status: "partial",
  error: null,
  created_at: "2026-09-15T14:03:00Z",
  started_at: "2026-09-15T14:03:05Z",
  finished_at: "2026-09-15T14:20:00Z",
  deadline_at: "2026-09-15T17:03:00Z",
  settings_snapshot: {},
  voice_guide_used: true,
  attempt_allowance: 10,
  attempts_used: 6,
  tokens_charged: 42000,
  accounting_gap: false,
  stop_requested: false,
  book_pass_status: "done",
  book_pass_error: null,
  book_input_mode: "digests",
  book_chapter_ids: ["c1", "c2"],
  book_model_used: "gpt-5.6-terra",
  chapters: [
    chapter("c1", 0, "Prologue: Salt", "done", C1_TEXT),
    chapter("c2", 1, "The Harbour", "done", C2_TEXT),
    chapter("c3", 2, "Low Tide", "failed", "Low tide text.", { error: "rate limited" }),
    chapter("c4", 3, "The Crossing", "skipped", "Crossing text."),
  ],
  notes: [HIGH_NOTE, BOOK_NOTE],
};

const summary = (over: Record<string, unknown> = {}) => ({
  id: "rt-done",
  book_id: "book-1",
  title: "Four chapters",
  status: "partial",
  chapters_total: 4,
  chapters_done: 2,
  book_pass_status: "done",
  book_input_mode: "digests",
  error: null,
  created_at: "2026-09-15T14:03:00Z",
  started_at: null,
  finished_at: null,
  ...over,
});

const statusOf = (status: string, over: Record<string, unknown> = {}) => ({
  id: "rt-live",
  status,
  chapters_total: 3,
  chapters_done: 1,
  chapters_failed: 0,
  chapters_skipped: 0,
  current_label: "The Harbour",
  attempts_used: 2,
  attempt_allowance: 8,
  tokens_charged: 1500,
  book_pass_status: "pending",
  book_input_mode: null,
  stop_requested: false,
  error: null,
  ...over,
});

// --- helpers ------------------------------------------------------------------------------------

function addPasted(label: string, text: string) {
  fireEvent.change(screen.getByPlaceholderText("Chapter label (optional)"), {
    target: { value: label },
  });
  fireEvent.change(screen.getByPlaceholderText("Paste a chapter here"), {
    target: { value: text },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add pasted chapter" }));
}

const runButton = () => screen.getByRole("button", { name: /Run read-through|Starting…/ });

/** Advance fake time, then let the promise chains that timers kicked off settle. */
async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }
}

beforeEach(() => {
  for (const fn of Object.values(apiMock)) fn.mockReset();
  apiMock.readThroughs.mockResolvedValue([]);
  apiMock.readThroughStatus.mockResolvedValue(statusOf("queued"));
  apiMock.readThrough.mockResolvedValue(RT);
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
});

// --- composing and starting ---------------------------------------------------------------------

describe("NotesScreen · starting a read-through", () => {
  it("posts once on a double click and shows the local estimate", async () => {
    apiMock.startReadThrough.mockReturnValue(new Promise(() => {}));
    render(<NotesScreen />);
    await waitFor(() => expect(apiMock.readThroughs).toHaveBeenCalledWith("book-1"));

    addPasted("The Ferry", "The ferry left at dawn.");
    addPasted("The Dock", "Nobody waved it off.");
    expect(
      screen.getByText(
        "2 chapters · 9 words · baseline 3 model calls (+ up to 3 fallback attempts)",
      ),
    ).toBeInTheDocument();

    const button = runButton();
    fireEvent.click(button);
    fireEvent.click(button);
    expect(apiMock.startReadThrough).toHaveBeenCalledTimes(1);
    expect(apiMock.startReadThrough.mock.calls[0][1].chapters).toEqual([
      { label: "The Ferry", text: "The ferry left at dawn." },
      { label: "The Dock", text: "Nobody waved it off." },
    ]);
  });

  it("reuses the same client_request_id when Run is retried after a network failure", async () => {
    apiMock.startReadThrough
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(summary({ id: "rt-new", status: "queued" }));
    render(<NotesScreen />);
    await waitFor(() => expect(apiMock.readThroughs).toHaveBeenCalled());

    addPasted("The Ferry", "The ferry left at dawn.");
    fireEvent.click(runButton());
    expect(await screen.findByText(/Couldn't reach the server/)).toBeInTheDocument();

    fireEvent.click(runButton());
    await waitFor(() => expect(apiMock.startReadThrough).toHaveBeenCalledTimes(2));
    const [first, second] = apiMock.startReadThrough.mock.calls.map((c) => c[1].client_request_id);
    expect(typeof first).toBe("string");
    expect(first.length).toBeGreaterThanOrEqual(8);
    expect(second).toBe(first);
  });

  it("mints a fresh client_request_id when the composition changes", async () => {
    apiMock.startReadThrough.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<NotesScreen />);
    await waitFor(() => expect(apiMock.readThroughs).toHaveBeenCalled());

    addPasted("The Ferry", "The ferry left at dawn.");
    fireEvent.click(runButton());
    await screen.findByText(/Couldn't reach the server/);

    fireEvent.change(screen.getByLabelText("Label for chapter 1"), {
      target: { value: "The Early Ferry" },
    });
    fireEvent.click(runButton());
    await waitFor(() => expect(apiMock.startReadThrough).toHaveBeenCalledTimes(2));
    const [first, second] = apiMock.startReadThrough.mock.calls.map((c) => c[1].client_request_id);
    expect(second).not.toBe(first);
  });

  it("shows a 409 detail inline", async () => {
    apiMock.startReadThrough.mockRejectedValue(
      new ApiError(409, "Conflict", '{"detail":"too many read-throughs running"}', {
        detail: "too many read-throughs running",
      }),
    );
    render(<NotesScreen />);
    await waitFor(() => expect(apiMock.readThroughs).toHaveBeenCalled());

    addPasted("The Ferry", "The ferry left at dawn.");
    fireEvent.click(runButton());
    expect(await screen.findByText("too many read-throughs running")).toBeInTheDocument();
  });
});

// --- the active run -----------------------------------------------------------------------------

describe("NotesScreen · an active run", () => {
  it("re-attaches to an active run on mount", async () => {
    apiMock.readThroughs.mockResolvedValue([summary({ id: "rt-live", status: "running" })]);
    apiMock.readThroughStatus.mockResolvedValue(statusOf("running"));
    render(<NotesScreen />);

    expect(await screen.findByText("Chapter 2 of 3 · The Harbour")).toBeInTheDocument();
    expect(apiMock.readThroughStatus).toHaveBeenCalledWith("rt-live");
    expect(
      screen.getByText(
        "Runs on the server — you can leave this screen. It may wait for a model slot.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Attempts 2 of 8 · 1,500 tokens charged")).toBeInTheDocument();
    expect(screen.getByText(/A read-through is already running for this book/)).toBeInTheDocument();
    expect(apiMock.readThrough).not.toHaveBeenCalled();
  });

  it("shows the Stop disclosure and calls stopReadThrough", async () => {
    apiMock.readThroughs.mockResolvedValue([summary({ id: "rt-live", status: "running" })]);
    apiMock.readThroughStatus.mockResolvedValue(statusOf("running"));
    apiMock.stopReadThrough.mockResolvedValue(statusOf("stopping", { stop_requested: true }));
    render(<NotesScreen />);

    expect(
      await screen.findByText(
        "Stopping keeps finished chapters. A request already sent to the model may still be billed.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(apiMock.stopReadThrough).toHaveBeenCalledWith("rt-live"));
    expect(await screen.findByText("Stopping…")).toBeInTheDocument();
  });

  it("disables Delete while the run is active", async () => {
    apiMock.readThroughs.mockResolvedValue([summary({ id: "rt-live", status: "running" })]);
    apiMock.readThroughStatus.mockResolvedValue(statusOf("running"));
    render(<NotesScreen />);

    await screen.findByText("Chapter 2 of 3 · The Harbour");
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("stops polling on a terminal status and loads the result", async () => {
    vi.useFakeTimers();
    apiMock.readThroughs
      .mockResolvedValueOnce([summary({ id: "rt-done", status: "running" })])
      .mockResolvedValue([summary({ id: "rt-done", status: "partial" })]);
    apiMock.readThroughStatus
      .mockResolvedValueOnce(statusOf("running", { id: "rt-done" }))
      .mockResolvedValueOnce(statusOf("partial", { id: "rt-done" }));
    render(<NotesScreen />);

    await settle();
    expect(apiMock.readThroughStatus).toHaveBeenCalledTimes(1);
    expect(apiMock.readThrough).not.toHaveBeenCalled();

    await settle(1500);
    expect(apiMock.readThroughStatus).toHaveBeenCalledTimes(2);
    expect(apiMock.readThrough).toHaveBeenCalledWith("rt-done");

    await settle(15000);
    expect(apiMock.readThroughStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Partial — some chapters were not read")).toBeInTheDocument();
  });

  it("stops polling when the run is gone (404)", async () => {
    vi.useFakeTimers();
    apiMock.readThroughs.mockResolvedValue([summary({ id: "rt-live", status: "running" })]);
    apiMock.readThroughStatus
      .mockResolvedValueOnce(statusOf("running"))
      .mockRejectedValueOnce(new ApiError(404, "Not Found", "", { detail: "not found" }));
    render(<NotesScreen />);

    await settle();
    await settle(1500);
    expect(apiMock.readThroughStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/no longer exists/)).toBeInTheDocument();

    await settle(15000);
    expect(apiMock.readThroughStatus).toHaveBeenCalledTimes(2);
  });
});

// --- a loaded result ----------------------------------------------------------------------------

describe("NotesScreen · results", () => {
  beforeEach(() => {
    apiMock.readThroughs.mockResolvedValue([summary()]);
  });

  it("labels digest-mode cross-chapter notes and lists the chapters that pass did not read", async () => {
    render(<NotesScreen />);
    expect(
      await screen.findByText("Cross-chapter notes from summaries — not the manuscript"),
    ).toBeInTheDocument();
    expect(screen.getByText("Low Tide; The Crossing")).toBeInTheDocument();
  });

  it("names the failed chapter and its error in a partial banner", async () => {
    render(<NotesScreen />);
    expect(await screen.findByText("Partial — some chapters were not read")).toBeInTheDocument();
    expect(screen.getByText("Low Tide — rate limited")).toBeInTheDocument();
    expect(screen.getByText("The Crossing")).toBeInTheDocument();
  });

  it("shows an ambiguous anchor's true count without claiming more highlights than were saved", async () => {
    const { container } = render(<NotesScreen />);
    // The fixture's anchor has 4 placements in all but only 2 saved candidates.
    const chip = await screen.findByText("appears 4 times · 2 highlighted");
    expect(chip.closest("[title]")?.getAttribute("title")).toBe(
      "Only 2 of 4 placements were saved, so only those are highlighted. None was chosen.",
    );
    const candidates = container.querySelectorAll('mark[data-kind="candidate"]');
    expect(candidates).toHaveLength(2);
    expect([...candidates].map((m) => m.textContent)).toEqual([LINE, LINE]);
  });

  it("scrolls the text pane to a located anchor, switching chapter if needed", async () => {
    const { container } = render(<NotesScreen />);
    const quote = await screen.findByRole("button", { name: `“${SALT}”` });
    expect(container.querySelector('mark[data-anchor-ids~="n-book:0"]')).toBeNull();

    fireEvent.click(quote);
    const mark = await waitFor(() => {
      const m = container.querySelector('mark[data-anchor-ids~="n-book:0"]');
      expect(m).not.toBeNull();
      return m as HTMLElement;
    });
    expect(mark.textContent).toBe(SALT);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("shows a book-note quote repeated across chapters as one chip per chapter, each highlighting in its own", async () => {
    const TIDE = "the tide turned";
    const C1X_PRE = "Morning. ";
    const C2X_MID = `${TIDE} at noon. Later `;
    const inChapter = (chapter_id: string, candidates: object[][]) => ({
      chapter_id,
      state: "ambiguous",
      text_quoted: TIDE,
      segments: [],
      candidates,
      candidate_count: 3, // the total across both chapters
    });
    const tideNote = {
      ...BOOK_NOTE,
      id: "n-tide",
      title: "The tide carries every turn",
      anchors: [
        inChapter("c1", [
          [{ start: C1X_PRE.length, end: C1X_PRE.length + TIDE.length, text: TIDE }],
        ]),
        inChapter("c2", [
          [{ start: 0, end: TIDE.length, text: TIDE }],
          [{ start: C2X_MID.length, end: C2X_MID.length + TIDE.length, text: TIDE }],
        ]),
      ],
    };
    apiMock.readThrough.mockResolvedValue({
      ...RT,
      chapters: [
        chapter("c1", 0, "Prologue: Salt", "done", `${C1X_PRE}${TIDE} early.`),
        chapter("c2", 1, "The Harbour", "done", `${C2X_MID}${TIDE} again.`),
      ],
      notes: [tideNote],
    });
    const { container } = render(<NotesScreen />);

    expect(await screen.findByText("appears 3 times · Prologue: Salt")).toBeInTheDocument();
    const harbourChip = screen.getByText("appears 3 times · The Harbour");
    // The chip counts what THIS chapter draws (2) against the total across chapters (3) — never "every".
    expect(harbourChip).toHaveAttribute(
      "title",
      "Highlights 2 placements in The Harbour; the quote appears 3 times in all. None was chosen.",
    );
    // No chapter notes, so the first chapter is open, and only its own placement is drawn.
    expect(container.querySelectorAll('mark[data-anchor-ids~="n-tide:0"]')).toHaveLength(1);
    expect(container.querySelector('mark[data-anchor-ids~="n-tide:1"]')).toBeNull();

    const [, second] = screen.getAllByRole("button", { name: `“${TIDE}”` });
    fireEvent.click(second);

    await waitFor(() =>
      expect(container.querySelectorAll('mark[data-active="true"]')).toHaveLength(2),
    );
    for (const m of container.querySelectorAll('mark[data-active="true"]')) {
      expect(m.getAttribute("data-anchor-ids")?.split(" ")).toContain("n-tide:1");
      expect(m.textContent).toBe(TIDE);
    }
    expect(container.querySelector('mark[data-anchor-ids~="n-tide:0"]')).toBeNull();
    expect(second).toHaveAttribute("aria-pressed", "true");
  });

  it("does not claim a highlight for a legacy ambiguous anchor with no chapter", async () => {
    const legacy = {
      ...BOOK_NOTE,
      id: "n-old",
      title: "An older cross-chapter note",
      anchors: [
        {
          chapter_id: null,
          state: "ambiguous",
          text_quoted: "the gulls again",
          segments: [],
          candidates: [],
          candidate_count: 2,
        },
      ],
    };
    apiMock.readThrough.mockResolvedValue({ ...RT, notes: [HIGH_NOTE, legacy] });
    const { container } = render(<NotesScreen />);

    const chip = await screen.findByText("appears 2 times · not highlighted");
    expect(chip.getAttribute("title")).not.toMatch(/Every placement/);
    expect(screen.queryByRole("button", { name: "“the gulls again”" })).toBeNull();
    expect(container.querySelector('mark[data-anchor-ids~="n-old:0"]')).toBeNull();
  });

  it("marks a note Done from the PATCH response without refetching the run", async () => {
    apiMock.patchReadThroughNote.mockResolvedValue({ ...HIGH_NOTE, status: "done" });
    render(<NotesScreen />);
    const card = (await screen.findByText("The lantern never pays off")).closest("article");
    expect(card).not.toBeNull();

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Done" }));
    expect(
      await within(card as HTMLElement).findByRole("button", { name: "Reopen" }),
    ).toBeInTheDocument();
    expect(apiMock.patchReadThroughNote).toHaveBeenCalledWith("n-high", { status: "done" });
    expect(apiMock.readThrough).toHaveBeenCalledTimes(1);
    expect(apiMock.readThroughs).toHaveBeenCalledTimes(1);
  });

  it("suggests prose for a note and says nothing is saved", async () => {
    apiMock.suggestProseForNote.mockResolvedValue({
      suggestions: [
        {
          mode: "insert_before",
          anchor_quote: LINE,
          prose: "The lantern had a job, and it was not light.",
          why: "Gives the object a purpose before it recurs.",
        },
      ],
      standards_loaded: ["voice_guide", "prose_contract"],
      standards_missing: [],
      canon_sources: ["canon/objects.md#lantern"],
      drift_scope_characters: [],
      fabricated_dropped: 0,
      telemetry_recorded: true,
      model: "claude-opus-latest",
      tokens_used: 1200,
    });
    render(<NotesScreen />);
    const card = (await screen.findByText("The lantern never pays off")).closest("article");

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Suggest prose" }));
    expect(apiMock.suggestProseForNote).toHaveBeenCalledWith("n-high");
    expect(
      await within(card as HTMLElement).findByText("The lantern had a job, and it was not light."),
    ).toBeInTheDocument();
    // The author must be told the prose is theirs to copy and will not survive a reload.
    expect(within(card as HTMLElement).getByText(/nothing saved/)).toBeInTheDocument();
  });

  it("reports a suggestion failure on the note instead of silently doing nothing", async () => {
    apiMock.suggestProseForNote.mockRejectedValue(
      new ApiError(503, "Service Unavailable", "", {
        detail: "None of the author's standards could be loaded",
      }),
    );
    render(<NotesScreen />);
    const card = (await screen.findByText("The lantern never pays off")).closest("article");

    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Suggest prose" }));
    expect(
      await within(card as HTMLElement).findByText(/standards could be loaded/),
    ).toBeInTheDocument();
  });

  it("asks before deleting a finished run", async () => {
    apiMock.deleteReadThrough.mockResolvedValue({ deleted: "rt-done" });
    render(<NotesScreen />);
    await screen.findByText("Partial — some chapters were not read");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(apiMock.deleteReadThrough).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(apiMock.deleteReadThrough).toHaveBeenCalledWith("rt-done"));
  });

  it("copies the run as Markdown", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<NotesScreen />);
    await screen.findByText("Partial — some chapters were not read");

    fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(writeText.mock.calls[0][0]).toContain(
      "Cross-chapter notes from summaries (digests), not the manuscript",
    );
  });
});
