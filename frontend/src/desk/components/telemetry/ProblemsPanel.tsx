"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { css } from "../../css";
import { api } from "../../api/client";
import { copyToClipboard } from "../../lib/download";
import type { TelemetryProblemOut } from "../../api/types";
import type { TelemetryDrawerView } from "./types";
import { SLOW_LATENCY_MS, type LlmCallFilters } from "./telemetryFilters";
import { buildProblemsSummary } from "./telemetryExport";

export function ProblemsPanel({
  bookId,
  onOpen,
}: {
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
}) {
  const [problems, setProblems] = useState<TelemetryProblemOut[] | null>(null);
  const [healthy, setHealthy] = useState(true);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.telemetryProblems(bookId);
      setProblems(d.problems);
      setHealthy(d.healthy);
    } catch {
      setProblems([]);
    }
  }, [bookId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (problems === null) return null;
  if (healthy) {
    return (
      <div
        style={css(
          "margin-bottom:14px;border:1px solid color-mix(in srgb,var(--ok) 35%,var(--line));background:color-mix(in srgb,var(--ok) 6%,var(--bg2));border-radius:10px;padding:12px 14px",
        )}
      >
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--ok)")}>
          No problems detected
        </div>
      </div>
    );
  }

  return (
    <div
      style={css(
        "margin-bottom:14px;border:1px solid color-mix(in srgb,var(--warn, #e8a020) 40%,var(--line));background:color-mix(in srgb,var(--warn, #e8a020) 6%,var(--bg2));border-radius:10px;padding:12px 14px",
      )}
    >
      <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:8px")}>
        <div
          style={css(
            "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);flex:1",
          )}
        >
          Problems detected
        </div>
        <button
          type="button"
          onClick={async () => {
            const ok = await copyToClipboard(buildProblemsSummary(problems ?? [], healthy));
            if (ok) {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }
          }}
          style={css(
            "height:24px;padding:0 10px;border-radius:6px;border:1px solid var(--line);background:var(--bg3);color:var(--dim);font-family:var(--mono);font-size:10px;cursor:pointer",
          )}
        >
          {copied ? "Copied" : "Copy summary"}
        </button>
      </div>
      <div style={css("display:flex;flex-direction:column;gap:8px")}>
        {problems.map((p, i) => (
          <ProblemRow key={i} problem={p} bookId={bookId} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
}

function problemToFilters(p: TelemetryProblemOut): LlmCallFilters {
  const drill = p.drill_down as Record<string, unknown> | undefined;
  switch (p.kind) {
    case "truncation":
      return { problems_only: true };
    case "failed_draft_job":
      return { errors_only: true };
    case "cache_prime_short":
      return { stage_prefix: "scene_packet" };
    case "cache_miss_after_prime":
      return { stage: "scene_packet_author" };
    case "high_latency":
      return { min_latency_ms: SLOW_LATENCY_MS };
    default: {
      const f: LlmCallFilters = {};
      if (drill?.truncated) f.truncated = true;
      if (drill?.errors) f.errors_only = true;
      if (typeof drill?.stage === "string") f.stage = drill.stage;
      if (typeof drill?.run_id === "string") f.run_id = drill.run_id;
      return f;
    }
  }
}

function openProblem(
  p: TelemetryProblemOut,
  bookId: string,
  onOpen: (view: TelemetryDrawerView) => void,
) {
  if (p.kind === "draft_not_ready") {
    onOpen({ kind: "draft_readiness", bookId });
    return;
  }
  onOpen({ kind: "filtered", label: p.summary, bookId, filters: problemToFilters(p) });
}

function formatBreakdownLine(b: Record<string, unknown>): string {
  if (typeof b.chapter_no === "number" && typeof b.scene_no === "number") {
    return `Ch${b.chapter_no} Sc${b.scene_no}: ${String(b.required_action ?? b.reason ?? "")}`;
  }
  if (typeof b.stage === "string") {
    let line = b.stage.replace(/_/g, " ");
    if (typeof b.count === "number") line += `: ${b.count}`;
    if (typeof b.stop_reason === "string") line += ` (${b.stop_reason})`;
    if (typeof b.recommended_action === "string") line += ` — ${b.recommended_action}`;
    return line;
  }
  if (typeof b.error === "string") return b.error.slice(0, 80);
  return JSON.stringify(b).slice(0, 80);
}

function ProblemRow({
  problem: p,
  bookId,
  onOpen,
}: {
  problem: TelemetryProblemOut;
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
}) {
  const router = useRouter();
  const color =
    p.severity === "error"
      ? "var(--bad)"
      : p.severity === "warn"
        ? "var(--warn, #e8a020)"
        : "var(--dim)";

  return (
    <div>
      <button
        type="button"
        onClick={() => openProblem(p, bookId, onOpen)}
        style={css(
          "display:block;width:100%;text-align:left;background:none;border:none;padding:0;cursor:pointer;color:inherit;font:inherit",
        )}
      >
        <div style={css(`font-family:var(--mono);font-size:12.5px;color:${color}`)}>
          {p.summary}
        </div>
      </button>
      {p.breakdown.length > 0 && (
        <ul
          style={css(
            "margin:4px 0 0 16px;padding:0;font-family:var(--mono);font-size:11px;color:var(--dim)",
          )}
        >
          {p.breakdown.slice(0, 6).map((b, j) => {
            const row = b as Record<string, unknown>;
            const chapterId = typeof row.chapter_id === "string" ? row.chapter_id : null;
            return (
              <li key={j}>
                {chapterId && p.kind === "draft_not_ready" ? (
                  <button
                    type="button"
                    onClick={() => router.push(`/packets?chapter=${chapterId}`)}
                    style={css(
                      "background:none;border:none;padding:0;cursor:pointer;color:var(--info, #5b9bd5);font:inherit;text-align:left",
                    )}
                  >
                    {formatBreakdownLine(row)}
                  </button>
                ) : (
                  formatBreakdownLine(row)
                )}
              </li>
            );
          })}
        </ul>
      )}
      {p.recommended_action && (
        <div style={css("margin-top:4px;font-size:11.5px;color:var(--dim)")}>
          {p.recommended_action}
        </div>
      )}
    </div>
  );
}
