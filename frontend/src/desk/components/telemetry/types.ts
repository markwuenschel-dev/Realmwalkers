"use client";

import type { LlmCallOut, RunTelemetryOut, TelemetryGroupOut, TelemetryTotals } from "../../api/types";

export type TelemetryDrawerView =
  | { kind: "run"; runId: string }
  | { kind: "stage"; stage: string; bookId: string; runId?: string }
  | { kind: "model"; model: string; bookId: string; runId?: string }
  | { kind: "scene"; runId: string; sceneNo: number }
  | { kind: "call"; callId: string }
  | { kind: "compare"; runA: string; runB: string; bookId: string }
  | { kind: "filtered"; label: string; bookId: string; filters: Record<string, string | boolean | number> };

export type DrawerNav = {
  view: TelemetryDrawerView;
  push: (v: TelemetryDrawerView) => void;
  pop: () => void;
  close: () => void;
  stack: TelemetryDrawerView[];
};

export function statusColor(status: string): string {
  if (status === "error") return "var(--bad)";
  if (status === "warn") return "var(--warn, #e8a020)";
  return "var(--ok)";
}

export function worstCall(calls: LlmCallOut[]): LlmCallOut | null {
  if (!calls.length) return null;
  return calls.reduce((a, b) => ((a.latency_ms ?? 0) >= (b.latency_ms ?? 0) ? a : b));
}

export function groupLabel(view: TelemetryDrawerView): string {
  switch (view.kind) {
    case "run":
      return "Run detail";
    case "stage":
      return view.stage.replace(/_/g, " ");
    case "model":
      return view.model;
    case "scene":
      return `Scene ${view.sceneNo}`;
    case "call":
      return "Call detail";
    case "compare":
      return "Compare runs";
    case "filtered":
      return view.label;
    default:
      return "Telemetry";
  }
}

export type RunDetailProps = {
  data: RunTelemetryOut;
  nav: DrawerNav;
};

export type StageRollup = TelemetryGroupOut & { key: string };

export function stageFlags(t: TelemetryTotals): string[] {
  const flags: string[] = [];
  if (t.truncations > 0) flags.push("high truncation");
  if (t.cache_hit_ratio < 0.25 && t.calls >= 2) flags.push("low cache hit");
  if ((t.avg_latency_ms ?? 0) > 15000) flags.push("high latency");
  if (t.errors > 0) flags.push("errors");
  return flags;
}
