import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ManuscriptUploader from "./ManuscriptUploader";
import type { ParsedManuscriptOut } from "../api/types";

vi.mock("../api/client", () => ({
  api: { parseManuscript: vi.fn(), importManuscript: vi.fn() },
}));
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
      title: "The Lobby",
      detected: true,
      conflict: false,
      warnings: [],
      scenes: [{ scene_no: 1, prose: "Alpha opens the scene.", word_count: 4 }],
    },
    {
      chapter_no: 2,
      title: null,
      detected: false,
      conflict: true,
      warnings: ['No "Chapter" header found — number assigned by position.'],
      scenes: [{ scene_no: 1, prose: "Beta.", word_count: 1 }],
    },
  ],
  warnings: ["1 chapter(s) had no header and were auto-numbered."],
  existing_chapter_nos: [2],
};

async function dropFile() {
  const { container } = render(<ManuscriptUploader bookId="book-1" />);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["ignored"], "draft.md", { type: "text/markdown" });
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve("Chapter 1\n\nAlpha opens the scene."),
  });
  fireEvent.change(input, { target: { files: [file] } });
  return container;
}

describe("ManuscriptUploader", () => {
  it("parses a dropped file and renders the read-only preview (conflict + inferred + warnings)", async () => {
    vi.mocked(api.parseManuscript).mockResolvedValue(PARSED);
    await dropFile();

    await waitFor(() =>
      expect(api.parseManuscript).toHaveBeenCalledWith("book-1", [
        { filename: "draft.md", text: "Chapter 1\n\nAlpha opens the scene." },
      ]),
    );
    expect(await screen.findByText(/Chapter 1 — The Lobby/)).toBeInTheDocument();
    expect(screen.getByText(/Detected 2 chapters · 2 scenes/)).toBeInTheDocument();
    expect(screen.getByText(/collides with existing ch 2/)).toBeInTheDocument();
    expect(screen.getByText(/no header — inferred/)).toBeInTheDocument();
    expect(screen.getByText(/Scene 1 · 4 words/)).toBeInTheDocument();
  });

  it("imports the parsed structure into review with the default POV applied", async () => {
    vi.mocked(api.parseManuscript).mockResolvedValue(PARSED);
    vi.mocked(api.importManuscript).mockResolvedValue({
      chapters_created: 2,
      chapters_updated: 0,
      scenes_imported: 2,
      skipped_conflicts: [],
      warnings: [],
    });
    await dropFile();
    await screen.findByText(/Chapter 1 — The Lobby/);

    fireEvent.change(screen.getByPlaceholderText("Character name"), {
      target: { value: "Marcus" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Import 2 scenes for review/ }));

    await waitFor(() =>
      expect(api.importManuscript).toHaveBeenCalledWith("book-1", {
        approve_directly: false,
        chapters: [
          {
            chapter_no: 1,
            title: "The Lobby",
            pov: "Marcus",
            overwrite: false,
            scenes: [{ scene_no: 1, prose: "Alpha opens the scene." }],
          },
          {
            chapter_no: 2,
            title: null,
            pov: "Marcus",
            overwrite: false,
            scenes: [{ scene_no: 1, prose: "Beta." }],
          },
        ],
      }),
    );
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith({
        tone: "success",
        message: "Imported 2 scenes into review",
      }),
    );
    expect(refreshAll).toHaveBeenCalled();
  });
});
