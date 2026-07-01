import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PacketsScreen from "./PacketsScreen";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("../state", () => ({
  useDesk: () => ({
    t: { accent: "#000", onAccent: "#fff", bad: "#f00", warn: "#e90", good: "#0a0", info: "#09f" },
  }),
}));

const CHAPTERS = [
  {
    id: "c1",
    chapter_no: 1,
    title: "The Start",
    pov: "Soren",
    outline: "Soren arrives.",
    status: "planned",
  },
  {
    id: "c2",
    chapter_no: 2,
    title: "The Middle",
    pov: "Mara",
    outline: "Mara investigates.",
    status: "planned",
  },
  {
    id: "c3",
    chapter_no: 3,
    title: "No outline yet",
    pov: "Soren",
    outline: "",
    status: "planned",
  },
];

const mockData: {
  chapters: typeof CHAPTERS;
  failedJobs: unknown[];
  jobs: { running: boolean; queued: number; failed: number; active_scene: null };
  clearFailed: () => void;
  manuscript: {
    chapters: { chapter_no: number; title: string | null; pov: string; scenes: { scene_no: number; prose: string | null }[] }[];
  } | null;
  books: { id: string; title: string }[];
  bookId: string | null;
  latestScenes: unknown[];
} = {
  chapters: CHAPTERS,
  failedJobs: [],
  jobs: { running: false, queued: 0, failed: 0, active_scene: null },
  clearFailed: vi.fn(),
  // Same shape useDeskData() really returns — exercised by the "PacketsScreen exports" suite below.
  manuscript: null,
  books: [],
  bookId: null,
  latestScenes: [],
};

vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

vi.mock("../api/client", () => ({
  api: {
    packet: vi.fn(),
    packetStatus: vi.fn(),
    proposePacket: vi.fn(),
    updatePacket: vi.fn(),
    approvePacket: vi.fn(),
    deletePacket: vi.fn(),
  },
}));

import { api } from "../api/client";

describe("PacketsScreen batch generate", () => {
  beforeEach(() => {
    vi.mocked(api.packet).mockReset().mockRejectedValue(new Error("404"));
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(api.proposePacket).mockReset();
  });

  it("hides the batch panel by default and reveals it on toggle", async () => {
    render(<PacketsScreen />);
    expect(screen.queryByText(/Pick several chapters/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Batch · generate packets/));
    await waitFor(() => expect(screen.getByText(/Pick several chapters/)).toBeInTheDocument());
  });

  it("only lists chapters that already have an outline", async () => {
    render(<PacketsScreen />);
    fireEvent.click(screen.getByText(/Batch · generate packets/));

    const panel = within(await screen.findByTestId("batch-panel"));
    expect(panel.getByText(/The Start/)).toBeInTheDocument();
    expect(panel.getByText(/The Middle/)).toBeInTheDocument();
    expect(panel.queryByText(/No outline yet/)).not.toBeInTheDocument();
  });

  it("fires proposePacket for every selected chapter and reports the results", async () => {
    vi.mocked(api.proposePacket).mockResolvedValue({
      running: true,
      phase: "authoring",
      elapsed_s: 0,
    });
    render(<PacketsScreen />);
    fireEvent.click(screen.getByText(/Batch · generate packets/));
    const panel = within(await screen.findByTestId("batch-panel"));

    fireEvent.click(panel.getByRole("checkbox", { name: /The Start/ }));
    fireEvent.click(panel.getByRole("checkbox", { name: /The Middle/ }));
    fireEvent.click(panel.getByText(/Generate 2 packets/));

    await waitFor(() => expect(api.proposePacket).toHaveBeenCalledTimes(2));
    expect(api.proposePacket).toHaveBeenCalledWith("c1");
    expect(api.proposePacket).toHaveBeenCalledWith("c2");
    await waitFor(() => expect(panel.getAllByText("authoring started")).toHaveLength(2));
  });

  it("surfaces a per-chapter error without blocking the rest of the batch", async () => {
    vi.mocked(api.proposePacket).mockImplementation(async (id: string) => {
      if (id === "c1") throw new Error("no outline");
      return { running: true, phase: "authoring", elapsed_s: 0 };
    });
    render(<PacketsScreen />);
    fireEvent.click(screen.getByText(/Batch · generate packets/));
    const panel = within(await screen.findByTestId("batch-panel"));

    fireEvent.click(panel.getByRole("checkbox", { name: /The Start/ }));
    fireEvent.click(panel.getByRole("checkbox", { name: /The Middle/ }));
    fireEvent.click(panel.getByText(/Generate 2 packets/));

    await waitFor(() => expect(panel.getByText(/failed: no outline/)).toBeInTheDocument());
    expect(panel.getByText("authoring started")).toBeInTheDocument();
  });
});

// Same three exports the Manuscript tab offers, scoped to the selected chapter's approved scenes
// (data.manuscript is the approved compile — a packet has no prose of its own to export).
describe("PacketsScreen exports", () => {
  beforeEach(() => {
    vi.mocked(api.packet).mockReset().mockRejectedValue(new Error("404"));
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    mockData.manuscript = null;
    mockData.books = [];
    mockData.bookId = null;
  });

  it("disables export buttons when the selected chapter has no approved prose yet", async () => {
    render(<PacketsScreen />);
    const md = await screen.findByRole("button", { name: "Export Markdown" });
    expect(md).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export Reader DOCX" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export Shunn DOCX" })).toBeDisabled();
  });

  it("enables export buttons once the selected chapter has approved prose", async () => {
    mockData.manuscript = {
      chapters: [
        { chapter_no: 1, title: "The Start", pov: "Soren", scenes: [{ scene_no: 1, prose: "Text." }] },
      ],
    };
    render(<PacketsScreen />);
    const md = await screen.findByRole("button", { name: "Export Markdown" });
    expect(md).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Export Reader DOCX" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Export Shunn DOCX" })).not.toBeDisabled();
  });

  it("stays disabled when the manuscript has the chapter but every scene is empty", async () => {
    mockData.manuscript = {
      chapters: [{ chapter_no: 1, title: "The Start", pov: "Soren", scenes: [{ scene_no: 1, prose: "   " }] }],
    };
    render(<PacketsScreen />);
    expect(await screen.findByRole("button", { name: "Export Markdown" })).toBeDisabled();
  });
});
