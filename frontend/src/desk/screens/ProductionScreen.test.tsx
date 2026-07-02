import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProductionScreen from "./ProductionScreen";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("../state", () => ({
  useDesk: () => ({
    t: { accent: "#000", onAccent: "#fff" },
  }),
}));

const mockData = {
  chapters: [
    {
      id: "chapter-1",
      chapter_no: 1,
      title: "Signal Fire",
      pov: "Mara",
      outline: "Mara chooses speed over certainty.",
      status: "planned",
    },
  ],
};

vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

vi.mock("../components/DraftActivity", () => ({
  Spinner: () => <span>spinner</span>,
}));

vi.mock("../components/ProseBlocks", () => ({
  default: ({ text }: { text: string }) => <div>{text}</div>,
}));

vi.mock("../api/client", () => ({
  api: {
    productionRuns: vi.fn(),
    productionRun: vi.fn(),
    startProductionRun: vi.fn(),
    triageProductionRun: vi.fn(),
    assembleProductionRun: vi.fn(),
    applyRepairTask: vi.fn(),
    verifyRepairTask: vi.fn(),
    repairTask: vi.fn(),
  },
}));

import { api } from "../api/client";

const RUN = {
  id: "run-1",
  book_id: "book-1",
  chapter_id: "chapter-1",
  status: "repairing",
  mode: "full_chapter",
  current_stage: "repair_queue",
  summary_json: { issue_count: 1, repair_task_count: 1 },
  created_at: "2026-07-02T10:00:00Z",
  updated_at: "2026-07-02T10:00:00Z",
};

const DETAIL = {
  run: RUN,
  chapter_sequence: {
    id: "sequence-1",
    book_id: "book-1",
    chapter_id: "chapter-1",
    chapter_packet_id: "packet-1",
    status: "approved",
    body: { scenes: [{ scene_no: 1, scene_function: "Mara breaks through the breach." }] },
    created_at: "2026-07-02T10:00:00Z",
    updated_at: "2026-07-02T10:00:00Z",
  },
  artifacts: [
    {
      id: "draft-1",
      artifact_type: "chapter_draft",
      body: { prose: "Draft prose." },
      version: 1,
      status: "active",
      content_hash: "draft",
      created_at: "2026-07-02T10:00:00Z",
    },
    {
      id: "final-1",
      artifact_type: "final_chapter",
      body: { prose: "Final chapter prose." },
      version: 1,
      status: "active",
      content_hash: "final",
      created_at: "2026-07-02T10:00:00Z",
    },
  ],
  dependencies: [],
  agent_runs: [],
  events: [
    {
      id: "event-1",
      production_run_id: "run-1",
      event_type: "repair_task_created",
      stage: "issue_triage",
      message: "Repair task queued for scene 1",
      created_at: "2026-07-02T10:00:00Z",
    },
  ],
  issues: [
    {
      id: "issue-1",
      production_run_id: "run-1",
      chapter_id: "chapter-1",
      artifact_type: "scene_review_report",
      artifact_id: "artifact-1",
      scene_no: 1,
      validator: "dialogue",
      issue_kind: "dialogue",
      severity: "warn",
      claim: "Dialogue reads flat and generic.",
      recommended_action: "Revise dialogue.",
      auto_repair_allowed: true,
      status: "repair_queued",
      created_at: "2026-07-02T10:00:00Z",
    },
  ],
  issue_decisions: [],
  repair_tasks: [
    {
      id: "task-1",
      production_run_id: "run-1",
      chapter_id: "chapter-1",
      scene_no: 1,
      repair_kind: "dialogue",
      authority_level: "span_only",
      status: "queued",
      issue_ids: ["issue-1"],
      instructions: "Tighten the dialogue while keeping the scene outcome intact.",
      preserve: ["Keep the scene outcome intact."],
      must_change: ["Dialogue reads flat and generic."],
      must_not_change: ["Do not change canon."],
      allowed_operations: ["replace_span"],
      forbidden_operations: ["change_canon"],
      requires_human_approval: false,
      created_at: "2026-07-02T10:00:00Z",
      updated_at: "2026-07-02T10:00:00Z",
    },
  ],
  repair_attempts: [],
  repair_verifications: [],
};

describe("ProductionScreen", () => {
  beforeEach(() => {
    vi.mocked(api.productionRuns).mockReset().mockResolvedValue([RUN]);
    vi.mocked(api.productionRun).mockReset().mockResolvedValue(DETAIL);
    vi.mocked(api.startProductionRun).mockReset().mockResolvedValue({
      run: RUN,
      issue_count: 1,
      repair_task_count: 1,
      latest_verification: null,
    });
    vi.mocked(api.triageProductionRun).mockReset().mockResolvedValue({
      run: RUN,
      issue_count: 1,
      repair_task_count: 1,
      latest_verification: null,
    });
    vi.mocked(api.assembleProductionRun).mockReset().mockResolvedValue({
      run: RUN,
      issue_count: 1,
      repair_task_count: 1,
      latest_verification: null,
    });
    vi.mocked(api.applyRepairTask).mockReset().mockResolvedValue(DETAIL.repair_tasks[0]);
    vi.mocked(api.verifyRepairTask)
      .mockReset()
      .mockResolvedValue({
        id: "verification-1",
        repair_attempt_id: "attempt-1",
        verdict: "accept",
        resolved_issue_ids: ["issue-1"],
        remaining_issue_ids: [],
        target_issue_resolved: true,
        canon_preserved: true,
        scene_outcome_preserved: true,
        voice_preserved: true,
        required_beats_preserved: true,
        reader_state_preserved: true,
        regression_score: 0,
        created_at: "2026-07-02T10:00:00Z",
      });
    vi.mocked(api.repairTask).mockReset().mockResolvedValue(DETAIL.repair_tasks[0]);
  });

  it("loads the latest run detail and renders issues, tasks, and final chapter prose", async () => {
    render(<ProductionScreen />);

    expect(await screen.findByText("Final chapter prose.")).toBeInTheDocument();
    expect(screen.getByText("Dialogue reads flat and generic.")).toBeInTheDocument();
    expect(screen.getByText(/Tighten the dialogue/)).toBeInTheDocument();
    expect(api.productionRuns).toHaveBeenCalledWith("chapter-1");
    expect(api.productionRun).toHaveBeenCalledWith("run-1");
  });

  it("starts a run and lets the user apply and verify a repair task", async () => {
    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() =>
      expect(api.startProductionRun).toHaveBeenCalledWith({
        chapter_id: "chapter-1",
        auto_triage: true,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyRepairTask).toHaveBeenCalledWith("task-1"));

    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(api.verifyRepairTask).toHaveBeenCalledWith("task-1"));
    expect(api.repairTask).toHaveBeenCalledWith("task-1");
  });
});
