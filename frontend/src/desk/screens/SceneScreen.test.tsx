import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SceneScreen from "./SceneScreen";

const routerPush = vi.fn();
const routerReplace = vi.fn();
let mockParams: { sceneId?: string } = {};
vi.mock("next/navigation", () => ({
  useParams: () => mockParams,
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  useSearchParams: () => new URLSearchParams(),
}));

// Mutable so individual tests can pick the rail tab / mode before render.
const mockDesk = {
  activeScene: 0,
  mode: "reading" as string,
  setMode: vi.fn(),
  tab: "continuity" as string,
  setTab: vi.fn(),
  rawProse: "",
  setProse: vi.fn(),
  feedback: "",
  setFeedback: vi.fn(),
  hoveredKey: null as string | null,
  setHover: vi.fn(),
  clearHover: vi.fn(),
  selectedAnn: null,
  selectAnn: vi.fn(),
  highlightAnn: vi.fn(),
  isLight: false,
  prevScene: vi.fn(),
  nextScene: vi.fn(),
  decide: vi.fn(), // instant-feedback toast hook, fired alongside data.decide
};
vi.mock("../state", () => ({
  useDesk: () => mockDesk,
}));

vi.mock("../components/CanonCard", () => ({
  default: () => <span>canon-card</span>,
}));
vi.mock("../components/ProseBlocks", () => ({
  default: ({ text }: { text: string }) => <div>{text}</div>,
  ProseInline: ({ text }: { text: string }) => <span>{text}</span>,
}));

vi.mock("../api/client", () => ({
  api: {
    draftAttempts: vi.fn().mockResolvedValue([]),
    // SceneFidelityReview fetches this on mount; return the inert shape so it renders nothing here.
    sceneFidelity: vi.fn().mockResolvedValue({
      scene_id: "s",
      has_report: false,
      is_current: false,
      currentness_reason: "no_active_contract",
      report_artifact_id: null,
      clause_evaluations: [],
      operational_holds: [],
    }),
  },
}));

// Continuity conflict (payload carries both values) + one legacy-severity note + one info note.
const CONFLICT = {
  id: "crit-1",
  reviewer: "continuity",
  severity: "block",
  note: null,
  payload: {
    character: "Mara",
    attribute: "level",
    prose_value: "12",
    ledger_value: "15",
    context_sentence: "Her interface read LEVEL 12.",
  },
};
const LEGACY_NOTE = {
  id: "crit-2",
  reviewer: "budget",
  severity: "hard",
  note: "token budget exceeded; saved partial draft",
  payload: null,
};
const INFO_NOTE = {
  id: "crit-3",
  reviewer: "pacing",
  severity: "info",
  note: "middle drags slightly",
  payload: null,
};

const SCENE = {
  id: "s1",
  chapter_id: "ch-1",
  scene_no: 2,
  version: 2,
  status: "pending_review",
  prose: "Mara vaulted the breach and kept moving.",
  agent_original: "Mara vaulted the breach.",
  prose_source: "agent",
  passes_run: ["drafter"],
  model: "test-model",
  word_count: 7,
  is_exemplar: false,
  critiques: [CONFLICT, LEGACY_NOTE, INFO_NOTE],
};

function baseData() {
  return {
    pending: [SCENE],
    detail: SCENE as typeof SCENE | null,
    missingSceneId: null as string | null,
    loadingScene: false,
    openSceneById: vi.fn(),
    chapters: [{ id: "ch-1", chapter_no: 1, title: "Signal Fire", pov: "Mara", status: "planned" }],
    characters: [],
    annotations: [],
    suggestions: [],
    versions: [],
    activeBeat: null,
    jobs: {
      running: 0,
      queued: 0,
      failed: 0,
      queue_paused: false,
      active_scene: null as { chapter_no: number; scene_no: number } | null,
    },
    decide: vi.fn().mockResolvedValue(undefined),
    resolveContinuity: vi.fn().mockResolvedValue(undefined),
    restartRedraft: vi.fn().mockResolvedValue(undefined),
    addAnnotation: vi.fn(),
    deleteAnnotation: vi.fn(),
    addSuggestion: vi.fn(),
    decideSuggestion: vi.fn(),
    deleteSuggestion: vi.fn(),
    setExemplar: vi.fn(),
  };
}
let mockData = baseData();
vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

describe("SceneScreen", () => {
  beforeEach(() => {
    mockParams = {};
    mockDesk.tab = "continuity";
    mockDesk.mode = "reading";
    mockData = baseData();
    routerPush.mockReset();
    routerReplace.mockReset();
    // localStorage is unavailable in this vitest environment (SecurityError on access); the screen
    // guards every use with try/catch, so tests simply run without draft persistence.
  });

  it("deep-links to this scene's packet with the ?chapter&scene convention", async () => {
    render(<SceneScreen />);
    fireEvent.click(await screen.findByText("Scene packet →"));
    expect(routerPush).toHaveBeenCalledWith("/packets?chapter=ch-1&scene=2");
  });

  it("tones continuity conflicts as repair work, not block-red, and counts them on the tab", async () => {
    render(<SceneScreen />);
    // The uppercase attribute label on the conflict card carries the surface tone.
    const label = await screen.findByText("level");
    expect(label.getAttribute("style")).toContain("--warn");
    expect(label.getAttribute("style")).not.toContain("--bad");
    // Continuity tab badge shows the conflict count.
    const tab = screen.getByRole("button", { name: /Continuity/ });
    expect(tab.textContent).toContain("1");
  });

  it("resolves a conflict with the chosen source", async () => {
    render(<SceneScreen />);
    fireEvent.click(await screen.findByText("Keep prose · fix ledger"));
    await waitFor(() =>
      expect(mockData.resolveContinuity).toHaveBeenCalledWith("s1", {
        critique_id: "crit-1",
        choice: "use_prose",
      }),
    );
  });

  it("links a conflict to its character in the Ledger", async () => {
    render(<SceneScreen />);
    fireEvent.click(await screen.findByText("View Mara in ledger →"));
    expect(routerPush).toHaveBeenCalledWith("/ledger?cat=characters&focus=Mara");
  });

  it("labels note severities with the unified vocabulary — never the retired 'hard'", async () => {
    mockDesk.tab = "notes";
    render(<SceneScreen />);
    expect(await screen.findByText("block")).toBeInTheDocument();
    expect(screen.getByText("info")).toBeInTheDocument();
    expect(screen.queryByText("hard")).not.toBeInTheDocument();
  });

  it("explains Restart while the queue is paused instead of lying about immediacy", async () => {
    mockParams = { sceneId: "s1" };
    mockData.detail = { ...SCENE, status: "revision_requested" };
    mockData.jobs = { ...mockData.jobs, queue_paused: true };
    render(<SceneScreen />);

    const restart = await screen.findByRole("button", { name: "Restart" });
    expect(restart).not.toBeDisabled();
    expect(restart.getAttribute("title")).toContain("Queue is paused");
    expect(screen.getByText(/restarted job waits until you resume/)).toBeInTheDocument();
  });

  it("disables Restart while this exact scene is already drafting", async () => {
    mockParams = { sceneId: "s1" };
    mockData.detail = { ...SCENE, status: "revision_requested" };
    mockData.jobs = { ...mockData.jobs, active_scene: { chapter_no: 1, scene_no: 2 } };
    render(<SceneScreen />);

    const restart = await screen.findByRole("button", { name: "Restart" });
    expect(restart).toBeDisabled();
    expect(restart.getAttribute("title")).toContain("Already drafting");
  });

  it("approves with the reviewer's edits (none here) via data.decide", async () => {
    mockDesk.rawProse = SCENE.prose;
    render(<SceneScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /Approve/ }));
    await waitFor(() =>
      expect(mockData.decide).toHaveBeenCalledWith("s1", {
        decision: "approve",
        edited_prose: null,
      }),
    );
  });

  it("requests a revision carrying the feedback", async () => {
    mockDesk.rawProse = SCENE.prose;
    mockDesk.feedback = "Tighten the middle beat.";
    render(<SceneScreen />);
    fireEvent.click(await screen.findByRole("button", { name: /Request revision/ }));
    await waitFor(() =>
      expect(mockData.decide).toHaveBeenCalledWith("s1", {
        decision: "revise",
        feedback: "Tighten the middle beat.",
        edited_prose: null,
      }),
    );
  });

  it("renders the honest empty state when the review queue is empty", () => {
    mockData.pending = [];
    mockData.detail = null;
    render(<SceneScreen />);
    expect(screen.getByText("Nothing to review")).toBeInTheDocument();
  });

  it("renders the deleted-scene state for a focused missing scene", () => {
    mockParams = { sceneId: "gone" };
    mockData.detail = null;
    mockData.missingSceneId = "gone";
    render(<SceneScreen />);
    expect(screen.getByText("Scene deleted or unavailable")).toBeInTheDocument();
  });
});
