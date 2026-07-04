import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ClearFailedPanel from "./ClearFailedPanel";
import type { FailedJobOut } from "../api/types";

const STALE_JOB: FailedJobOut = {
  id: "job-1",
  chapter_no: 1,
  scene_no: 1,
  last_error:
    "ScenePacketRequiredError: scene packet abc is stale (upstream inputs changed since derivation) — re-derive or re-approve it before drafting @ approval_policy.py:200",
};

const OTHER_JOB: FailedJobOut = {
  id: "job-2",
  chapter_no: 1,
  scene_no: 2,
  last_error: "BadRequestError: Error code: 400 - credit balance too low",
};

describe("ClearFailedPanel", () => {
  it("surfaces the stale-packet remedy instead of burying it in the error string", () => {
    // Regression: a stale-packet failure has a known two-step remedy (re-approve/re-derive in the
    // Packets tab, THEN retry) and Retry alone re-fails in milliseconds — the banner must say so.
    render(<ClearFailedPanel failedCount={1} failedJobs={[STALE_JOB]} onClear={vi.fn()} />);
    const hint = screen.getByTestId("stale-packet-hint");
    expect(hint.textContent).toContain("Re-approve");
    expect(hint.textContent).toContain("Packets");
  });

  it("shows no stale hint for unrelated failures", () => {
    render(<ClearFailedPanel failedCount={1} failedJobs={[OTHER_JOB]} onClear={vi.fn()} />);
    expect(screen.queryByTestId("stale-packet-hint")).not.toBeInTheDocument();
  });

  it("keeps the hint out of compact mode (indicator strips)", () => {
    render(<ClearFailedPanel failedCount={1} failedJobs={[STALE_JOB]} onClear={vi.fn()} compact />);
    expect(screen.queryByTestId("stale-packet-hint")).not.toBeInTheDocument();
  });
});
