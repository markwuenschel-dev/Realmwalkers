"use client";

import { css } from "../../css";
import type { AgentStatsOut } from "../../api/types";
import { MetricCard } from "../ui";

// Per-agent health from telemetry, as a compact MetricCard row (Atelier stat tiles). Falls back to
// a quiet mono line when the agent has no recent runs — a wall of empty tiles would be noise.
export function AgentHealthStrip({ stats }: { stats: AgentStatsOut | undefined }) {
  const hint = "last 20 runs · 7 days";
  if (!stats || stats.calls === 0) {
    return (
      <span style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>
        From telemetry · {hint} · No recent runs
      </span>
    );
  }
  const esc = stats.escalation_rate != null ? Math.round(stats.escalation_rate * 100) : 0;
  const lat = stats.avg_latency_ms != null ? `${(stats.avg_latency_ms / 1000).toFixed(1)}s` : "—";
  return (
    <div
      style={css(
        "display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;max-width:520px",
      )}
    >
      <MetricCard label="Calls" value={String(stats.calls)} hint={hint} />
      <MetricCard
        label="Escalations"
        value={`${esc}%`}
        tone={esc >= 25 ? "var(--warn)" : "var(--ink)"}
      />
      <MetricCard label="Avg latency" value={lat} />
    </div>
  );
}
