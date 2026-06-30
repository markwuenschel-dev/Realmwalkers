"use client";

import { useCallback, useEffect, useState } from "react";
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

function ProblemRow({
  problem: p,
  bookId,
  onOpen,
}: {
  problem: TelemetryProblemOut;
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
}) {
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
        onClick={() =>
          onOpen({ kind: "filtered", label: p.summary, bookId, filters: problemToFilters(p) })
        }
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
          {p.breakdown.slice(0, 5).map((b, j) => (
            <li key={j}>
              {typeof b.stage === "string" ? b.stage : ""}
              {typeof b.count === "number" ? `: ${b.count}` : ""}
            </li>
          ))}
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
