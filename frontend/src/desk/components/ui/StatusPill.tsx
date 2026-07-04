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
    revise_required: "warn",
    block_drafting: "bad",
    not_run: "neutral",
  },
  prose: {
    drafted: "good",
    drafting: "info",
    queued: "info",
    failed: "bad",
    missing: "neutral",
  },
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
  return (
    <Chip
      label={`${axisLabel}: ${normalized.replace(/_/g, " ")}`}
      tone={tone}
      size={size}
      title={title}
    />
  );
}
