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
  ],
  warnings: [],
  existing_chapter_nos: [],
};

describe("ManuscriptUploader", () => {
  it("parses a dropped file and mounts the boundary editor", async () => {
    vi.mocked(api.parseManuscript).mockResolvedValue(PARSED);
    const { container } = render(<ManuscriptUploader bookId="book-1" />);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["ignored"], "draft.md", { type: "text/markdown" });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve("Chapter 1\n\nAlpha opens the scene."),
    });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() =>
      expect(api.parseManuscript).toHaveBeenCalledWith("book-1", [
        { filename: "draft.md", text: "Chapter 1\n\nAlpha opens the scene." },
      ]),
    );
    // the editor mounted: its Import button and the parsed chapter title input are present
    expect(
      await screen.findByRole("button", { name: /Import 1 scene for review/ }),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("The Lobby")).toBeInTheDocument();
  });
});
