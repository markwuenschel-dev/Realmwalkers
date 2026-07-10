import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SceneFidelityRequirements from "./SceneFidelityRequirements";

const mocks = vi.hoisted(() => ({
  scenePacketFidelity: vi.fn(),
  acceptFidelitySuggestions: vi.fn(),
  refineFidelityRequirement: vi.fn(),
  replaceFidelityRequirement: vi.fn(),
}));
vi.mock("../api/client", () => ({ api: mocks }));

const ACTIVE = {
  requirement_id: "req-a",
  mode: "relationship_turn",
  post_draft_policy: "export_required",
  clauses: [{ clause_id: "c1" }],
};
const SUGGESTED = {
  requirement_id: "sug-b",
  mode: "combat_blocking",
  post_draft_policy: "advisory",
  clauses: [{ clause_id: "c2" }, { clause_id: "c3" }],
};
const FIDELITY = {
  scene_packet_id: "p1",
  active_requirements: [ACTIVE],
  suggested_requirements: [SUGGESTED],
  fingerprint: "fp",
  violations: [],
};

describe("SceneFidelityRequirements", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.scenePacketFidelity.mockResolvedValue(FIDELITY);
    mocks.acceptFidelitySuggestions.mockResolvedValue({ ...FIDELITY, suggested_requirements: [] });
    mocks.refineFidelityRequirement.mockResolvedValue(FIDELITY);
    mocks.replaceFidelityRequirement.mockResolvedValue(FIDELITY);
  });

  it("renders nothing when the packet has no fidelity contract", () => {
    mocks.scenePacketFidelity.mockResolvedValue({
      scene_packet_id: "p1",
      active_requirements: [],
      suggested_requirements: [],
      fingerprint: "fp",
      violations: [],
    });
    const { container } = render(<SceneFidelityRequirements packetId="p1" />);
    expect(container.querySelector('[data-testid="fidelity-requirements"]')).toBeNull();
  });

  it("renders active and suggested requirements", async () => {
    render(<SceneFidelityRequirements packetId="p1" />);
    expect(await screen.findByText("Active requirements")).toBeInTheDocument();
    expect(screen.getByText("Suggested (not active)")).toBeInTheDocument();
    expect(screen.getByText("relationship_turn")).toBeInTheDocument();
    expect(screen.getByText("combat_blocking")).toBeInTheDocument();
    // The export-required active requirement is flagged distinctly.
    expect(screen.getByText("export-required")).toBeInTheDocument();
  });

  it("accepts a single suggestion by its requirement id", async () => {
    render(<SceneFidelityRequirements packetId="p1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    expect(mocks.acceptFidelitySuggestions).toHaveBeenCalledWith("p1", {
      requirement_ids: ["sug-b"],
    });
    // The response drops the suggestion, so the suggested section clears (also flushes the state update).
    await waitFor(() => expect(screen.queryByText("Suggested (not active)")).toBeNull());
  });

  it("refines an active requirement with the edited JSON body", async () => {
    render(<SceneFidelityRequirements packetId="p1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Refine" }));
    const edited = { ...ACTIVE, statement_note: "tightened" };
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: JSON.stringify(edited) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save refinement" }));
    expect(mocks.refineFidelityRequirement).toHaveBeenCalledWith("p1", {
      requirement_id: "req-a",
      requirement: edited,
    });
    // On success the editor closes (also flushes the post-response state update).
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  });

  it("rejects invalid JSON on refine without calling the API", async () => {
    render(<SceneFidelityRequirements packetId="p1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Refine" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "{ not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Save refinement" }));
    expect(mocks.refineFidelityRequirement).not.toHaveBeenCalled();
    expect(screen.getByText("Requirement is not valid JSON.")).toBeInTheDocument();
  });
});
