"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../css";
import { api } from "../api/client";
import { Spinner } from "./DraftActivity";
import type { ChapterTelemetryOut, SceneTelemetryOut, TelemetryTotals } from "../api/types";

// Shared presentation for persisted LLM-call telemetry (workers/telemetry.py → llm_calls). The same
// pieces render the per-chapter derive panel (under the scene packets) and the global Telemetry tab,
// so cache efficiency, cost, and truncation/error health read the same everywhere.

export function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function cacheColor(ratio: number): string {
  const pct = ratio * 100;
  return pct >= 60 ? "var(--ok)" : pct >= 25 ? "var(--warn, #e8a020)" : "var(--dim)";
}

// One labelled stat. `tone` lets failure counts go red only when non-zero.
function Stat({
  label,
  value,
  color = "var(--ink)",
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div style={css("display:flex;flex-direction:column;gap:2px;min-width:64px")}>
      <span
        style={css(
          "font-family:var(--mono);font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim)",
        )}
      >
        {label}
      </span>
      <span style={css(`font-family:var(--mono);font-size:13px;color:${color}`)}>{value}</span>
    </div>
  );
}

// The headline strip for any totals object (a scene, chapter, stage, model, or whole book).
export function TotalsStrip({ t }: { t: TelemetryTotals }) {
  const cachePct = Math.round(t.cache_hit_ratio * 100);
  return (
    <div
      style={css(
        "display:flex;flex-wrap:wrap;gap:16px;align-items:flex-end;background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:13px 15px",
      )}
    >
      <Stat label="Calls" value={String(t.calls)} />
      <Stat label="In" value={fmtTokens(t.input_tokens)} />
      <Stat label="Out" value={fmtTokens(t.output_tokens)} />
      <Stat label="Cache" value={`${cachePct}%`} color={cacheColor(t.cache_hit_ratio)} />
      <Stat
        label="Saved"
        value={fmtTokens(t.cache_tokens_saved)}
        color={t.cache_tokens_saved > 0 ? "var(--ok)" : "var(--dim)"}
      />
      <Stat
        label="Truncated"
        value={String(t.truncations)}
        color={t.truncations > 0 ? "var(--bad)" : "var(--dim)"}
      />
      <Stat
        label="Errors"
        value={String(t.errors)}
        color={t.errors > 0 ? "var(--bad)" : "var(--dim)"}
      />
      <Stat label="Latency" value={t.avg_latency_ms != null ? `${t.avg_latency_ms}ms` : "—"} />
    </div>
  );
}

// A compact comparison table over a set of totals-bearing rows. `label` names the first column.
export function TotalsTable<T extends TelemetryTotals>({
  label,
  rows,
  nameOf,
  emptyText = "No calls recorded yet.",
}: {
  label: string;
  rows: T[];
  nameOf: (row: T) => string;
  emptyText?: string;
}) {
  if (rows.length === 0) {
    return (
      <div
        style={css("font-family:var(--mono);font-size:11.5px;color:var(--dim);padding:10px 2px")}
      >
        {emptyText}
      </div>
    );
  }
  const cell = "padding:6px 10px;font-family:var(--mono);font-size:11.5px;text-align:right";
  const head =
    "padding:6px 10px;font-family:var(--mono);font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--dim);text-align:right";
  return (
    <div style={css("overflow:auto;border:1px solid var(--line);border-radius:10px")}>
      <table style={css("width:100%;border-collapse:collapse")}>
        <thead>
          <tr style={css("background:var(--bg2)")}>
            <th style={css(`${head};text-align:left`)}>{label}</th>
            <th style={css(head)}>Calls</th>
            <th style={css(head)}>Cache</th>
            <th style={css(head)}>In</th>
            <th style={css(head)}>Out</th>
            <th style={css(head)}>Saved</th>
            <th style={css(head)}>Trunc</th>
            <th style={css(head)}>Err</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={css("border-top:1px solid var(--line)")}>
              <td style={css(`${cell};text-align:left;color:var(--ink)`)}>{nameOf(r)}</td>
              <td style={css(`${cell};color:var(--dim)`)}>{r.calls}</td>
              <td style={css(`${cell};color:${cacheColor(r.cache_hit_ratio)}`)}>
                {Math.round(r.cache_hit_ratio * 100)}%
              </td>
              <td style={css(`${cell};color:var(--dim)`)}>{fmtTokens(r.input_tokens)}</td>
              <td style={css(`${cell};color:var(--dim)`)}>{fmtTokens(r.output_tokens)}</td>
              <td
                style={css(
                  `${cell};color:${r.cache_tokens_saved > 0 ? "var(--ok)" : "var(--dim)"}`,
                )}
              >
                {fmtTokens(r.cache_tokens_saved)}
              </td>
              <td style={css(`${cell};color:${r.truncations > 0 ? "var(--bad)" : "var(--dim)"}`)}>
                {r.truncations}
              </td>
              <td style={css(`${cell};color:${r.errors > 0 ? "var(--bad)" : "var(--dim)"}`)}>
                {r.errors}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SectionLabel({ text }: { text: string }) {
  return (
    <div
      style={css(
        "font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin:14px 0 7px",
      )}
    >
      {text}
    </div>
  );
}

// The per-chapter derive telemetry panel, shown under the scene packets. `refreshKey` lets the host
// re-pull after a derive finishes (bump it). Renders nothing until there's at least one recorded call.
export function ChapterTelemetryPanel({
  chapterId,
  refreshKey = 0,
}: {
  chapterId: string;
  refreshKey?: number;
}) {
  const [data, setData] = useState<ChapterTelemetryOut | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.chapterTelemetry(chapterId));
    } catch {
      /* telemetry is best-effort — never block the packet view on it */
    } finally {
      setLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (!data || data.totals.calls === 0) {
    return loading ? (
      <div
        style={css(
          "display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:14px",
        )}
      >
        <Spinner size={11} /> loading telemetry…
      </div>
    ) : null;
  }

  return (
    <div style={css("margin-top:18px")}>
      <SectionLabel text={`Derive telemetry · latest run · ${data.totals.calls} model calls`} />
      <TotalsStrip t={data.totals} />
      <SectionLabel text="Per scene" />
      <TotalsTable<SceneTelemetryOut>
        label="Scene"
        rows={data.scenes}
        nameOf={(r) =>
          `${r.scene_no != null ? `Scene ${r.scene_no}` : "—"}${
            r.models.length ? ` · ${r.models.join(", ")}` : ""
          }`
        }
      />
    </div>
  );
}
