import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TelemetryScreen from "./TelemetryScreen";
import type { BookTelemetryOut, RunRollupOut, RunTelemetryOut } from "../api/types";

// A Read-through (ADR 0035) reads chapters the author supplies, so its calls carry no chapter and no
// scene. These cover the two places that fact shows: the run label, and the scene panel.

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/telemetry",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const deskData = vi.hoisted(() => ({
  bookId: "book-1",
  chapters: [],
  jobs: { running: false, queued: 0, failed: 0, active_scene: null },
  failedJobs: [],
  retryFailed: vi.fn(),
  clearFailed: vi.fn(),
}));

vi.mock("../api/data", () => ({ useDeskData: () => deskData }));

const apiMock = vi.hoisted(() => ({
  bookTelemetry: vi.fn(),
  runTelemetry: vi.fn(),
  telemetryProblems: vi.fn(),
  chapterTelemetry: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMock }));

const TOTALS = {
  calls: 2,
  input_tokens: 1800,
  output_tokens: 240,
  cache_creation_tokens: 0,
  cache_read_tokens: 0,
  cache_hit_ratio: 0,
  cache_tokens_saved: 0,
  truncations: 0,
  errors: 0,
  fallbacks: 0,
  avg_latency_ms: 900,
  estimated_cost_usd: 0.01,
  cache_savings_usd: 0,
};

const run = (over: Partial<RunRollupOut>): RunRollupOut =>
  ({
    run_id: "11111111-1111-1111-1111-111111111111",
    started_at: "2026-09-18T10:00:00Z",
    chapter_id: null,
    chapter_no: null,
    title: null,
    run_kind: null,
    ...TOTALS,
    ...over,
  }) as RunRollupOut;

const book = (runs: RunRollupOut[]): BookTelemetryOut =>
  ({
    totals: TOTALS,
    by_stage: [{ key: "read_through_chapter", ...TOTALS }],
    by_model: [{ key: "gpt-5.6-luna", ...TOTALS }],
    by_chapter: [],
    by_run: runs,
    by_production_run: [],
    by_kind: [],
    editorial_runs: [],
    run_total: runs.length,
  }) as unknown as BookTelemetryOut;

const runDetail = (over: Partial<RunTelemetryOut>): RunTelemetryOut =>
  ({
    run_id: "11111111-1111-1111-1111-111111111111",
    started_at: "2026-09-18T10:00:00Z",
    chapter_id: null,
    chapter_no: null,
    title: null,
    run_kind: null,
    totals: TOTALS,
    by_stage: [],
    by_model: [],
    scenes: [],
    calls: [],
    settings_snapshot: null,
    ...over,
  }) as unknown as RunTelemetryOut;

const scene = (scene_no: number | null) => ({
  scene_no,
  status: "ok",
  models: ["gpt-5.6-luna"],
  stages: ["read_through_chapter"],
  worst_latency_ms: 900,
  stage_summary: "",
  pipeline: [],
  ...TOTALS,
});

describe("TelemetryScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.telemetryProblems.mockResolvedValue({ problems: [] });
    deskData.bookId = `book-${Math.random().toString(36).slice(2)}`; // the screen caches per book id
  });

  it("names a read-through run instead of showing a bare id", async () => {
    const readThrough = run({ run_kind: "read_through" });
    apiMock.bookTelemetry.mockResolvedValue(book([readThrough]));
    apiMock.runTelemetry.mockResolvedValue(runDetail({ run_kind: "read_through" }));

    render(<TelemetryScreen />);

    // Both surfaces name it: the run table row and the run picker option.
    expect(await screen.findAllByText(/Read-through/)).toHaveLength(2);
    expect(screen.queryByText(/11111111/)).not.toBeInTheDocument();
  });

  it("still labels an ordinary run by its chapter", async () => {
    apiMock.bookTelemetry.mockResolvedValue(
      book([run({ chapter_no: 3, title: "Three", chapter_id: "c3" })]),
    );
    apiMock.runTelemetry.mockResolvedValue(runDetail({ scenes: [scene(1)] }));

    render(<TelemetryScreen />);

    expect(await screen.findByText(/Ch 3/)).toBeInTheDocument();
    expect(screen.queryByText(/Read-through/)).not.toBeInTheDocument();
  });

  it("hides the scene panel when the newest run has no scenes to show", async () => {
    apiMock.bookTelemetry.mockResolvedValue(book([run({ run_kind: "read_through" })]));
    // A scene-less run still returns one bucket, keyed by a null scene_no.
    apiMock.runTelemetry.mockResolvedValue(
      runDetail({ run_kind: "read_through", scenes: [scene(null)] }),
    );

    render(<TelemetryScreen />);

    await screen.findAllByText(/Read-through/);
    await waitFor(() => expect(apiMock.runTelemetry).toHaveBeenCalled());
    expect(screen.queryByText("By scene · latest run")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Sc—/)).not.toBeInTheDocument();
  });

  it("shows the scene panel for a run that has real scenes", async () => {
    apiMock.bookTelemetry.mockResolvedValue(book([run({ chapter_no: 3, chapter_id: "c3" })]));
    apiMock.runTelemetry.mockResolvedValue(runDetail({ scenes: [scene(1), scene(2)] }));

    render(<TelemetryScreen />);

    expect(await screen.findByText("By scene · latest run")).toBeInTheDocument();
  });
});
