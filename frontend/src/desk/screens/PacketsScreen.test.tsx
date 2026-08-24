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
    chapters: {
      chapter_no: number;
      title: string | null;
      pov: string;
      scenes: { scene_no: number; prose: string | null }[];
    }[];
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
    packetAuthority: vi.fn(),
    packetStatus: vi.fn(),
    proposePacket: vi.fn(),
    updatePacket: vi.fn(),
    approvePacket: vi.fn(),
    deletePacket: vi.fn(),
    // Amendment mode (#261) — the eligibility preflight is fetched on every chapter open.
    amendmentEligibility: vi.fn(),
    startAmendment: vi.fn(),
    approveAmendment: vi.fn(),
    // Called by ScenePacketsPanel, which mounts once a packet is approved.
    scenePackets: vi.fn(),
    deriveStatus: vi.fn(),
    draftReadiness: vi.fn(),
    chapterTelemetry: vi.fn(),
  },
}));

// jsdom has no URL.createObjectURL — assert the download call instead of a real browser download.
vi.mock("../lib/download", () => ({
  downloadBlob: vi.fn(),
  copyToClipboard: vi.fn(),
}));

import { api } from "../api/client";
import { downloadBlob } from "../lib/download";

// A structurally valid proposed packet whose only findings are repair/warn — the backend says it is
// approvable (approve-with-repairs), so the UI must offer approval and label the outstanding repairs.
const REPAIR_PACKET = {
  id: "p1",
  book_id: "b1",
  chapter_id: "c1",
  status: "proposed",
  confidence: "yellow",
  qa_verdict: "approve_with_warnings",
  body: { one_sentence_spine: "Soren chooses the fire over the flood." },
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
      { kind: "tone_drift", detail: "voice risk", severity: "warn" },
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
  approval_state: "approvable",
  approval_blockers: [],
};

// The amendment preflight rides along on every chapter open. Default it to "no verdict" so the suites
// that are not about amendment mode render exactly as they did before it existed.
beforeEach(() => {
  vi.mocked(api.amendmentEligibility).mockReset().mockRejectedValue(new Error("404"));
  vi.mocked(api.packetAuthority).mockReset().mockRejectedValue(new Error("404"));
  vi.mocked(api.startAmendment).mockReset();
  vi.mocked(api.approveAmendment).mockReset();
});

describe("PacketsScreen legacy question recovery", () => {
  const LEGACY_QUESTION_PACKET = {
    ...REPAIR_PACKET,
    open_questions: {
      items: [{ text: "Who authorized the crossing?", legacy: true }],
      resolved: [],
    },
    open_questions_token: "legacy-open-questions-token",
    can_approve: false,
    approval_state: "open_questions",
    approval_blockers: ["resolve the packet's open questions first"],
  };

  beforeEach(() => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(LEGACY_QUESTION_PACKET);
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(api.updatePacket).mockReset().mockResolvedValue(LEGACY_QUESTION_PACKET);
  });

  it("prepares all legacy questions without submitting their text from the client", async () => {
    render(<PacketsScreen />);

    fireEvent.click(await screen.findByRole("button", { name: "Prepare historical questions" }));

    await waitFor(() =>
      expect(api.updatePacket).toHaveBeenCalledWith("c1", {
        prepare_legacy_open_questions: true,
        expected_open_questions_token: "legacy-open-questions-token",
      }),
    );
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });
});

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
        {
          chapter_no: 1,
          title: "The Start",
          pov: "Soren",
          scenes: [{ scene_no: 1, prose: "Text." }],
        },
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
      chapters: [
        {
          chapter_no: 1,
          title: "The Start",
          pov: "Soren",
          scenes: [{ scene_no: 1, prose: "   " }],
        },
      ],
    };
    render(<PacketsScreen />);
    expect(await screen.findByRole("button", { name: "Export Markdown" })).toBeDisabled();
  });
});

describe("PacketsScreen approve with repairs", () => {
  beforeEach(() => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(REPAIR_PACKET);
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(api.approvePacket)
      .mockReset()
      .mockResolvedValue({ ...REPAIR_PACKET, status: "approved" });
    // Approval mounts ScenePacketsPanel — give its mount-time fetches quiet defaults.
    vi.mocked(api.scenePackets).mockReset().mockResolvedValue([]);
    vi.mocked(api.deriveStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(api.draftReadiness).mockReset().mockResolvedValue({
      chapter_id: "c1",
      chapter_packet_approved: true,
      scene_packets: {},
      beats: {},
      jobs: {},
      draftable: false,
      disabled_reason: "No scene packets derived yet — derive scene packets first.",
      blockers: [],
      scene_packets_stale: 0,
      scene_packet_qa_blocking: 0,
      active_draft_jobs: 0,
      missing_scene_drafts: [],
      structural_blockers: [],
      provider_rate_limited: false,
      can_draft: false,
    });
    vi.mocked(api.chapterTelemetry).mockReset().mockRejectedValue(new Error("404"));
  });

  it("keeps approval available for a repair-laden packet and labels the outstanding repairs", async () => {
    render(<PacketsScreen />);

    // 2 repair-severity findings (1 violation + 1 issue); the warn issue is advisory, not a repair.
    const approve = await screen.findByRole("button", {
      name: "Approve (2 repair tasks outstanding)",
    });
    expect(approve).not.toBeDisabled();
    expect(screen.getByText(/repair tasks gate final export, not drafting/)).toBeInTheDocument();

    fireEvent.click(approve);
    await waitFor(() => expect(api.approvePacket).toHaveBeenCalledWith("c1"));
  });

  it("marks the repair-tier violation as export-gating in the validation panel", async () => {
    render(<PacketsScreen />);
    await screen.findByText(/Deterministic validation/);
    expect(screen.getByText(/blocks final export only/)).toBeInTheDocument();
  });

  it("a disabled Approve always shows a reason and a hover title — never a silent grey", async () => {
    // Server-guaranteed blockers render verbatim; the keyed fallback covers legacy payloads whose
    // blockers arrive empty (the exact case that used to grey the button with no explanation).
    vi.mocked(api.packet).mockResolvedValue({
      ...REPAIR_PACKET,
      can_approve: false,
      approval_state: "open_questions",
      approval_blockers: ["resolve the packet's open questions first"],
    });
    render(<PacketsScreen />);

    const approve = await screen.findByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute("title", "resolve the packet's open questions first");
    expect(screen.getByText("resolve the packet's open questions first")).toBeInTheDocument();
  });

  it("falls back to a keyed reason when a legacy payload ships no blockers", async () => {
    vi.mocked(api.packet).mockResolvedValue({
      ...REPAIR_PACKET,
      can_approve: false,
      approval_state: "approvable", // pre-approval_state serializations default here
      approval_blockers: [],
    });
    render(<PacketsScreen />);

    const approve = await screen.findByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    expect(screen.getByText(/not approvable right now/)).toBeInTheDocument();
  });
});

// Amendment mode (#261) wired end-to-end at the screen: eligibility fetched on chapter open, the
// authority-split banner, the amendment-only approve route, and the fail-closed 409 recovery. Mocked at
// the `api` boundary — no network.
describe("PacketsScreen amendment mode", () => {
  const PROPOSED_AMENDMENT = {
    ...REPAIR_PACKET,
    id: "amend-1",
    status: "proposed",
    origin_mode: "amendment",
    supersedes_packet_id: "pred-0000-0000",
    source_fingerprint: "aaaaaaaaaaaaaaaabbbb",
    qa_warnings: null,
  };
  const APPROVED_AMENDMENT = {
    ...PROPOSED_AMENDMENT,
    status: "approved",
    approved_at: "2026-07-03T09:00:00Z",
    approval_source: "manual_command",
    amendment_scope: {
      predecessor_packet_id: "pred-0000-0000",
      staled_scene_packet_ids: ["scenepkt-1", "scenepkt-2"],
      superseded_at: "2026-07-03T09:00:00Z",
    },
  };
  const ALREADY_OPEN = {
    chapter_id: "c1",
    reason: "amendment_already_open",
    eligible: false,
    message: "An amendment for this chapter is already open and awaiting review.",
    approved_packet_id: "pred-0000-0000",
    open_amendment_packet_id: "amend-1",
    unseeded_scene_ids: ["scene-9"],
    seeded_scene_ids: [],
    source_fingerprint: "aaaaaaaaaaaaaaaabbbb",
  };
  const ELIGIBLE = {
    ...ALREADY_OPEN,
    reason: "unseeded_scenes_present",
    eligible: true,
    message: null,
    open_amendment_packet_id: null,
  };

  beforeEach(() => {
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    // Approving flips the packet to approved, which mounts ScenePacketsPanel — quiet defaults for it.
    vi.mocked(api.scenePackets).mockReset().mockResolvedValue([]);
    vi.mocked(api.deriveStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(api.draftReadiness).mockReset().mockRejectedValue(new Error("404"));
    vi.mocked(api.chapterTelemetry).mockReset().mockRejectedValue(new Error("404"));
  });

  it("fetches eligibility on chapter open and offers Start amendment when eligible", async () => {
    vi.mocked(api.packet).mockReset().mockRejectedValue(new Error("404"));
    vi.mocked(api.amendmentEligibility).mockReset().mockResolvedValue(ELIGIBLE);
    vi.mocked(api.startAmendment).mockResolvedValue({
      id: "adopt-1234-5678",
      book_id: "b1",
      chapter_id: "c1",
      mode: "amendment",
      status: "queued",
      created_at: "2026-07-03T09:00:00Z",
      updated_at: "2026-07-03T09:00:00Z",
    });

    render(<PacketsScreen />);
    await waitFor(() => expect(api.amendmentEligibility).toHaveBeenCalledWith("c1"));

    const start = await screen.findByRole("button", { name: "Start amendment" });
    expect(start).not.toBeDisabled();
    fireEvent.click(start);

    await waitFor(() => expect(api.startAmendment).toHaveBeenCalledWith("c1"));
    const notice = await screen.findByTestId("amendment-notice");
    expect(notice.textContent).toMatch(/adopt-12/);
    expect(notice.textContent).toMatch(/queued/);
    expect(notice.textContent).toMatch(/does not poll/i);
  });

  it("banners the authority split and blocks the ordinary Approve for a proposed amendment", async () => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(PROPOSED_AMENDMENT);
    vi.mocked(api.amendmentEligibility).mockReset().mockResolvedValue(ALREADY_OPEN);

    render(<PacketsScreen />);
    const banner = await screen.findByTestId("amendment-authority-banner");
    expect(banner.textContent).toMatch(/still governed by its predecessor/i);
    expect(banner.textContent).toContain("pred-000");

    // The ordinary approve route refuses an amendment (409 amendment_requires_amendment_approval), so
    // the Desk must not offer it — and must say where to go instead.
    const ordinary = screen.getByRole("button", { name: "Approve packet" });
    expect(ordinary).toBeDisabled();
    expect(ordinary.getAttribute("title")).toMatch(/Approve amendment/);
    expect(screen.getByText(/newest packet is a proposed amendment/i)).toBeInTheDocument();
  });

  it("approves through the amendment route and shows what it invalidated", async () => {
    vi.mocked(api.packet)
      .mockReset()
      .mockResolvedValueOnce(PROPOSED_AMENDMENT)
      .mockResolvedValue(APPROVED_AMENDMENT);
    vi.mocked(api.amendmentEligibility).mockReset().mockResolvedValue(ALREADY_OPEN);
    vi.mocked(api.approveAmendment).mockResolvedValue(APPROVED_AMENDMENT);

    render(<PacketsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve amendment" }));

    await waitFor(() => expect(api.approveAmendment).toHaveBeenCalledWith("c1", "amend-1"));
    await waitFor(() =>
      expect(screen.queryByTestId("amendment-authority-banner")).not.toBeInTheDocument(),
    );
    const panel = screen.getByTestId("amendment-panel");
    expect(panel.textContent).toMatch(/Scene contracts staled by this amendment · 2/);
    expect(panel.textContent).toContain("scenepkt");
  });

  it("renders the drift 409 as 'nothing was changed' plus the real fix", async () => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(PROPOSED_AMENDMENT);
    vi.mocked(api.amendmentEligibility).mockReset().mockResolvedValue(ALREADY_OPEN);
    // The shape `http()` throws for a 409: FastAPI wraps the payload one level down in `detail`.
    vi.mocked(api.approveAmendment).mockRejectedValue({
      status: 409,
      statusText: "Conflict",
      message: "409 Conflict",
      data: {
        detail: {
          reason: "amendment_source_drifted",
          message:
            "The chapter's prose changed after this amendment was authored, so NOTHING was changed " +
            "— the approved contract and every scene packet are exactly as they were. Re-run the " +
            "amendment against the current prose, then approve that one.",
          expected: "aaaaaaaaaaaaaaaabbbb",
          actual: "9999999999999999eeee",
        },
      },
    });

    render(<PacketsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Approve amendment" }));

    const recovery = await screen.findByTestId("amendment-recovery");
    expect(recovery.textContent).toMatch(/NOTHING was changed/);
    expect(recovery.textContent).toMatch(/exactly as they were/);
    expect(recovery.textContent).toMatch(/not a partial write, so there is nothing to clean up/i);
    expect(recovery.textContent).toMatch(/Re-run the amendment against the current prose/i);
    expect(screen.getByTestId("amendment-recovery-reason").textContent).toBe(
      "amendment_source_drifted",
    );
    // Refused, so the proposal is still under review and the banner still stands.
    expect(screen.getByTestId("amendment-authority-banner")).toBeInTheDocument();
  });

  it("shows no amendment panel for an ordinary packet with no verdict", async () => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(REPAIR_PACKET);
    vi.mocked(api.amendmentEligibility).mockReset().mockRejectedValue(new Error("404"));

    render(<PacketsScreen />);
    await screen.findByRole("button", { name: /Approve \(2 repair tasks outstanding\)/ });
    expect(screen.queryByTestId("amendment-panel")).not.toBeInTheDocument();
  });
});

describe("PacketsScreen raw packet JSON", () => {
  beforeEach(() => {
    vi.mocked(api.packet).mockReset().mockResolvedValue(REPAIR_PACKET);
    vi.mocked(api.packetStatus).mockReset().mockResolvedValue({ running: false });
    vi.mocked(downloadBlob).mockReset();
  });

  it("toggles a pretty-printed canonical JSON view of the packet body", async () => {
    render(<PacketsScreen />);
    expect(screen.queryByTestId("packet-json")).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Packet JSON" }));
    const pre = await screen.findByTestId("packet-json");
    expect(pre.textContent).toContain('"one_sentence_spine"');
    expect(pre.textContent).toContain("Soren chooses the fire over the flood.");

    fireEvent.click(screen.getByRole("button", { name: "Hide JSON" }));
    expect(screen.queryByTestId("packet-json")).not.toBeInTheDocument();
  });

  it("downloads the packet body as chapter_<no>_packet.json", async () => {
    render(<PacketsScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Packet JSON" }));
    fireEvent.click(await screen.findByRole("button", { name: "Download JSON" }));

    expect(downloadBlob).toHaveBeenCalledTimes(1);
    const [filename, content, mime] = vi.mocked(downloadBlob).mock.calls[0];
    expect(filename).toBe("chapter_1_packet.json");
    expect(JSON.parse(content)).toEqual(REPAIR_PACKET.body);
    expect(mime).toBe("application/json");
  });
});
