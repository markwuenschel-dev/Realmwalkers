import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DiffScreen from "./DiffScreen";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ sceneId: "s1" }),
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const V1 = {
  id: "v1-id",
  chapter_id: "ch-1",
  scene_no: 2,
  version: 1,
  status: "superseded",
  prose: "First line.\nSecond line.",
  agent_original: null as string | null,
};
const V2 = {
  id: "v2-id",
  chapter_id: "ch-1",
  scene_no: 2,
  version: 2,
  status: "superseded",
  prose: "First line.\nSecond line, revised.",
  agent_original: "First line.\nSecond line, revised.",
};
const V3 = {
  id: "v3-id",
  chapter_id: "ch-1",
  scene_no: 2,
  version: 3,
  status: "approved",
  prose: "First line.\nSecond line, polished by hand.",
  agent_original: "First line.\nSecond line, as the agent wrote it.",
};

function baseData() {
  return {
    detail: { id: "s1", scene_no: 2, chapter_id: "ch-1", version: 3 } as {
      id: string;
      scene_no: number;
    } | null,
    versions: [V1, V2, V3] as (typeof V3)[],
    openSceneById: vi.fn(),
    revertScene: vi.fn().mockResolvedValue(undefined),
  };
}
let mockData = baseData();
vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

describe("DiffScreen", () => {
  beforeEach(() => {
    mockData = baseData();
    routerPush.mockReset();
  });

  it("defaults to comparing the last two versions", () => {
    render(<DiffScreen />);
    expect(screen.getByText(/Before · v2$/)).toBeInTheDocument();
    expect(screen.getByText(/After · v3 — current/)).toBeInTheDocument();
    // The changed line shows on both sides of the split.
    expect(screen.getByText(/Second line, revised\./)).toBeInTheDocument();
    expect(screen.getByText(/Second line, polished by hand\./)).toBeInTheDocument();
  });

  it("reverts to an older version only after an explicit confirm — cancel backs out", async () => {
    render(<DiffScreen />);
    fireEvent.change(screen.getByTitle("compare from"), { target: { value: "v1-id" } });

    fireEvent.click(screen.getByRole("button", { name: /⟲ Revert to v1/ }));
    expect(mockData.revertScene).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Confirm revert" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /⟲ Revert to v1/ }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm revert" }));
    await waitFor(() => expect(mockData.revertScene).toHaveBeenCalledWith("v1-id"));
  });

  it("agent mode diffs the agent original against the final text of ONE version", () => {
    render(<DiffScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Agent vs final" }));

    expect(screen.getByText("Before · v3 — agent original")).toBeInTheDocument();
    expect(screen.getByText("After · v3 — your final")).toBeInTheDocument();
    expect(screen.getByText(/as the agent wrote it\./)).toBeInTheDocument();
    expect(screen.getByText(/polished by hand\./)).toBeInTheDocument();
    // Version-vs-version affordances leave: no base select, no revert.
    expect(screen.queryByTitle("compare from")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Revert/ })).not.toBeInTheDocument();
  });

  it("explains a version with no preserved agent original instead of a blank diff", () => {
    render(<DiffScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Agent vs final" }));
    fireEvent.change(screen.getByTitle("version to inspect"), { target: { value: "v1-id" } });

    expect(screen.getByText(/v1 has no preserved agent original/)).toBeInTheDocument();
  });

  it("says so when the final matches the agent original (no human edits)", () => {
    render(<DiffScreen />);
    fireEvent.click(screen.getByRole("button", { name: "Agent vs final" }));
    fireEvent.change(screen.getByTitle("version to inspect"), { target: { value: "v2-id" } });

    expect(screen.getByText(/No human edits — the final text matches/)).toBeInTheDocument();
  });

  it("navigates: open → per version and ← Back to scene", () => {
    render(<DiffScreen />);
    fireEvent.click(screen.getAllByText("open →")[0]);
    expect(routerPush).toHaveBeenCalledWith("/scene/v2-id");

    fireEvent.click(screen.getByRole("button", { name: "← Back to scene" }));
    expect(routerPush).toHaveBeenCalledWith("/scene/v3-id");
  });

  it("a single-version lineage lands in agent mode instead of a dead end", () => {
    mockData.versions = [V3];
    render(<DiffScreen />);

    expect(screen.getByText("Before · v3 — agent original")).toBeInTheDocument();
    const versionsToggle = screen.getByRole("button", { name: "Versions" });
    expect(versionsToggle).toBeDisabled();
    expect(versionsToggle.getAttribute("title")).toContain("Only one version exists");
  });
});
