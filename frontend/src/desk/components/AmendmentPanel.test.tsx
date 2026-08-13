import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AmendmentPanel, { eligibilityGateRows, isProposedAmendment } from "./AmendmentPanel";
import type { AmendmentEligibilityOut, PacketOut } from "../api/types";

// Amendment mode (#261). The point of this panel is that `GET .../packet` resolves by RECENCY with no
// status filter, so a proposed amendment is what the Desk shows while the APPROVED PREDECESSOR is still
// the chapter's governing authority. These tests pin that distinction, the reason-token -> UI mapping,
// and the recovery text for the fail-closed refusals — the three things an author would be misled by.

const BASE_PACKET: PacketOut = {
  id: "11111111-1111-1111-1111-111111111111",
  book_id: "b1",
  chapter_id: "c1",
  status: "proposed",
  body: { one_sentence_spine: "Soren chooses the fire over the flood." },
  qa_warnings: null,
  open_questions: null,
  created_at: "2026-07-02T10:00:00Z",
  can_approve: true,
  approval_state: "approvable",
  approval_blockers: [],
};

/** The newest packet is a PROPOSED amendment: it names a predecessor that still governs. */
const PROPOSED_AMENDMENT: PacketOut = {
  ...BASE_PACKET,
  status: "proposed",
  origin_mode: "amendment",
  supersedes_packet_id: "22222222-2222-2222-2222-222222222222",
  source_fingerprint: "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
  evidence_manifest_fingerprint: "ccccccccccccccccdddddddddddddddd",
};

/** An ordinary initial contract, approved. No amendment lineage, so no authority split to warn about. */
const INITIAL_APPROVED: PacketOut = {
  ...BASE_PACKET,
  status: "approved",
  origin_mode: "initial",
  approval_source: "manual_command",
  approved_at: "2026-07-02T11:00:00Z",
  source_fingerprint: "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
};

/** After the locked transition: authority moved, and `amendment_scope` records what it invalidated. */
const APPROVED_AMENDMENT: PacketOut = {
  ...PROPOSED_AMENDMENT,
  status: "approved",
  approval_source: "manual_command",
  approved_at: "2026-07-03T09:00:00Z",
  amendment_scope: {
    predecessor_packet_id: "22222222-2222-2222-2222-222222222222",
    staled_scene_packet_ids: [
      "33333333-3333-3333-3333-333333333333",
      "44444444-4444-4444-4444-444444444444",
    ],
    superseded_at: "2026-07-03T09:00:00Z",
  },
};

const eligibility = (over: Partial<AmendmentEligibilityOut> = {}): AmendmentEligibilityOut => ({
  chapter_id: "c1",
  reason: "unseeded_scenes_present",
  eligible: true,
  message: null,
  approved_packet_id: "22222222-2222-2222-2222-222222222222",
  open_amendment_packet_id: null,
  unseeded_scene_ids: ["55555555-5555-5555-5555-555555555555"],
  seeded_scene_ids: ["66666666-6666-6666-6666-666666666666"],
  source_fingerprint: "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
  ...over,
});

/** An `ApiError`-shaped 409 as `http()` throws it — duck-typed, exactly what `conflictDetail` reads. */
const conflict = (detail: Record<string, unknown>) => ({
  status: 409,
  statusText: "Conflict",
  data: { detail },
  message: "409 Conflict",
});

// The server's own sentences, verbatim from `workers/packet/amendment.REFUSAL_MESSAGES` and
// `api/routers/packets.py` — the panel must show these, not paraphrase them.
const DRIFT_MESSAGE =
  "The chapter's prose changed after this amendment was authored, so NOTHING was changed — the " +
  "approved contract and every scene packet are exactly as they were. Re-run the amendment against " +
  "the current prose, then approve that one.";

describe("AmendmentPanel authority split", () => {
  it("warns that a proposed amendment is not the live contract, and names the predecessor", () => {
    render(<AmendmentPanel packet={PROPOSED_AMENDMENT} eligibility={null} />);

    const banner = screen.getByTestId("amendment-authority-banner");
    expect(banner.textContent).toMatch(/proposed amendment under review/i);
    expect(banner.textContent).toMatch(/still governed by its predecessor/i);
    // The predecessor is identified by the id the PROPOSAL carries — short-hashed for scanning.
    expect(banner.textContent).toContain("22222222");
    expect(banner.textContent).toMatch(/supersede that predecessor/i);
    expect(banner.textContent).toMatch(/stale/i);
  });

  it("says out loud that no endpoint returns the active authority", () => {
    render(<AmendmentPanel packet={PROPOSED_AMENDMENT} eligibility={null} />);
    expect(screen.getByTestId("amendment-authority-banner").textContent).toMatch(
      /no endpoint returns "the active authority"/i,
    );
  });

  it("shows NO banner for an initial approved packet", () => {
    render(<AmendmentPanel packet={INITIAL_APPROVED} eligibility={eligibility()} />);
    expect(screen.queryByTestId("amendment-authority-banner")).not.toBeInTheDocument();
    expect(isProposedAmendment(INITIAL_APPROVED)).toBe(false);
  });

  it("shows NO banner once the amendment itself is approved — it IS the authority then", () => {
    render(<AmendmentPanel packet={APPROVED_AMENDMENT} eligibility={null} />);
    expect(screen.queryByTestId("amendment-authority-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("amendment-panel").textContent).toMatch(/what this amendment did/i);
  });

  it("flags a proposal that names no predecessor instead of implying approval will work", () => {
    render(
      <AmendmentPanel
        packet={{ ...PROPOSED_AMENDMENT, supersedes_packet_id: null }}
        eligibility={null}
      />,
    );
    expect(screen.getByTestId("amendment-authority-banner").textContent).toMatch(
      /amendment_predecessor_missing/,
    );
  });
});

describe("AmendmentPanel eligibility reasons", () => {
  // Every refusal token in the closed set must render the server's sentence — the author's next action
  // differs per token, so a generic "not eligible" would be useless.
  const REFUSALS: [string, string][] = [
    [
      "no_approved_packet",
      "This chapter has no approved contract, so there is nothing to amend. Adopt an initial contract first (Start contract adoption) — amendment is copy-on-write FROM an approved packet.",
    ],
    [
      "no_imported_scenes",
      "This chapter has no imported prose, so no scene can be missing a seed. Amendment repairs imported prose that the approved contract does not cover.",
    ],
    [
      "all_scenes_seeded",
      "Every scene in this chapter already resolves to a seed in the approved contract, so amendment is not the right repair. If a scene's contract is out of date, re-derive its scene packet.",
    ],
    [
      "amendment_already_open",
      "An amendment for this chapter is already open and awaiting review. Review or discard that one rather than opening a second — a chapter may only have one amendment in flight.",
    ],
  ];

  for (const [reason, message] of REFUSALS) {
    it(`renders the ${reason} refusal message and token`, () => {
      render(
        <AmendmentPanel
          packet={null}
          eligibility={eligibility({ reason, eligible: false, message })}
          onStart={vi.fn()}
        />,
      );
      expect(screen.getByTestId("amendment-refusal-message").textContent).toBe(message);
      expect(screen.getByTestId("amendment-reason-token").textContent).toBe(reason);
      expect(screen.getByRole("button", { name: "Start amendment" })).toBeDisabled();
    });
  }

  it("routes the refusal through GateDisclosure, stopping at the gate that actually failed", () => {
    render(
      <AmendmentPanel
        packet={null}
        eligibility={eligibility({
          reason: "no_imported_scenes",
          eligible: false,
          message: "no imported prose",
        })}
      />,
    );
    fireEvent.click(screen.getByText(/Why is this disabled\?/));
    expect(screen.getByText("Chapter has an approved contract to amend")).toBeInTheDocument();
    expect(screen.getByText("Chapter has imported prose")).toBeInTheDocument();
    // The server short-circuits at the first refusal, so the two later gates were never evaluated and
    // must NOT be shown as passing.
    expect(screen.queryByText(/Some imported scene has no seed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No amendment already in flight/)).not.toBeInTheDocument();
  });

  it("enumerates all four gates as pass only when the chapter is eligible", () => {
    const rows = eligibilityGateRows(eligibility());
    expect(rows).toHaveLength(4);
    expect(rows.every((r) => r.pass)).toBe(true);
  });

  it("frames amendment_already_open as expected when the open branch is the packet shown", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={eligibility({
          reason: "amendment_already_open",
          eligible: false,
          message: "An amendment for this chapter is already open and awaiting review.",
          open_amendment_packet_id: PROPOSED_AMENDMENT.id,
        })}
      />,
    );
    expect(screen.getByTestId("amendment-panel").textContent).toMatch(
      /Expected, not a problem: the amendment already open is the proposal shown above/i,
    );
  });

  it("offers Start amendment only when eligible", () => {
    const onStart = vi.fn();
    render(<AmendmentPanel packet={null} eligibility={eligibility()} onStart={onStart} />);
    const start = screen.getByRole("button", { name: "Start amendment" });
    expect(start).not.toBeDisabled();
    fireEvent.click(start);
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

describe("AmendmentPanel evidence and affected scenes", () => {
  it("labels the fingerprints as fingerprints and refuses to call them a diff", () => {
    render(<AmendmentPanel packet={PROPOSED_AMENDMENT} eligibility={eligibility()} />);
    const panel = screen.getByTestId("amendment-panel").textContent ?? "";
    expect(panel).toMatch(/Prose fingerprint this packet was authored against/);
    expect(panel).toMatch(/Evidence-manifest fingerprint/);
    expect(panel).toContain("aaaaaaaaaaaa…"); // short-hashed, not the full 64 hex chars
    expect(panel).toMatch(/These are hashes, not a diff/);
  });

  it("predicts the drift refusal when the two prose fingerprints differ", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={eligibility({ source_fingerprint: "9999999999999999eeeeeeeeeeeeeeee" })}
      />,
    );
    expect(screen.getByTestId("amendment-panel").textContent).toMatch(
      /approving will very likely refuse with\s+amendment_source_drifted/i,
    );
  });

  it("lists the unseeded scenes as why amendment is permitted", () => {
    render(<AmendmentPanel packet={null} eligibility={eligibility()} />);
    const panel = screen.getByTestId("amendment-panel").textContent ?? "";
    expect(panel).toMatch(/Imported scenes with no seed · 1/);
    expect(panel).toMatch(/why amendment is permitted at all/);
    expect(panel).toContain("55555555");
  });

  it("lists the staled scene contracts as what an approved amendment invalidated", () => {
    render(<AmendmentPanel packet={APPROVED_AMENDMENT} eligibility={null} />);
    const panel = screen.getByTestId("amendment-panel").textContent ?? "";
    expect(panel).toMatch(/Scene contracts staled by this amendment · 2/);
    expect(panel).toMatch(/re-derive these before drafting/);
    expect(panel).toContain("33333333");
    expect(panel).toContain("44444444");
  });
});

describe("AmendmentPanel recovery", () => {
  it("says NOTHING was changed for a drift 409 and names the fix", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={eligibility({ reason: "amendment_already_open", eligible: false })}
        failure={conflict({
          reason: "amendment_source_drifted",
          message: DRIFT_MESSAGE,
          expected: "aaaaaaaaaaaaaaaabbbbbbbbbbbbbbbb",
          actual: "9999999999999999eeeeeeeeeeeeeeee",
        })}
      />,
    );
    const recovery = screen.getByTestId("amendment-recovery");
    expect(recovery.textContent).toMatch(/NOTHING was changed/);
    expect(recovery.textContent).toContain(DRIFT_MESSAGE);
    expect(recovery.textContent).toMatch(/not a partial write, so there is nothing to clean up/i);
    expect(recovery.textContent).toMatch(/Re-run the amendment against the current prose/i);
    expect(screen.getByTestId("amendment-recovery-reason").textContent).toBe(
      "amendment_source_drifted",
    );
    // Both fingerprints, short-hashed, so the author can see which snapshot moved.
    expect(recovery.textContent).toContain("aaaaaaaaaaaa…");
    expect(recovery.textContent).toContain("999999999999…");
  });

  it("points an already-open refusal at the existing branch", () => {
    render(
      <AmendmentPanel
        packet={null}
        eligibility={eligibility({
          reason: "amendment_already_open",
          eligible: false,
          open_amendment_packet_id: "77777777-7777-7777-7777-777777777777",
        })}
        failure={conflict({
          reason: "amendment_already_open",
          message: "An amendment for this chapter is already open and awaiting review.",
        })}
      />,
    );
    const recovery = screen.getByTestId("amendment-recovery");
    expect(recovery.textContent).toMatch(/already open/i);
    expect(recovery.textContent).toContain("77777777");
    expect(recovery.textContent).toMatch(/review or discard it/i);
  });

  it("never renders an opaque failure — an unknown non-409 still gets a next step", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={null}
        failure={new Error("500 Internal Server Error")}
      />,
    );
    const recovery = screen.getByTestId("amendment-recovery");
    expect(recovery.textContent).toContain("500 Internal Server Error");
    expect(recovery.textContent).toMatch(/no machine-readable reason/i);
    expect(recovery.textContent).toMatch(/re-check eligibility before retrying/i);
    expect(screen.queryByTestId("amendment-recovery-reason")).not.toBeInTheDocument();
  });

  it("explains a 409 reason it has no specific recovery for, rather than swallowing it", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={null}
        failure={conflict({ reason: "some_new_token", message: "Server sentence." })}
      />,
    );
    const recovery = screen.getByTestId("amendment-recovery");
    expect(recovery.textContent).toContain("Server sentence.");
    expect(recovery.textContent).toContain("some_new_token");
  });
});

describe("AmendmentPanel consequences and actions", () => {
  it("states plainly what approving does before the author clicks it", () => {
    render(<AmendmentPanel packet={PROPOSED_AMENDMENT} eligibility={null} />);
    const panel = screen.getByTestId("amendment-panel").textContent ?? "";
    expect(panel).toMatch(/What approving does/i);
    expect(panel).toMatch(/stops being the chapter's authority/i);
    expect(panel).toMatch(/marks every live scene contract derived from that predecessor stale/i);
    expect(panel).toMatch(/must be re-derived and re-approved before those scenes can be drafted/i);
    expect(panel).toMatch(/any drift refuses with nothing written/i);
  });

  it("offers Approve amendment only for a proposal, and wires it to the caller", () => {
    const onApprove = vi.fn();
    const { unmount } = render(
      <AmendmentPanel packet={PROPOSED_AMENDMENT} eligibility={null} onApprove={onApprove} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve amendment" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
    unmount();

    render(<AmendmentPanel packet={INITIAL_APPROVED} eligibility={null} onApprove={onApprove} />);
    expect(screen.queryByRole("button", { name: "Approve amendment" })).not.toBeInTheDocument();
  });

  it("disables the actions while one is in flight", () => {
    render(
      <AmendmentPanel
        packet={PROPOSED_AMENDMENT}
        eligibility={eligibility()}
        busy="approve"
        onStart={vi.fn()}
        onApprove={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Approving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start amendment" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
  });

  it("renders nothing at all when there is no verdict, no lineage and no failure", () => {
    const { container } = render(<AmendmentPanel packet={INITIAL_APPROVED} eligibility={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
