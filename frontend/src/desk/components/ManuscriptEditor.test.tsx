import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ManuscriptEditor from "./ManuscriptEditor";
import type { ParsedManuscriptOut } from "../api/types";

vi.mock("../api/client", () => ({ api: { importManuscript: vi.fn() } }));
import { api } from "../api/client";

const { pushToast, refreshAll } = vi.hoisted(() => ({
  pushToast: vi.fn(),
  refreshAll: vi.fn(() => Promise.resolve()),
}));
vi.mock("../api/data", () => ({ useDeskData: () => ({ pushToast, refreshAll }) }));

const PARSED: ParsedManuscriptOut = {
  chapters: [
    {
      chapter_no: 1,
      title: "One",
      detected: true,
      conflict: false,
      warnings: [],
      scenes: [
        { scene_no: 1, prose: "Alpha para.", word_count: 2 },
        { scene_no: 2, prose: "Beta para.", word_count: 2 },
      ],
    },
  ],
  warnings: [],
  existing_chapter_nos: [],
};

const REPORT = {
  chapters_created: 1,
  chapters_updated: 0,
  scenes_imported: 2,
  skipped_conflicts: [],
  warnings: [],
};

function lastPayload() {
  return vi.mocked(api.importManuscript).mock.calls.at(-1)![1];
}

describe("ManuscriptEditor", () => {
  it("imports the parsed structure unedited (two scenes in one chapter)", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue(REPORT);
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes for review/ }));
    await waitFor(() =>
      expect(api.importManuscript).toHaveBeenCalledWith("book-1", {
        approve_directly: false,
        auto_title: false,
        chapters: [
          {
            chapter_no: 1,
            title: "One",
            pov: "",
            kind: "chapter",
            overwrite: false,
            scenes: [
              { scene_no: 1, prose: "Alpha para." },
              { scene_no: 2, prose: "Beta para." },
            ],
          },
        ],
      }),
    );
  });

  it("promotes a scene break to a chapter, yielding two chapters on import", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue({ ...REPORT, chapters_created: 2 });
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "→ chapter" }));
    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes for review/ }));
    await waitFor(() => expect(api.importManuscript).toHaveBeenCalled());

    const payload = lastPayload();
    expect(payload.chapters).toHaveLength(2);
    expect(payload.chapters[0].scenes).toEqual([{ scene_no: 1, prose: "Alpha para." }]);
    expect(payload.chapters[1].chapter_no).toBe(2);
    expect(payload.chapters[1].scenes).toEqual([{ scene_no: 1, prose: "Beta para." }]);
  });

  it("merges a scene into the previous one when its break is removed", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue(REPORT);
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "remove" }));
    fireEvent.click(screen.getByRole("button", { name: /Import 1 scene for review/ }));
    await waitFor(() => expect(api.importManuscript).toHaveBeenCalled());

    const payload = lastPayload();
    expect(payload.chapters).toHaveLength(1);
    expect(payload.chapters[0].scenes).toEqual([
      { scene_no: 1, prose: "Alpha para.\n\nBeta para." },
    ]);
  });

  it("applies the default POV to all chapters", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue(REPORT);
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("default POV"), { target: { value: "Marcus" } });
    fireEvent.click(screen.getByRole("button", { name: "apply POV to all" }));
    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes for review/ }));
    await waitFor(() => expect(api.importManuscript).toHaveBeenCalled());

    expect(lastPayload().chapters[0].pov).toBe("Marcus");
  });

  it("passes the accept-directly and auto-title toggles into the import payload", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue(REPORT);
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.click(screen.getByLabelText("auto-generate titles"));
    fireEvent.click(screen.getByLabelText("accept directly (skip review)"));
    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes \(accept directly\)/ }));
    await waitFor(() => expect(api.importManuscript).toHaveBeenCalled());

    expect(lastPayload().approve_directly).toBe(true);
    expect(lastPayload().auto_title).toBe(true);
  });

  it("keeps chapter number 0 and the chosen kind (a prologue sorting before chapter 1)", async () => {
    vi.mocked(api.importManuscript).mockResolvedValue(REPORT);
    render(<ManuscriptEditor parsed={PARSED} bookId="book-1" onImported={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("chapter number"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("chapter kind"), { target: { value: "prologue" } });
    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes for review/ }));
    await waitFor(() => expect(api.importManuscript).toHaveBeenCalled());

    expect(lastPayload().chapters[0].chapter_no).toBe(0);
    expect(lastPayload().chapters[0].kind).toBe("prologue");
  });
});
