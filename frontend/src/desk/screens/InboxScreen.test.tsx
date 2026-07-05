import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InboxScreen from "./InboxScreen";

// Desk Control Round, Phase 10: the Drafting desk as a command center — a "Needs your decision"
// queue (first five cards, select-all over the WHOLE queue), a Pipeline panel with queue quick
// actions (pause/resume), a per-chapter progress strip, and a collapsible planner. The old
// Revising/Approved kanban columns are gone.

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: routerPush }),
}));

const openScene = vi.fn();
const toggleActivity = vi.fn();
vi.mock("../state", () => ({
  useDesk: () => ({
    t: { accent: "#000", bad: "#f00", warn: "#e90", good: "#0a0", info: "#09f", dim: "#888" },
    openScene,
    openSceneId: vi.fn(),
    toggleActivity,
  }),
}));

// Child surfaces with their own data plumbing — stubbed so these tests exercise the Inbox
// composition itself, not the children.
vi.mock("../components/Planner", () => ({
  default: () => <div>planner-form</div>,
}));
vi.mock("../components/DraftActivity", () => ({
  DraftPanel: () => <div>draft-panel</div>,
  ActivityFeed: () => <div>activity-feed</div>,
  formatElapsed: () => null,
}));
vi.mock("../components/ClearFailedPanel", () => ({
  default: () => null,
}));

vi.mock("../api/client", () => ({
  api: {
    decide: vi.fn(),
    scene: vi.fn(),
    annotations: vi.fn(),
    suggestions: vi.fn(),
  },
}));

const pendingScene = (n: number) => ({
  id: `p${n}`,
  chapter_id: "ch-1",
  scene_no: n,
  version: 1,
  status: "draft",
  prose: `Pending prose for scene number ${n} runs on.`,
});

const latestScene = (n: number, status: string, chapterId: string) => ({
  id: `l${n}`,
  chapter_id: chapterId,
  scene_no: n,
  version: 1,
  status,
  prose: `Latest prose for scene number ${n} runs on.`,
});

// Fixture book: 6 pending (only 5 render as cards), ch-1 with 2/3 approved + 1 revising, ch-2 with
// 0/2 approved + 1 revising — so "Revising 2" and the per-chapter counts are both non-trivial.
const baseData = () => ({
  loading: false,
  books: [{ id: "b1", title: "Realmwalkers" }],
  bookId: "b1",
  chapters: [
    {
      id: "ch-1",
      chapter_no: 1,
      title: "Signal Fire",
      pov: "Mara",
      outline: "",
      status: "planned",
    },
    { id: "ch-2", chapter_no: 2, title: "Undertow", pov: "Mara", outline: "", status: "planned" },
  ],
  latestScenes: [
    latestScene(1, "approved", "ch-1"),
    latestScene(2, "approved", "ch-1"),
    latestScene(3, "revision_requested", "ch-1"),
    latestScene(4, "revision_requested", "ch-2"),
    latestScene(5, "draft", "ch-2"),
  ],
  pending: [1, 2, 3, 4, 5, 6].map(pendingScene),
  manuscript: null,
  jobs: { running: false, queued: 2, failed: 1, queue_paused: false, active_scene: null },
  failedJobs: [],
  activity: [],
  draftNext: vi.fn(),
  setQueuePaused: vi.fn(),
  runBulk: vi.fn(),
  deleteScenes: vi.fn(),
  retryFailed: vi.fn(),
  clearFailed: vi.fn(),
});

let mockData = baseData();
vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

describe("InboxScreen command center", () => {
  beforeEach(() => {
    mockData = baseData();
    routerPush.mockReset();
    openScene.mockReset();
    toggleActivity.mockReset();
  });

  it("lists the first five pending scenes and select-all covers ALL pending, not just the visible", () => {
    render(<InboxScreen />);
    expect(screen.getByText("Needs your decision")).toBeInTheDocument();
    expect(screen.getByText("6 awaiting review")).toBeInTheDocument();
    expect(screen.getByText("Scene 1")).toBeInTheDocument();
    expect(screen.getByText("Scene 5")).toBeInTheDocument();
    expect(screen.queryByText("Scene 6")).not.toBeInTheDocument();
    expect(screen.getByText("+1 more awaiting")).toBeInTheDocument();

    // Select-all ticks every pending scene (6), not just the 5 rendered cards — the BulkBar count
    // is the proof.
    fireEvent.click(screen.getByLabelText(/select all 6/));
    expect(screen.getByText("6 scenes selected")).toBeInTheDocument();
  });

  it("offers Pause when the queue runs and requests setQueuePaused(true)", () => {
    render(<InboxScreen />);
    expect(screen.getByText("2 queued · 1 failed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(mockData.setQueuePaused).toHaveBeenCalledWith(true);
  });

  it("shows the paused queue summary and offers Resume when paused", () => {
    mockData.jobs.queue_paused = true;
    render(<InboxScreen />);
    expect(screen.getByText("2 queued · 1 failed · paused")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(mockData.setQueuePaused).toHaveBeenCalledWith(false);
  });

  it("wires Draft next and Activity quick actions", () => {
    render(<InboxScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Draft next" }));
    expect(mockData.draftNext).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    expect(toggleActivity).toHaveBeenCalledTimes(1);
  });

  it("renders one progress row per chapter with approved/total counts, linking to /chapters", () => {
    render(<InboxScreen />);
    const row = screen.getByRole("button", { name: /Ch 1 · Signal Fire/ });
    expect(within(row).getByText("2/3")).toBeInTheDocument();
    expect(
      within(screen.getByRole("button", { name: /Ch 2 · Undertow/ })).getByText("0/2"),
    ).toBeInTheDocument();
    fireEvent.click(row);
    expect(routerPush).toHaveBeenCalledWith("/chapters");
  });

  it("shows the Revising chip with the count and routes it to the chapters board", () => {
    render(<InboxScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Revising 2" }));
    expect(routerPush).toHaveBeenCalledWith("/chapters");
  });

  it("collapses the planner when chapters exist and the header toggle opens it", () => {
    render(<InboxScreen />);
    expect(screen.queryByText("planner-form")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Plan a chapter" }));
    expect(screen.getByText("planner-form")).toBeInTheDocument();
  });

  it("opens the planner by default on an empty book (no chapters)", () => {
    mockData.chapters = [];
    mockData.latestScenes = [];
    render(<InboxScreen />);
    expect(screen.getByText("planner-form")).toBeInTheDocument();
  });

  it("no longer renders the old kanban column headers", () => {
    render(<InboxScreen />);
    expect(screen.queryByText("Awaiting review")).not.toBeInTheDocument();
    expect(screen.queryByText("Revising")).not.toBeInTheDocument();
    expect(screen.queryByText("Approved")).not.toBeInTheDocument();
  });
});
