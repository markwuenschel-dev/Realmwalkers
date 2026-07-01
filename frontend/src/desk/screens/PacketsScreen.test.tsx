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

const mockData = {
  chapters: CHAPTERS,
  failedJobs: [],
  jobs: { running: false, queued: 0, failed: 0, active_scene: null },
  clearFailed: vi.fn(),
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
