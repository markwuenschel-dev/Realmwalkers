import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EditScreen from "./EditScreen";

// The audit is advisory and stateless: the server writes nothing, so this screen is the only place a
// decision exists until the author copies the result out. These tests pin the two things that would
// silently corrupt that — a suggestion applied to prose it was not anchored in, and a rejected
// suggestion still folded into the copied text.

const styleReview = vi.fn();
vi.mock("../api/client", () => ({
  api: {
    styleReview: (...args: unknown[]) => styleReview(...args),
  },
}));

const PROSE =
  "Marcus set the cup down without drinking. He'd been on the other side of that exact decision before.";

const RESULT = {
  suggestions: [
    {
      rule: "R2",
      rule_source: "prose_clarity_rules",
      severity: "warn",
      quote: "He'd been on the other side of that exact decision before.",
      new_text: "He had refused the same thing once, in a room like this one.",
      why: "Points at an event the reader was never shown.",
    },
    {
      rule: "contract 3",
      rule_source: "prose_contract",
      severity: "info",
      quote: "without drinking",
      new_text: null,
      why: "Detail may not earn its place.",
    },
  ],
  standards_loaded: ["prose_clarity_rules", "prose_contract"],
  standards_missing: ["voice_guide"],
  drift_scope_characters: ["marcus"],
  telemetry_recorded: true,
  fabricated_dropped: 0,
  model: "gpt-5.6-luna",
  source_chars: PROSE.length,
  tokens_used: 8123,
};

const proseBox = () => screen.getByPlaceholderText("Paste the passage you want audited.");

async function runAudit() {
  fireEvent.change(proseBox(), { target: { value: PROSE } });
  fireEvent.click(screen.getByRole("button", { name: "Audit" }));
  await waitFor(() => expect(screen.getByText("R2")).toBeTruthy());
}

describe("EditScreen", () => {
  beforeEach(() => {
    sessionStorage.clear();
    styleReview.mockReset();
    styleReview.mockResolvedValue(RESULT);
  });

  it("renders one card per suggestion, citing the rule and its source", async () => {
    render(<EditScreen />);
    await runAudit();

    expect(screen.getByText("R2")).toBeTruthy();
    expect(screen.getByText("contract 3")).toBeTruthy();
    expect(screen.getByText("Points at an event the reader was never shown.")).toBeTruthy();
  });

  it("offers Accept only where a replacement was proposed", async () => {
    // A diagnosis with no `new_text` has nothing to apply — offering Accept would promise an edit the
    // audit never wrote. Dismiss is always available.
    render(<EditScreen />);
    await runAudit();

    expect(screen.getAllByRole("button", { name: "Accept" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Dismiss" })).toHaveLength(2);
  });

  it("copies the original text when nothing is accepted", async () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    render(<EditScreen />);
    await runAudit();

    fireEvent.click(screen.getByRole("button", { name: "Copy with accepted edits" }));
    expect(writeText).toHaveBeenCalledWith(PROSE);
  });

  it("folds in only the accepted suggestion", async () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    render(<EditScreen />);
    await runAudit();

    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    fireEvent.click(screen.getByRole("button", { name: "Copy with accepted edits" }));

    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("He had refused the same thing once");
    // The second suggestion carried no replacement, so its quote must survive untouched.
    expect(copied).toContain("without drinking");
  });

  it("audits the text that was submitted, not later edits to the box", async () => {
    // Every quote anchors by substring. If the author edited the passage after the run, the
    // suggestions would point at text that no longer exists — so the audited copy is pinned.
    render(<EditScreen />);
    await runAudit();

    expect(screen.queryByPlaceholderText("Paste the passage you want audited.")).toBeNull();
    expect(screen.getByRole("button", { name: "Edit again" })).toBeTruthy();
  });

  it("says which standards did not load rather than hiding a weakened audit", async () => {
    render(<EditScreen />);
    await runAudit();

    expect(screen.getByText(/not loaded: voice_guide/)).toBeTruthy();
  });

  it("warns when no character was named, so only always-on drift rules ran", async () => {
    // Cast-scoped rules are never inferred from pronouns, so an empty scope is a real coverage gap
    // the author has to be able to see.
    styleReview.mockResolvedValue({
      ...RESULT,
      standards_loaded: ["prose_clarity_rules", "forbidden_drift"],
      drift_scope_characters: [],
    });
    render(<EditScreen />);
    await runAudit();

    expect(screen.getByText(/only the always-on drift rules ran/)).toBeTruthy();
  });

  it("surfaces an unrecorded run rather than hiding the accounting hole", async () => {
    styleReview.mockResolvedValue({ ...RESULT, telemetry_recorded: false });
    render(<EditScreen />);
    await runAudit();

    expect(screen.getByText(/cost was not recorded/)).toBeTruthy();
  });

  it("reports dropped fabrications", async () => {
    styleReview.mockResolvedValue({ ...RESULT, fabricated_dropped: 2 });
    render(<EditScreen />);
    await runAudit();

    expect(screen.getByText(/2 finding\(s\) dropped/)).toBeTruthy();
  });

  it("distinguishes clean prose from a failed audit", async () => {
    styleReview.mockResolvedValue({ ...RESULT, suggestions: [] });
    render(<EditScreen />);
    fireEvent.change(proseBox(), { target: { value: PROSE } });
    fireEvent.click(screen.getByRole("button", { name: "Audit" }));

    await waitFor(() => expect(screen.getByText(/No rule was broken/)).toBeTruthy());
  });

  it("surfaces the server's error instead of an empty finding list", async () => {
    styleReview.mockRejectedValue(new Error("503 Service Unavailable — No style documents"));
    render(<EditScreen />);
    fireEvent.change(proseBox(), { target: { value: PROSE } });
    fireEvent.click(screen.getByRole("button", { name: "Audit" }));

    await waitFor(() => expect(screen.getByText(/No style documents/)).toBeTruthy());
  });
});
