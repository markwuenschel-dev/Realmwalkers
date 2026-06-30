import { downloadBlob } from "../../lib/download";
import type { LlmCallOut, RunTelemetryOut, TelemetryProblemOut } from "../../api/types";
import { stageFlags } from "./types";

function csvEscape(value: string | number | boolean | null | undefined): string {
  if (value == null) return "";
  const s = String(value);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function buildCallsCsv(calls: LlmCallOut[]): string {
  const headers = [
    "id",
    "created_at",
    "stage",
    "scene_no",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "latency_ms",
    "truncated",
    "error",
    "estimated_cost_usd",
  ];
  const rows = calls.map((c) =>
    [
      c.id,
      c.created_at ?? "",
      c.stage,
      c.scene_no ?? "",
      c.model,
      c.input_tokens,
      c.output_tokens,
      c.cache_creation_tokens,
      c.cache_read_tokens,
      c.latency_ms ?? "",
      c.truncated,
      c.error ?? "",
      c.estimated_cost_usd ?? 0,
    ]
      .map(csvEscape)
      .join(","),
  );
  return `${headers.join(",")}\n${rows.join("\n")}\n`;
}

export function downloadRunJson(data: RunTelemetryOut): void {
  downloadBlob(
    `telemetry_run_${data.run_id.slice(0, 8)}.json`,
    JSON.stringify(data, null, 2),
    "application/json;charset=utf-8",
  );
}

export function downloadCallsCsv(calls: LlmCallOut[], prefix: string): void {
  downloadBlob(`${prefix}_calls.csv`, buildCallsCsv(calls), "text/csv;charset=utf-8");
}

export function buildRunDiagnosisSummary(data: RunTelemetryOut): string {
  const lines: string[] = [];
  const stamp = data.started_at
    ? new Date(data.started_at).toLocaleString()
    : data.run_id.slice(0, 8);
  const ch =
    data.chapter_no != null ? `Ch ${data.chapter_no}${data.title ? ` · ${data.title}` : ""}` : "";
  lines.push(`Run telemetry · ${stamp}${ch ? ` · ${ch}` : ""}`);
  lines.push(`Run ID: ${data.run_id}`);
  lines.push("");
  const t = data.totals;
  lines.push(
    `Totals: ${t.calls} calls · ${t.input_tokens} in / ${t.output_tokens} out · ${Math.round(t.cache_hit_ratio * 100)}% cache hit`,
  );
  lines.push(
    `Issues: ${t.truncations} truncations · ${t.errors} errors · ${t.fallbacks} fallbacks · est $${t.estimated_cost_usd.toFixed(4)}`,
  );
  const flags = stageFlags(t);
  if (flags.length) lines.push(`Flags: ${flags.join(", ")}`);
  lines.push("");

  const problemScenes = data.scenes.filter((s) => s.status !== "ok");
  if (problemScenes.length) {
    lines.push("Scenes with issues:");
    for (const s of problemScenes) {
      lines.push(
        `  Sc${s.scene_no ?? "?"} [${s.status}]: ${s.stage_summary || `${s.truncations} trunc · ${s.errors} err`}`,
      );
    }
    lines.push("");
  }

  const worst = [...data.calls].filter((c) => c.truncated || c.error).slice(0, 8);
  if (worst.length) {
    lines.push("Notable calls:");
    for (const c of worst) {
      lines.push(
        `  ${c.stage}${c.scene_no != null ? ` Sc${c.scene_no}` : ""}: ${c.truncated ? "TRUNC" : ""}${c.error ? ` ERR ${c.error.slice(0, 60)}` : ""}`.trim(),
      );
    }
  }

  return lines.join("\n");
}

export function buildProblemsSummary(problems: TelemetryProblemOut[], healthy: boolean): string {
  if (healthy || !problems.length) return "No telemetry problems detected.";
  const lines = [`Telemetry problems (${problems.length}):`, ""];
  for (const p of problems) {
    lines.push(`[${p.severity}] ${p.summary} (${p.count})`);
    if (p.breakdown?.length) {
      for (const b of p.breakdown.slice(0, 5)) {
        const label =
          (b as { stage?: string; label?: string }).stage ?? (b as { label?: string }).label;
        const count = (b as { count?: number }).count;
        if (label != null && count != null) lines.push(`  ${label}: ${count}`);
      }
    }
    if (p.recommended_action) lines.push(`  → ${p.recommended_action}`);
    lines.push("");
  }
  return lines.join("\n").trim();
}
