import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChaptersScreen from "./ChaptersScreen";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const openSceneId = vi.fn();
vi.mock("../state", () => ({
  useDesk: () => ({
    chaptersView: "board",
    setChaptersView: vi.fn(),
    openSceneId,
    t: { accent: "#000" },
  }),
}));

vi.mock("../api/client", () => ({
  api: {
    chaptersOverview: vi.fn(),
    redraftScenes: vi.fn(),
    createHumanScene: vi.fn(),
  },
}));

import { api } from "../api/client";

const CH1 = { id: "ch-1", chapter_no: 1, title: "Signal Fire", pov: "Mara", status: "planned" };
const CH2 = { id: "ch-2", chapter_no: 2, title: "Relay", pov: "Seb", status: "planned" };
const SCENE = {
  id: "s1",
  chapter_id: "ch-1",
  scene_no: 1,
  version: 1,
  status: "approved",
  prose: "Mara vaulted the breach.",
};

// One rich pipeline row (gated) + one ready row (no disclosure, no runs).
const ROW1 = {
  chapter_id: "ch-1",
  chapter_no: 1,
  packet_status: "approved",
  packet_approval_state: "already_approved",
  packet_approval_blockers: ["Packet already approved — edit or re-propose to make changes."],
  scene_packets_total: 3,
  scene_packets_approved: 2,
  scene_packets_blocked: 0,
  scene_packets_stale: 1,
  scene_packets_rate_limited: 0,
  violation_counts: { repair: 1, hard: 1 },
  scenes_with_prose: 1,
  expected_scenes: 3,
  assembly_ready: false,
  can_draft: false,
  disabled_reason: "1 scene packet(s) are stale — re-derive or re-approve them before drafting.",
  active_draft_jobs: 0,
  provider_rate_limited: false,
  latest_run: {
    id: "run-1",
    status: "waiting_for_human",
    current_stage: "repair_queue",
    issue_count: 2,
    repair_task_count: 1,
    updated_at: "2026-07-05T10:00:00Z",
  },
};
const ROW2 = {
  ...ROW1,
  chapter_id: "ch-2",
  chapter_no: 2,
  scene_packets_total: 2,
  scene_packets_approved: 2,
  scene_packets_stale: 0,
  violation_counts: {},
  scenes_with_prose: 0,
  expected_scenes: 2,
  can_draft: true,
  disabled_reason: null,
  latest_run: null,
};

function baseData() {
  return {
    loading: false,
    chapters: [CH1, CH2],
    latestScenes: [SCENE],
    pending: [],
    manuscript: null,
    books: [{ id: "b1", title: "Realmwalkers" }],
    bookId: "b1",
    jobs: { running: 0, queued: 0, failed: 0, queue_paused: false, active_scene: null },
    pushToast: vi.fn(),
    draftNext: vi.fn().mockResolvedValue(undefined),
    refreshAll: vi.fn().mockResolvedValue(undefined),
    updateChapter: vi.fn().mockResolvedValue(undefined),
  };
}
let mockData = baseData();
vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

describe("ChaptersScreen pipeline command center", () => {
  beforeEach(() => {
    mockData = baseData();
    routerPush.mockReset();
    openSceneId.mockReset();
    vi.mocked(api.chaptersOverview).mockReset().mockResolvedValue([ROW1, ROW2]);
    vi.mocked(api.redraftScenes)
      .mockReset()
      .mockResolvedValue({ queued: 1 } as never);
    vi.mocked(api.createHumanScene)
      .mockReset()
      .mockResolvedValue({ id: "new-scene" } as never);
  });

  it("renders the pipeline strip per chapter from ONE overview fetch", async () => {
    render(<ChaptersScreen />);

    expect(await screen.findAllByText("packet approved")).toHaveLength(2);
    expect(api.chaptersOverview).toHaveBeenCalledTimes(1);
    expect(api.chaptersOverview).toHaveBeenCalledWith("b1");
    // Contract + prose counts, run facts, gate chips.
    expect(screen.getByText(/contracts 2\/3 · prose 1\/3/)).toBeInTheDocument();
    expect(screen.getByText("waiting for human")).toBeInTheDocument();
    expect(screen.getByText(/2 issues · 1 repairs/)).toBeInTheDocument();
    expect(screen.getByText("not ready")).toBeInTheDocument();
    expect(screen.getByText("ready to draft")).toBeInTheDocument();
    expect(screen.getByText("no runs")).toBeInTheDocument();
  });

  it("folds violation severities — legacy 'hard' renders as block, never raw", async () => {
    render(<ChaptersScreen />);
    expect(await screen.findByText("1 repair")).toBeInTheDocument();
    expect(screen.getByText("1 block")).toBeInTheDocument();
    expect(screen.queryByText(/hard/)).not.toBeInTheDocument();
  });

  it("opens the gate disclosure with the backend's verbatim reason", async () => {
    render(<ChaptersScreen />);
    // Only the gated chapter shows a disclosure; the ready one doesn't.
    const toggles = await screen.findAllByRole("button", { name: /Why is this disabled\?/ });
    expect(toggles).toHaveLength(1);

    fireEvent.click(toggles[0]);
    expect(
      screen.getByText(
        "1 scene packet(s) are stale — re-derive or re-approve them before drafting.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/2\/3 approved · 1 stale/)).toBeInTheDocument();
    expect(screen.getByText(/1\/3 scenes have prose/)).toBeInTheDocument();
  });

  it("deep-links each pipeline segment to the tab that acts on it", async () => {
    render(<ChaptersScreen />);
    fireEvent.click((await screen.findAllByText("packet approved"))[0]);
    expect(routerPush).toHaveBeenCalledWith("/packets?chapter=ch-1");

    fireEvent.click(screen.getByText(/2 issues · 1 repairs/));
    expect(routerPush).toHaveBeenCalledWith("/production?chapter=ch-1");
  });

  it("keeps the chapter panels alive when the overview fetch fails", async () => {
    vi.mocked(api.chaptersOverview).mockRejectedValue(new Error("boom"));
    render(<ChaptersScreen />);

    expect(await screen.findAllByText(/pipeline — \(unavailable/)).toHaveLength(2);
    // Existing panels unaffected: the chapter header and the board still render.
    expect(screen.getByText(/Signal Fire/)).toBeInTheDocument();
    expect(screen.getByText("Ch 1 · Scene 1")).toBeInTheDocument();
  });

  it("routes the empty state to the Inbox", async () => {
    mockData.chapters = [];
    mockData.latestScenes = [];
    vi.mocked(api.chaptersOverview).mockResolvedValue([]);
    render(<ChaptersScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Go to Inbox" }));
    expect(routerPush).toHaveBeenCalledWith("/inbox");
  });

  it("writes a section by hand via createHumanScene", async () => {
    render(<ChaptersScreen />);
    fireEvent.click((await screen.findAllByText("+ Write section by hand"))[0]);

    fireEvent.change(screen.getByPlaceholderText("scene #"), { target: { value: "2" } });
    fireEvent.change(screen.getByPlaceholderText("write the prose for this section…"), {
      target: { value: "Hand-written prose." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save section" }));

    await waitFor(() =>
      expect(api.createHumanScene).toHaveBeenCalledWith("ch-1", {
        scene_no: 2,
        prose: "Hand-written prose.",
      }),
    );
  });

  it("warns via toast when redrafting into a paused queue", async () => {
    mockData.jobs = { ...mockData.jobs, queue_paused: true };
    render(<ChaptersScreen />);

    fireEvent.click(await screen.findByTitle("select to re-draft"));
    fireEvent.click(screen.getByRole("button", { name: "Re-draft selected" }));

    await waitFor(() => expect(api.redraftScenes).toHaveBeenCalledWith("ch-1", ["s1"]));
    await waitFor(() =>
      expect(mockData.pushToast).toHaveBeenCalledWith({
        tone: "warn",
        message: "Queued 1 redraft — the queue is paused; they draft after you resume",
      }),
    );
  });

  it("opens a board card as a scene", async () => {
    render(<ChaptersScreen />);
    fireEvent.click(await screen.findByText("Ch 1 · Scene 1"));
    expect(openSceneId).toHaveBeenCalledWith("s1");
  });
});
