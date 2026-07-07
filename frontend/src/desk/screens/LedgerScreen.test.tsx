import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LedgerScreen from "./LedgerScreen";

// Workstream H: the stale-canon cleanup UI. These assert the screen wires the new client methods and,
// critically, that a destructive action always dry-runs `cleanup-preview` BEFORE the real retire/delete.

let mockSearch = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearch,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// Pin the Ledger to a canon kind so the cleanup filters/rows/BulkBar actions render; deep-link tests
// repoint mockLedgerCat/mockSearch before render.
const setLedgerCat = vi.fn();
let mockLedgerCat = "canon:location";
vi.mock("../state", () => ({
  useDesk: () => ({
    t: { accent: "#000", bad: "#f00", warn: "#e90", good: "#0a0", info: "#09f", dim: "#888" },
    ledgerCat: mockLedgerCat,
    selectedThread: "",
    setLedgerCat,
    selectThread: vi.fn(),
  }),
}));

const mockData = {
  bookId: "b1",
  canon: [],
  characters: [] as { character: string; stats: Record<string, unknown> }[],
  threads: [],
  ruleProposals: [],
  runBulk: vi.fn(),
  createCanon: vi.fn(),
  updateCanon: vi.fn(),
  deleteCanon: vi.fn().mockResolvedValue(undefined),
  upsertCharacter: vi.fn(),
  deleteCharacter: vi.fn(),
  createThread: vi.fn(),
  addThreadBeat: vi.fn(),
  deleteThread: vi.fn(),
  distillRules: vi.fn(),
  decideRuleProposal: vi.fn(),
};

vi.mock("../api/data", () => ({
  useDeskData: () => mockData,
}));

vi.mock("../api/client", () => ({
  api: {
    listCanon: vi.fn(),
    canonCleanupPreview: vi.fn(),
    retireCanon: vi.fn(),
    bulkDeleteCanon: vi.fn(),
    rebuildCanon: vi.fn(),
    deleteCanon: vi.fn(),
    deleteCharacter: vi.fn(),
    deleteThread: vi.fn(),
  },
}));

import { api } from "../api/client";

const ROW = {
  id: "e1",
  kind: "location",
  name: "Old Keep",
  body: "A ruined keep on the ridge.",
  source: "repo_ingested",
  status: "active",
  // Provenance + embedding lifecycle (Desk Control Round, Phase 9).
  doc_path: "series/canon/locations.md",
  heading_path: "Keeps › Old Keep",
  owner_topic: "locations",
  source_priority: 2,
  embedding_version: "voyage-3",
  embedding_stale: false,
};

const PREVIEW = {
  dry_run: true,
  matched: 1,
  would_retire: 1,
  would_delete: 1,
  protected_manual: 0,
  items: [
    {
      id: "e1",
      kind: "location",
      name: "Old Keep",
      source: "repo_ingested",
      status: "active",
      summary: "A ruined keep on the ridge.",
      reason: "eligible",
    },
  ],
};

describe("LedgerScreen canon cleanup", () => {
  beforeEach(() => {
    vi.mocked(api.listCanon).mockReset().mockResolvedValue([ROW]);
    vi.mocked(api.canonCleanupPreview).mockReset().mockResolvedValue(PREVIEW);
    vi.mocked(api.retireCanon).mockReset().mockResolvedValue({ retired: 1, protected_manual: 0 });
    vi.mocked(api.bulkDeleteCanon)
      .mockReset()
      .mockResolvedValue({ deleted: 1, protected_manual: 0 });
    vi.mocked(api.rebuildCanon).mockReset().mockResolvedValue({ status: "started" });
  });

  it("loads canon via listCanon and shows a source + status badge per row", async () => {
    render(<LedgerScreen />);
    const name = await screen.findByText("Old Keep");
    expect(api.listCanon).toHaveBeenCalledWith("b1", { status: "active", source: "all" });
    // Badges live in the row header next to the name (scoped so the filter <option>s don't collide).
    const header = within(name.parentElement as HTMLElement);
    expect(header.getByText("repo_ingested")).toBeInTheDocument();
    expect(header.getByText("active")).toBeInTheDocument();
  });

  it("renders the provenance line (doc › heading, full value in the tooltip) and owner/prio chips", async () => {
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");
    const prov = screen.getByText("series/canon/locations.md › Keeps › Old Keep");
    expect(prov).toHaveAttribute("title", "series/canon/locations.md › Keeps › Old Keep");
    expect(screen.getByText("locations")).toBeInTheDocument();
    expect(screen.getByText("prio 2")).toBeInTheDocument();
  });

  it("shows the embedding-stale chip only when the row's embedding is stale", async () => {
    const fresh = render(<LedgerScreen />);
    await screen.findByText("Old Keep");
    expect(screen.queryByText("embedding stale")).not.toBeInTheDocument();
    fresh.unmount();

    vi.mocked(api.listCanon).mockResolvedValue([{ ...ROW, embedding_stale: true }]);
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");
    const chip = screen.getByText("embedding stale");
    expect(chip).toHaveAttribute("title", expect.stringContaining("re-embed"));
  });

  it("offers superseded in the status filter, before all", async () => {
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");
    const select = screen.getByLabelText("status filter");
    const options = within(select)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["active", "stale", "retired", "superseded", "all"]);
  });

  it("previews before retiring: cleanup-preview fires before retireCanon", async () => {
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(await screen.findByText("Retire selected"));

    await waitFor(() =>
      expect(api.canonCleanupPreview).toHaveBeenCalledWith("b1", { ids: ["e1"], dry_run: true }),
    );
    expect(api.retireCanon).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: /Retire 1 row/ }));

    await waitFor(() =>
      expect(api.retireCanon).toHaveBeenCalledWith("b1", { ids: ["e1"], dry_run: false }),
    );
    // Preview strictly precedes the real mutation.
    expect(vi.mocked(api.canonCleanupPreview).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.retireCanon).mock.invocationCallOrder[0],
    );
  });

  it("previews before deleting: cleanup-preview fires before bulkDeleteCanon", async () => {
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(await screen.findByText("Delete selected"));

    await waitFor(() => expect(api.canonCleanupPreview).toHaveBeenCalledTimes(1));
    expect(api.bulkDeleteCanon).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: /Delete 1 row/ }));

    await waitFor(() =>
      expect(api.bulkDeleteCanon).toHaveBeenCalledWith("b1", { ids: ["e1"], dry_run: false }),
    );
    expect(vi.mocked(api.canonCleanupPreview).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.bulkDeleteCanon).mock.invocationCallOrder[0],
    );
  });

  it("clean rebuild button calls rebuildCanon", async () => {
    render(<LedgerScreen />);
    await screen.findByText("Old Keep");
    fireEvent.click(screen.getByText(/Clean rebuild from docs/));
    await waitFor(() => expect(api.rebuildCanon).toHaveBeenCalledWith("b1"));
  });
});

describe("LedgerScreen deep links (?cat & ?focus)", () => {
  beforeEach(() => {
    vi.mocked(api.listCanon).mockReset().mockResolvedValue([ROW]);
    setLedgerCat.mockReset();
    mockSearch = new URLSearchParams();
    mockLedgerCat = "canon:location";
    mockData.characters = [];
  });

  it("consumes ?cat= on mount and selects that category", async () => {
    mockSearch = new URLSearchParams("cat=characters");
    render(<LedgerScreen />);
    await waitFor(() => expect(setLedgerCat).toHaveBeenCalledWith("characters"));
  });

  it("highlights the ?focus= character panel — the target of a Scene continuity link", async () => {
    mockSearch = new URLSearchParams("cat=characters&focus=Mara");
    mockLedgerCat = "characters";
    mockData.characters = [
      { character: "Mara", stats: { level: 15 }, is_pov: true, provisional: false, body: null },
      { character: "Seb", stats: {}, is_pov: false, provisional: false, body: null },
    ] as never;
    render(<LedgerScreen />);
    await screen.findByText("Mara");

    // The highlight lives on the Panel's own border (every card's avatar square also uses
    // --accentLine, so assert on the <section> style, not innerHTML).
    const focusedPanel = document.getElementById("ledger-char-Mara")?.querySelector("section");
    const otherPanel = document.getElementById("ledger-char-Seb")?.querySelector("section");
    expect(focusedPanel?.getAttribute("style")).toContain("--accentLine");
    expect(otherPanel?.getAttribute("style")).not.toContain("--accentLine");
  });
});
