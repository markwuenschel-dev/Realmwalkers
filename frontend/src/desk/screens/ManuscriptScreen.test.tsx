import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeskDataProvider } from "../api/data";
import { EMPTY_JOBS } from "../api/constants";
import ManuscriptScreen from "./ManuscriptScreen";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("../components/ProseBlocks", () => ({
  default: ({ text }: { text: string }) => <div>{text}</div>,
}));

vi.mock("../api/client", () => ({
  api: {
    books: vi.fn(),
    chapters: vi.fn(),
    chapterScenes: vi.fn(),
    pending: vi.fn(),
    manuscript: vi.fn(),
    characters: vi.fn(),
    canon: vi.fn(),
    threads: vi.fn(),
    ruleProposals: vi.fn(),
    jobsStatus: vi.fn(),
    jobsFailed: vi.fn(),
  },
}));

import { api } from "../api/client";

const MANUSCRIPT = {
  book_id: "b1",
  title: "Realmwalkers",
  chapters: [
    {
      chapter_no: 1,
      title: "Signal Fire",
      pov: "Mara",
      kind: "chapter",
      epigraph: null,
      scenes: [{ scene_no: 1, prose: "Approved prose lives here." }],
    },
  ],
};

// The provider persists across tab switches (it lives in the app layout); only the SCREEN unmounts
// when the user navigates away. This stage mirrors that: toggling `show` remounts ManuscriptScreen
// while DeskDataProvider — and its cached manuscript — survives.
function Stage({ show }: { show: boolean }) {
  return <DeskDataProvider>{show ? <ManuscriptScreen /> : <div>elsewhere</div>}</DeskDataProvider>;
}

describe("ManuscriptScreen manuscript caching", () => {
  beforeEach(() => {
    vi.mocked(api.books)
      .mockReset()
      .mockResolvedValue([
        { id: "b1", title: "Realmwalkers", premise: null, created_at: "2026-01-01T00:00:00Z" },
      ]);
    vi.mocked(api.chapters).mockReset().mockResolvedValue([]);
    vi.mocked(api.chapterScenes).mockReset().mockResolvedValue([]);
    vi.mocked(api.pending).mockReset().mockResolvedValue([]);
    vi.mocked(api.manuscript)
      .mockReset()
      .mockResolvedValue(MANUSCRIPT as never);
    vi.mocked(api.characters).mockReset().mockResolvedValue([]);
    vi.mocked(api.canon).mockReset().mockResolvedValue([]);
    vi.mocked(api.threads).mockReset().mockResolvedValue([]);
    vi.mocked(api.ruleProposals).mockReset().mockResolvedValue([]);
    vi.mocked(api.jobsStatus).mockReset().mockResolvedValue(EMPTY_JOBS);
    vi.mocked(api.jobsFailed).mockReset().mockResolvedValue([]);
  });

  it("renders the cached manuscript on remount without refetching the compile", async () => {
    const view = render(<Stage show={false} />);

    // Let the provider's initial full load settle before the tab opens. The background canon body
    // upgrade (2nd api.canon call) is issued after the manuscript landed and was marked fresh, so
    // it doubles as the "bootstrap finished" probe.
    await waitFor(() => expect(api.manuscript).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.canon).toHaveBeenCalledTimes(2));

    // First Manuscript visit: the compile is warm from the initial load — zero extra fetches.
    view.rerender(<Stage show />);
    expect(await screen.findByText("Approved prose lives here.")).toBeInTheDocument();
    expect(api.manuscript).toHaveBeenCalledTimes(1);

    // Tab away and back: the screen unmounts and remounts, the provider (and its cache) survives.
    view.rerender(<Stage show={false} />);
    expect(screen.getByText("elsewhere")).toBeInTheDocument();
    view.rerender(<Stage show />);

    // The cached compile paints immediately — the warm cache still means ZERO new manuscript fetches.
    expect(await screen.findByText("Approved prose lives here.")).toBeInTheDocument();
    expect(api.manuscript).toHaveBeenCalledTimes(1);
  });
});
