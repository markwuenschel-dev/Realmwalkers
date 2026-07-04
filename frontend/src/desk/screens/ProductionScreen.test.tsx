import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProductionScreen from "./ProductionScreen";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: routerPush }),
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

// jsdom has no URL.createObjectURL — assert the download call instead of a real browser download.
vi.mock("../lib/download", () => ({
  downloadBlob: vi.fn(),
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
    packet: vi.fn(),
    draftReadiness: vi.fn(),
  },
}));

import { api } from "../api/client";
import { downloadBlob } from "../lib/download";

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

// A proposed (not yet approved) chapter packet carrying only repair/warn findings — approvable, but
// production refuses to start until it IS approved. Second violation predates the blocks_* booleans
// to exercise the severity-derived fallback.
const GATE_PACKET = {
  id: "packet-1",
  book_id: "book-1",
  chapter_id: "chapter-1",
  status: "proposed",
  confidence: "yellow",
  qa_verdict: "approve_with_warnings",
  body: {},
  qa_warnings: {
    issues: [
      {
        kind: "leaked_reveal",
        field: "allowed_knowledge",
        detail: "Reader learns the warden's name too early.",
        severity: "repair",
        blocks_drafting: false,
        blocks_human_review: false,
        blocks_final_export: true,
      },
    ],
    violations: [
      {
        kind: "roster_double_bucketed",
        field: "characters_present",
        detail: "Mara is both present and absent.",
        severity: "repair",
      },
    ],
  },
  open_questions: { items: [] },
  created_at: "2026-07-02T10:00:00Z",
  can_approve: true,
  approval_blockers: [],
};

const BLOCKED_PACKET = {
  ...GATE_PACKET,
  status: "blocked",
  confidence: "red",
  qa_verdict: "block_drafting",
  can_approve: false,
  blocked_reason: "deterministic validation failed: packet body is not a JSON object",
  blocker_source: "validation",
  blocker_kind: "contract_validation",
  qa_warnings: {
    violations: [
      {
        kind: "invalid_body",
        field: null,
        detail: "chapter packet body is not a JSON object",
        severity: "block",
        blocks_drafting: true,
        blocks_human_review: true,
        blocks_final_export: true,
      },
    ],
  },
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
    vi.mocked(api.packet).mockReset().mockRejectedValue(new Error("404 Not Found"));
    // Full prose coverage by default, so the Assemble-chapter button stays enabled in these tests;
    // the assembly-gate test overrides this with missing scenes.
    vi.mocked(api.draftReadiness)
      .mockReset()
      .mockResolvedValue({
        chapter_id: "chapter-1",
        chapter_packet_approved: true,
        scene_packets: { approved: 4, expected: 4 },
        beats: { approved: 4, linked: 4, unlinked: [] },
        jobs: { active: 0, malformed: 0, failed_scene_packet_required: 0 },
        prose: {
          scenes_with_prose: 4,
          expected_scenes: 4,
          missing_scene_numbers: [],
          assembly_ready: true,
        },
        draftable: false,
        disabled_reason: "Every scene already has a draft — use redraft to regenerate a scene.",
        blockers: [],
        scene_packets_stale: 0,
        scene_packet_qa_blocking: 0,
        active_draft_jobs: 0,
        missing_scene_drafts: [],
        structural_blockers: [],
        provider_rate_limited: false,
        can_draft: false,
      });
    routerPush.mockReset();
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

    fireEvent.click(screen.getByRole("button", { name: "Assemble chapter" }));
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

  it("reports what Apply did instead of silently mutating the task", async () => {
    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(api.applyRepairTask).toHaveBeenCalledTimes(1));
    const notice = await screen.findByTestId("production-notice");
    expect(notice.textContent).toContain("Repair applied for Scene 1");
  });

  it("disables every action while one is in flight (double-submit guard)", async () => {
    // Regression: `busy` is a single slot, so per-label disabling let a second click flip it and
    // re-enable the first button while its request was still in flight.
    vi.mocked(api.applyRepairTask).mockImplementation(() => new Promise<never>(() => {}));
    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Applying…" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Verify" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Auto-triage" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh assembly" })).toBeDisabled();
  });

  it("explains the triage no-op instead of silently doing nothing", async () => {
    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    // The fixture's only issue is already repair_queued — nothing left in `proposed`, so triage
    // would be a deterministic server-side no-op. The button must say so, not silently succeed.
    fireEvent.click(screen.getByRole("button", { name: "Auto-triage" }));
    const notice = await screen.findByTestId("production-notice");
    expect(notice.textContent).toContain("Nothing to triage");
    expect(api.triageProductionRun).not.toHaveBeenCalled();

    fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("production-notice")).not.toBeInTheDocument();
  });

  it("explains a blocked assembly with expandable per-gate diagnostics", async () => {
    // Half-drafted chapter: assembly is disabled, and the gate block must say exactly why —
    // pass/fail per gate, never a bare disabled button.
    vi.mocked(api.draftReadiness).mockResolvedValue({
      chapter_id: "chapter-1",
      chapter_packet_approved: true,
      scene_packets: { approved: 4, expected: 4 },
      beats: { approved: 4, linked: 4, unlinked: [] },
      jobs: { active: 1, malformed: 0, failed_scene_packet_required: 0 },
      prose: {
        scenes_with_prose: 2,
        expected_scenes: 4,
        missing_scene_numbers: [3, 4],
        assembly_ready: false,
      },
      draftable: true,
      disabled_reason: null,
      blockers: [],
      scene_packets_stale: 0,
      scene_packet_qa_blocking: 0,
      active_draft_jobs: 1,
      missing_scene_drafts: [3, 4],
      structural_blockers: [],
      provider_rate_limited: false,
      can_draft: true,
    });
    render(<ProductionScreen />);

    const gate = await screen.findByTestId("assembly-gate");
    expect(screen.getByRole("button", { name: "Assemble chapter" })).toBeDisabled();

    fireEvent.click(within(gate).getByRole("button", { name: /Why is this disabled\?/ }));
    expect(within(gate).getByText(/2\/4 scenes have prose · missing: 3, 4/)).toBeInTheDocument();
    expect(
      within(gate).getByText(/1 active — scenes may still be arriving/),
    ).toBeInTheDocument();
    expect(within(gate).getAllByText("fail").length).toBeGreaterThanOrEqual(2);
  });

  it("exposes the full run state as viewable + downloadable JSON after each step", async () => {
    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    // Toggle the inline inspector: the raw ProductionRunDetailOut, not a summary.
    fireEvent.click(screen.getByRole("button", { name: "Run JSON" }));
    const panel = await screen.findByTestId("run-json");
    expect(panel.textContent).toContain('"event_type": "repair_task_created"');
    expect(panel.textContent).toContain('"repair_kind": "dialogue"');

    // Download carries the run id + current stage so per-step downloads sort meaningfully.
    fireEvent.click(screen.getByRole("button", { name: "Download JSON" }));
    expect(downloadBlob).toHaveBeenCalledWith(
      "production_run_run-1_repair_queue.json",
      expect.stringContaining('"status": "repairing"'),
      "application/json",
    );

    fireEvent.click(screen.getByRole("button", { name: "Hide run JSON" }));
    expect(screen.queryByTestId("run-json")).not.toBeInTheDocument();
  });

  it("renders structured remediation instead of the raw error when no packet is approved", async () => {
    vi.mocked(api.startProductionRun).mockRejectedValue(
      Object.assign(
        new Error('409 Conflict — {"detail":"no approved chapter packet for this chapter"}'),
        { status: 409, data: { detail: "no approved chapter packet for this chapter" } },
      ),
    );
    vi.mocked(api.packet).mockResolvedValue(GATE_PACKET);

    render(<ProductionScreen />);
    await screen.findByText("Final chapter prose.");

    fireEvent.click(screen.getByRole("button", { name: "Assemble chapter" }));

    const gate = await screen.findByTestId("production-gate");
    expect(api.packet).toHaveBeenCalledWith("chapter-1");
    // Says exactly why: a packet exists but is not approved yet. (findBy: the panel shows a
    // "checking the packet" placeholder until the packet fetch resolves.)
    expect(await within(gate).findByText(/not approved yet/)).toBeInTheDocument();
    // Repair tasks as actionable items: kind/field/detail with a severity badge, both for a new row
    // (persisted blocks_*) and an old row (severity-derived fallback).
    expect(within(gate).getByText(/Mara is both present and absent/)).toBeInTheDocument();
    expect(within(gate).getByText(/Reader learns the warden's name too early/)).toBeInTheDocument();
    expect(
      within(gate).getByText(/roster_double_bucketed · characters_present/),
    ).toBeInTheDocument();
    expect(within(gate).getAllByText("repair")).toHaveLength(2);
    // Repair ≠ blocked: the copy says drafting can proceed while export waits.
    expect(within(gate).getByText(/never block approval or drafting/)).toBeInTheDocument();
    // Never the raw exception dump.
    expect(screen.queryByText(/409 Conflict/)).not.toBeInTheDocument();

    fireEvent.click(within(gate).getByRole("button", { name: "Go to Packets" }));
    expect(routerPush).toHaveBeenCalledWith("/packets?chapter=chapter-1");
  });

  it("renders remediation for a blocked run from the packet's machine-readable blockers", async () => {
    const blockedRun = { ...RUN, status: "blocked", current_stage: "packet_gate" };
    vi.mocked(api.productionRuns).mockResolvedValue([blockedRun]);
    vi.mocked(api.productionRun).mockResolvedValue({ ...DETAIL, run: blockedRun });
    vi.mocked(api.packet).mockResolvedValue(BLOCKED_PACKET);

    render(<ProductionScreen />);

    const gate = await screen.findByTestId("production-gate");
    expect(
      await within(gate).findByText("Blocked by deterministic validation"),
    ).toBeInTheDocument();
    expect(within(gate).getByText(/deterministic validation failed/)).toBeInTheDocument();
    expect(within(gate).getByText(/chapter packet body is not a JSON object/)).toBeInTheDocument();
    expect(within(gate).getByText("block")).toBeInTheDocument();
    expect(within(gate).getByRole("button", { name: "Go to Packets" })).toBeInTheDocument();
  });
});
