"use client";

import type { AgentStatsOut } from "../../api/types";

export function AgentHealthStrip({ stats }: { stats: AgentStatsOut | undefined }) {
  const label = "From telemetry · last 20 runs · 7 days";
  if (!stats || stats.calls === 0) {
    return (
      <span style={{ fontSize: "12px", color: "var(--dim)", fontFamily: "var(--mono)" }}>
        {label} · No recent runs
      </span>
    );
  }
  const esc = stats.escalation_rate != null ? Math.round(stats.escalation_rate * 100) : 0;
  const lat = stats.avg_latency_ms != null ? `${(stats.avg_latency_ms / 1000).toFixed(1)}s` : "—";
  return (
    <span style={{ fontSize: "12px", color: "var(--dim)", fontFamily: "var(--mono)" }}>
      {label} · {stats.calls} calls · {esc}% escalations · avg {lat}
    </span>
  );
}
