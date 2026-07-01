"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { css } from "../../css";
import { api } from "../../api/client";
import { useDeskData } from "../../api/data";
import ClearFailedPanel from "../ClearFailedPanel";
import { copyToClipboard } from "../../lib/download";
import type { TelemetryProblemOut } from "../../api/types";
import type { TelemetryDrawerView } from "./types";
import { SLOW_LATENCY_MS, type LlmCallFilters } from "./telemetryFilters";
import { buildProblemsSummary } from "./telemetryExport";

export function ProblemsPanel({
  bookId,
  onOpen,
  reloadKey,
}: {
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
  reloadKey?: number;
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
  }, [load, reloadKey]);

  if (problems === null) return null;

  const problemCount = problems.reduce((n, p) => n + (p.count > 0 ? p.count : 1), 0);

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
      <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap")}>
        <div
          style={css(
            "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);flex:1",
          )}
        >
          Problems detected
        </div>
        <span
          style={css(
            "font-family:var(--mono);font-size:10px;padding:2px 8px;border-radius:999px;background:color-mix(in srgb,var(--warn, #e8a020) 18%,var(--bg3));color:var(--ink)",
          )}
        >
          {problemCount}
        </span>
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
      <div
        style={css(
          "display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px",
        )}
      >
        {problems.map((p, i) => (
          <ProblemCard
            key={`${p.kind}-${i}`}
            problem={p}
            bookId={bookId}
            onOpen={onOpen}
            onReload={load}
          />
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
    const action = String(b.required_action ?? b.reason ?? "");
    const count = typeof b.count === "number" && b.count > 1 ? ` (×${b.count})` : "";
    return `Ch${b.chapter_no} Sc${b.scene_no}: ${action}${count}`;
  }
  if (typeof b.stage === "string") {
    let line = b.stage.replace(/_/g, " ");
    if (typeof b.count === "number" && b.count > 1) line += ` (×${b.count})`;
    else if (typeof b.count === "number") line += `: ${b.count}`;
    if (typeof b.stop_reason === "string") line += ` (${b.stop_reason})`;
    if (typeof b.recommended_action === "string") line += ` — ${b.recommended_action}`;
    return line;
  }
  if (typeof b.error === "string") return b.error.slice(0, 80);
  if (typeof b.count === "number" && b.count > 1) {
    return `${JSON.stringify(b).slice(0, 60)} (×${b.count})`;
  }
  return JSON.stringify(b).slice(0, 80);
}

function severityColor(severity: string): string {
  if (severity === "error") return "var(--bad)";
  if (severity === "warn") return "var(--warn, #e8a020)";
  return "var(--dim)";
}

function ProblemCard({
  problem: p,
  bookId,
  onOpen,
  onReload,
}: {
  problem: TelemetryProblemOut;
  bookId: string;
  onOpen: (view: TelemetryDrawerView) => void;
  onReload: () => Promise<void>;
}) {
  const router = useRouter();
  const data = useDeskData();
  const [expanded, setExpanded] = useState(false);
  const color = severityColor(p.severity);
  const breakdown = p.breakdown as Record<string, unknown>[];
  const hasBreakdown = breakdown.length > 0;
  const preview = expanded ? breakdown : breakdown.slice(0, 3);

  return (
    <div
      style={css(
        "border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:var(--bg2);display:flex;flex-direction:column;gap:6px",
      )}
    >
      <div style={css("display:flex;align-items:flex-start;gap:8px")}>
        <span
          style={css(
            `flex-shrink:0;font-family:var(--mono);font-size:9px;text-transform:uppercase;padding:2px 6px;border-radius:5px;border:1px solid ${color};color:${color}`,
          )}
        >
          {p.severity}
        </span>
        <button
          type="button"
          onClick={() => openProblem(p, bookId, onOpen)}
          style={css(
            "flex:1;text-align:left;background:none;border:none;padding:0;cursor:pointer;color:inherit;font:inherit",
          )}
        >
          <div style={css(`font-family:var(--mono);font-size:12px;color:${color};line-height:1.4`)}>
            {p.summary}
            {p.count > 1 && (
              <span style={css("color:var(--dim);font-size:11px")}> · ×{p.count}</span>
            )}
          </div>
        </button>
      </div>

      {hasBreakdown && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            style={css(
              "background:none;border:none;padding:0;cursor:pointer;font-family:var(--mono);font-size:10px;color:var(--dim)",
            )}
          >
            {expanded ? "Hide breakdown" : `Show breakdown (${breakdown.length})`}
          </button>
          <ul
            style={css(
              "margin:4px 0 0;padding:0 0 0 16px;font-family:var(--mono);font-size:11px;color:var(--dim);line-height:1.45",
            )}
          >
            {preview.map((b, j) => {
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
            {!expanded && breakdown.length > 3 && (
              <li style={css("color:var(--dim);list-style:none;margin-left:-16px")}>
                … {breakdown.length - 3} more
              </li>
            )}
          </ul>
        </div>
      )}

      {p.recommended_action && (
        <div style={css("font-size:11.5px;color:var(--dim);line-height:1.4")}>
          {p.recommended_action}
        </div>
      )}

      {p.kind === "failed_draft_job" && (
        <ClearFailedPanel
          compact
          failedCount={data.jobs.failed}
          failedJobs={data.failedJobs}
          onRetry={async () => {
            const out = await data.retryFailed();
            await onReload();
            return out;
          }}
          onClear={async () => {
            await data.clearFailed();
            await onReload();
          }}
        />
      )}

      {(p.kind === "soft_work_budget_exceeded" ||
        p.kind === "hard_work_budget_exceeded" ||
        p.kind === "high_latency") && (
        <button
          type="button"
          onClick={() => router.push("/settings")}
          style={css(
            "align-self:flex-start;background:none;border:none;padding:0;cursor:pointer;font-family:var(--mono);font-size:11px;color:var(--info, #5b9bd5)",
          )}
        >
          Adjust in Settings → Agent Ops
        </button>
      )}
    </div>
  );
}
