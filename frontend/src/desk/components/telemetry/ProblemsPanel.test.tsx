import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProblemsPanel } from "./ProblemsPanel";
import type { TelemetryProblemOut } from "../../api/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const mockData = {
  jobs: { running: false, queued: 0, failed: 2, active_scene: null },
  failedJobs: [{ id: "j1", chapter_no: 1, scene_no: 1, last_error: "boom" }],
  retryFailed: vi.fn(),
  clearFailed: vi.fn(),
};

vi.mock("../../api/data", () => ({
  useDeskData: () => mockData,
}));

vi.mock("../../api/client", () => ({
  api: {
    telemetryProblems: vi.fn(),
  },
}));

import { api } from "../../api/client";

function problem(over: Partial<TelemetryProblemOut>): TelemetryProblemOut {
  return {
    kind: "error",
    severity: "warn",
    summary: "test problem",
    count: 1,
    breakdown: [],
    recommended_action: "",
    drill_down: {},
    ...over,
  };
}

describe("ProblemsPanel", () => {
  beforeEach(() => {
    vi.mocked(api.telemetryProblems).mockReset();
  });

  it("renders retry/clear controls only for failed_draft_job", async () => {
    vi.mocked(api.telemetryProblems).mockResolvedValue({
      healthy: false,
      problems: [problem({ kind: "failed_draft_job", summary: "2 failed draft jobs" })],
    });

    render(<ProblemsPanel bookId="book-1" onOpen={vi.fn()} />);

    expect(await screen.findByText("2 failed draft jobs")).toBeInTheDocument();
    expect(await screen.findByText(/Retry 2 failed/)).toBeInTheDocument();
    expect(await screen.findByText("Clear failed")).toBeInTheDocument();
  });

  it("renders a Settings link only for budget/latency problems", async () => {
    vi.mocked(api.telemetryProblems).mockResolvedValue({
      healthy: false,
      problems: [
        problem({ kind: "soft_work_budget_exceeded", summary: "soft budget exceeded" }),
        problem({ kind: "hard_work_budget_exceeded", summary: "hard budget exceeded" }),
        problem({ kind: "high_latency", summary: "slow calls" }),
      ],
    });

    render(<ProblemsPanel bookId="book-1" onOpen={vi.fn()} />);

    await screen.findByText("soft budget exceeded");
    expect(screen.getAllByText("Adjust in Settings → Agent Ops")).toHaveLength(3);
  });

  it("renders neither control for diagnostic-only problems", async () => {
    vi.mocked(api.telemetryProblems).mockResolvedValue({
      healthy: false,
      problems: [
        problem({ kind: "truncation", summary: "truncated calls" }),
        problem({ kind: "cache_prime_short", summary: "cache prime short" }),
        problem({ kind: "token_count_fallback", summary: "token fallback" }),
      ],
    });

    render(<ProblemsPanel bookId="book-1" onOpen={vi.fn()} />);

    await screen.findByText("truncated calls");
    expect(screen.queryByText("Clear failed")).not.toBeInTheDocument();
    expect(screen.queryByText("Adjust in Settings → Agent Ops")).not.toBeInTheDocument();
  });

  it("shows the healthy banner when there are no problems", async () => {
    vi.mocked(api.telemetryProblems).mockResolvedValue({ healthy: true, problems: [] });

    render(<ProblemsPanel bookId="book-1" onOpen={vi.fn()} />);

    expect(await screen.findByText("No problems detected")).toBeInTheDocument();
  });
});
