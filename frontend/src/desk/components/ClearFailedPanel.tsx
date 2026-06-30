"use client";

import { useState } from "react";
import { css } from "../css";
import type { FailedJobOut, RetryFailedOut } from "../api/types";

type Props = {
  failedCount: number;
  failedJobs: FailedJobOut[];
  onRetry?: () => Promise<RetryFailedOut | null>;
  onClear: () => Promise<unknown>;
  scopeLabel?: string;
  compact?: boolean;
};

/** Shared failed-draft jobs banner — Inbox, Packets, Scene Packets, Manuscript Draft. */
export default function ClearFailedPanel({
  failedCount,
  failedJobs,
  onRetry,
  onClear,
  scopeLabel,
  compact = false,
}: Props) {
  const [busy, setBusy] = useState<"retry" | "clear" | null>(null);
  const [lastResult, setLastResult] = useState<RetryFailedOut | null>(null);
  if (failedCount <= 0) return null;

  const scope = scopeLabel ? ` (${scopeLabel})` : "";

  return (
    <div
      style={css(
        "border:1px solid color-mix(in srgb,var(--bad) 32%,var(--line));background:color-mix(in srgb,var(--bad) 7%,var(--bg2));border-radius:10px;padding:12px 13px",
      )}
    >
      <div
        style={css(
          "font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--bad);margin-bottom:5px",
        )}
      >
        {failedCount} failed{scope}
      </div>
      {!compact && (
        <div style={css("font-size:12px;color:var(--dim);line-height:1.45;margin-bottom:10px")}>
          Errored mid-draft. Clear to dismiss, or retry once the cause below is fixed.
        </div>
      )}
      {failedJobs.length > 0 && !compact && (
        <div
          style={css(
            "display:flex;flex-direction:column;gap:5px;margin-bottom:10px;max-height:150px;overflow:auto",
          )}
        >
          {failedJobs.slice(0, 6).map((f) => (
            <div
              key={f.id}
              style={css(
                "font-family:var(--mono);font-size:10.5px;line-height:1.4;color:var(--dim);overflow-wrap:anywhere",
              )}
            >
              <span style={css("color:var(--bad)")}>
                Ch{f.chapter_no ?? "?"}·Sc{f.scene_no ?? "?"}
              </span>{" "}
              {f.last_error ?? "unknown error"}
            </div>
          ))}
          {failedJobs.length > 6 && (
            <div style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              …and {failedJobs.length - 6} more
            </div>
          )}
        </div>
      )}
      <div style={css("display:flex;flex-direction:column;gap:8px")}>
        {onRetry && (
          <button
            disabled={busy != null}
            onClick={async () => {
              setBusy("retry");
              try {
                const out = await onRetry();
                setLastResult(out);
              } finally {
                setBusy(null);
              }
            }}
            style={css(
              `width:100%;padding:8px;border-radius:7px;border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));background:color-mix(in srgb,var(--bad) 12%,var(--bg3));color:var(--bad);font-size:12.5px;cursor:${busy ? "default" : "pointer"};font-family:var(--ui)`,
            )}
          >
            {busy === "retry" ? "Re-queuing…" : `Retry ${failedCount} failed`}
          </button>
        )}
        <button
          disabled={busy != null}
          onClick={async () => {
            if (
              !confirm(
                `Clear ${failedCount} failed draft job${failedCount === 1 ? "" : "s"}${scope}? They will not be re-queued.`,
              )
            )
              return;
            setBusy("clear");
            try {
              await onClear();
              setLastResult(null);
            } finally {
              setBusy(null);
            }
          }}
          style={css(
            `width:100%;padding:8px;border-radius:7px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-size:12.5px;cursor:${busy ? "default" : "pointer"};font-family:var(--ui)`,
          )}
        >
          {busy === "clear" ? "Clearing…" : "Clear failed"}
        </button>
      </div>
      {lastResult && onRetry && (
        <div
          style={css("margin-top:10px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}
        >
          {lastResult.requested ?? failedCount} requested · {lastResult.requeued} queued
          {(lastResult.skipped?.length ?? 0) > 0 && (
            <span style={css("color:var(--warn)")}> · {lastResult.skipped!.length} blocked</span>
          )}
        </div>
      )}
    </div>
  );
}
