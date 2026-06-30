"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../../css";
import { api } from "../../api/client";
import type { TelemetryProblemOut } from "../../api/types";
import type { TelemetryDrawerView } from "./types";

export function ProblemsPanel({
  bookId,
  onOpen,
}: {
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
}) {
  const [problems, setProblems] = useState<TelemetryProblemOut[] | null>(null);
  const [healthy, setHealthy] = useState(true);

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
      <div
        style={css(
          "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:8px",
        )}
      >
        Problems detected
      </div>
      <div style={css("display:flex;flex-direction:column;gap:8px")}>
        {problems.map((p, i) => (
          <ProblemRow key={i} problem={p} bookId={bookId} onOpen={onOpen} />
        ))}
      </div>
    </div>
  );
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
  const color = p.severity === "error" ? "var(--bad)" : p.severity === "warn" ? "var(--warn, #e8a020)" : "var(--dim)";
  const drill = p.drill_down as Record<string, unknown> | undefined;
  return (
    <div>
      <button
        type="button"
        onClick={() => {
          if (!drill) return;
          const filters: Record<string, string | boolean | number> = { book_id: bookId };
          if (drill.truncated) filters.truncated = true;
          if (drill.errors) filters.errors_only = true;
          if (typeof drill.stage === "string") filters.stage = drill.stage;
          if (typeof drill.run_id === "string") filters.run_id = drill.run_id;
          if (typeof drill.min_latency_ms === "number") filters.min_latency_ms = drill.min_latency_ms;
          onOpen({ kind: "filtered", label: p.summary, bookId, filters });
        }}
        style={css(
          "display:block;width:100%;text-align:left;background:none;border:none;padding:0;cursor:pointer;color:inherit;font:inherit",
        )}
      >
        <div style={css(`font-family:var(--mono);font-size:12.5px;color:${color}`)}>{p.summary}</div>
      </button>
      {p.breakdown.length > 0 && (
        <ul style={css("margin:4px 0 0 16px;padding:0;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
          {p.breakdown.slice(0, 5).map((b, j) => (
            <li key={j}>
              {typeof b.stage === "string" ? b.stage : ""}
              {typeof b.count === "number" ? `: ${b.count}` : ""}
            </li>
          ))}
        </ul>
      )}
      {p.recommended_action && (
        <div style={css("margin-top:4px;font-size:11.5px;color:var(--dim)")}>{p.recommended_action}</div>
      )}
    </div>
  );
}
