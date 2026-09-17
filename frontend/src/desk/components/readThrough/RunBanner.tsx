// RunBanner — progress of the book's active read-through, fed by the slim status poll.

import type { ReadThroughStatusOut } from "../../api/types";
import { css } from "../../css";
import { formatCount } from "../../lib/readThroughMarkdown";
import { Button, Panel, ProgressBar, Spinner } from "../ui";

export const LEAVE_NOTE =
  "Runs on the server — you can leave this screen. It may wait for a model slot.";
export const STOP_DISCLOSURE =
  "Stopping keeps finished chapters. A request already sent to the model may still be billed.";

export function progressHeadline(s: ReadThroughStatusOut): string {
  if (s.status === "queued") return "Waiting to start…";
  if (s.status === "stopping" || s.stop_requested) return "Stopping…";
  const processed = s.chapters_done + s.chapters_failed + s.chapters_skipped;
  if (s.chapters_total === 0) return "Running…";
  if (processed >= s.chapters_total) return "Reading across chapters…";
  return `Chapter ${processed + 1} of ${s.chapters_total}${s.current_label ? ` · ${s.current_label}` : ""}`;
}

const MONO = "font-family:var(--mono);font-size:11px;color:var(--dim)";

export default function RunBanner({
  status,
  title,
  onStop,
  stopBusy,
  stopError,
}: {
  status: ReadThroughStatusOut;
  title?: string;
  onStop: () => void;
  stopBusy: boolean;
  stopError: string | null;
}) {
  const processed = status.chapters_done + status.chapters_failed + status.chapters_skipped;
  const value =
    status.status === "queued" || status.chapters_total === 0
      ? null
      : processed / status.chapters_total;
  const stopDisabled = stopBusy || status.status === "stopping" || status.stop_requested;
  const tally = [
    `Attempts ${status.attempts_used} of ${status.attempt_allowance}`,
    `${formatCount(status.tokens_charged)} tokens charged`,
    status.chapters_failed > 0 ? `${status.chapters_failed} failed` : null,
    status.chapters_skipped > 0 ? `${status.chapters_skipped} skipped` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Panel
      eyebrow={title ? `Read-through running · ${title}` : "Read-through running"}
      title={
        <span style={css("display:inline-flex;align-items:center;gap:10px")}>
          <Spinner size={14} />
          <span>{progressHeadline(status)}</span>
        </span>
      }
      actions={
        <Button size="sm" variant="danger" disabled={stopDisabled} onClick={onStop}>
          Stop
        </Button>
      }
    >
      <ProgressBar value={value} />
      <div style={css(`${MONO};margin-top:10px`)}>{tally}</div>
      <p style={css("margin:8px 0 0;font-size:13px;color:var(--ink)")}>{LEAVE_NOTE}</p>
      <p style={css(`${MONO};margin:4px 0 0`)}>{STOP_DISCLOSURE}</p>
      {status.error && (
        <p style={css("margin:6px 0 0;font-size:12.5px;color:var(--warn)")}>{status.error}</p>
      )}
      {stopError && (
        <p style={css("margin:6px 0 0;font-size:12.5px;color:var(--bad)")}>
          {`Couldn't stop: ${stopError}`}
        </p>
      )}
    </Panel>
  );
}
