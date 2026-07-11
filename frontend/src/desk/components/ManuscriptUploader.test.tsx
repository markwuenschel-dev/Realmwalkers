import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ManuscriptUploader from "./ManuscriptUploader";
import type { ParsedManuscriptOut } from "../api/types";

vi.mock("../api/client", () => ({ api: { parseManuscript: vi.fn() } }));
import { api } from "../api/client";

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

describe("ManuscriptUploader", () => {
  it("parses a dropped file and renders the read-only preview (conflict + inferred + warnings)", async () => {
    vi.mocked(api.parseManuscript).mockResolvedValue(PARSED);
    const { container } = render(<ManuscriptUploader bookId="book-1" />);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["ignored"], "draft.md", { type: "text/markdown" });
    // Pin text() so the test doesn't depend on jsdom's Blob.text() implementation.
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve("Chapter 1\n\nAlpha opens the scene."),
    });
    fireEvent.change(input, { target: { files: [file] } });

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
});
