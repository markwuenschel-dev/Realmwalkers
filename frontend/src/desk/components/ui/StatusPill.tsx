import Chip from "./Chip";
import type { ChipTone } from "./Chip";

export type StatusAxis = "contract" | "qa" | "prose";

// The Desk's three independent status axes, never merged: contract lifecycle (gold family for
// the healthy path), advisory QA verdict, prose-draft state. "approved + QA: revise required"
// is a legitimate combination and must read as two facts, not one contradiction.
const AXIS_TONES: Record<StatusAxis, Record<string, ChipTone>> = {
  contract: {
    approved: "good",
    proposed: "info",
    stale: "warn",
    rate_limited: "warn",
    blocked: "bad",
  },
  qa: {
    approve: "good",
    approve_warn: "warn",
    approve_with_warnings: "warn",
    revise_required: "warn",
    block_drafting: "bad",
    not_run: "neutral",
  },
  prose: {
    drafted: "good",
    drafting: "info",
    queued: "info",
    failed: "bad",
    // ADR 0027 book-ownership invariant: an ownerless job is quarantined — withheld from execution
    // and from every failure control (no retry/clear). Warn (not bad) keeps it visually distinct
    // from a plain failure: this is a held state an operator resolves, not an errored draft.
    quarantined: "warn",
    missing: "neutral",
  },
};

// A few states read better as a bespoke phrase than the default "axis: state". An integrity hold
// (ADR 0027) is one: "prose: quarantined" undersells that the job is deliberately withheld.
const LABEL_OVERRIDES: Record<string, string> = {
  quarantined: "Integrity hold",
};

/** One axis+state as a labeled chip: "contract: approved", "QA: revise required",
 *  "prose: drafting". Unknown states render neutral rather than crashing. */
export default function StatusPill({
  axis,
  state,
  size = "md",
  title,
}: {
  axis: StatusAxis;
  state: string | null | undefined;
  size?: "sm" | "md";
  title?: string;
}) {
  const normalized = (state ?? (axis === "qa" ? "not_run" : "missing")).toLowerCase();
  const tone = AXIS_TONES[axis][normalized] ?? "neutral";
  const axisLabel = axis === "qa" ? "QA" : axis;
  const label = LABEL_OVERRIDES[normalized] ?? `${axisLabel}: ${normalized.replace(/_/g, " ")}`;
  return <Chip label={label} tone={tone} size={size} title={title} />;
}
