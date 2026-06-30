"use client";

import { css } from "../../css";
import type { LlmCallOut } from "../../api/types";
import type { DrawerNav } from "./types";
import { FANOUT_STAGES, PRIME_STAGES, stageSortKey } from "./telemetryStages";

function callColor(c: LlmCallOut): string {
  if (c.error) return "var(--bad)";
  if (c.truncated) return "var(--warn, #e8a020)";
  return "color-mix(in srgb, var(--info, #5b9bd5) 55%, var(--dim))";
}

function callStartMs(c: LlmCallOut, fallback: number): number {
  return c.created_at ? new Date(c.created_at).getTime() : fallback;
}

function callIndex(c: LlmCallOut): number {
  const i = (c.metadata as Record<string, unknown> | null)?.call_index;
  return typeof i === "number" ? i : 999999;
}

type TimelineRow = { key: string; label: string; calls: LlmCallOut[] };

function buildTimelineRows(calls: LlmCallOut[]): TimelineRow[] {
  const sorted = [...calls].sort((a, b) => {
    const d = callIndex(a) - callIndex(b);
    if (d !== 0) return d;
    return callStartMs(a, 0) - callStartMs(b, 0);
  });

  const rows: TimelineRow[] = [];
  const seenStages = new Set<string>();

  for (const stage of [...new Set(sorted.map((c) => c.stage))].sort(
    (a, b) => stageSortKey(a) - stageSortKey(b),
  )) {
    if (seenStages.has(stage)) continue;
    seenStages.add(stage);
    const stageCalls = sorted.filter((c) => c.stage === stage);

    if (PRIME_STAGES.has(stage)) {
      rows.push({
        key: stage,
        label: stage.replace(/_/g, " "),
        calls: stageCalls,
      });
      continue;
    }

    if (FANOUT_STAGES.has(stage)) {
      const scenes = [
        ...new Set(stageCalls.map((c) => c.scene_no).filter((n): n is number => n != null)),
      ].sort((a, b) => a - b);
      for (const sn of scenes) {
        const group = stageCalls.filter((c) => c.scene_no === sn);
        rows.push({
          key: `${stage}-sc${sn}`,
          label: `Sc${sn} · ${stage.replace(/_/g, " ")}`,
          calls: group,
        });
      }
      const orphans = stageCalls.filter((c) => c.scene_no == null);
      if (orphans.length) {
        rows.push({
          key: `${stage}-orphan`,
          label: stage.replace(/_/g, " "),
          calls: orphans,
        });
      }
      continue;
    }

    const byScene = new Map<number | "none", LlmCallOut[]>();
    for (const c of stageCalls) {
      const k = c.scene_no ?? "none";
      const list = byScene.get(k) ?? [];
      list.push(c);
      byScene.set(k, list);
    }
    for (const [sn, group] of [...byScene.entries()].sort((a, b) => {
      if (a[0] === "none") return 1;
      if (b[0] === "none") return -1;
      return (a[0] as number) - (b[0] as number);
    })) {
      const label =
        sn === "none" ? stage.replace(/_/g, " ") : `Sc${sn} · ${stage.replace(/_/g, " ")}`;
      rows.push({ key: `${stage}-${sn}`, label, calls: group });
    }
  }

  return rows;
}

export function RunCallTimeline({ calls, nav }: { calls: LlmCallOut[]; nav: DrawerNav }) {
  if (!calls.length) {
    return <div style={css("font-size:12px;color:var(--dim)")}>No calls to chart</div>;
  }

  const rows = buildTimelineRows(calls);
  const allSorted = [...calls].sort((a, b) => callIndex(a) - callIndex(b));
  const t0 = callStartMs(allSorted[0], Date.now());
  let tEnd = t0;
  for (const c of allSorted) {
    const start = callStartMs(c, t0);
    tEnd = Math.max(tEnd, start + (c.latency_ms ?? 80));
  }
  const span = Math.max(tEnd - t0, 1);
  const totalMs = tEnd - t0;

  return (
    <div style={css("display:flex;flex-direction:column;gap:3px")}>
      <div
        style={css(
          "display:flex;justify-content:space-between;font-family:var(--mono);font-size:9.5px;color:var(--dim);margin-bottom:4px",
        )}
      >
        <span>Pipeline timeline (parallel scenes)</span>
        <span>{totalMs >= 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`}</span>
      </div>
      {rows.map((row) => {
        const starts = row.calls.map((c) => callStartMs(c, t0));
        const ends = row.calls.map((c, i) => starts[i] + (c.latency_ms ?? 80));
        const rowStart = Math.min(...starts);
        const rowEnd = Math.max(...ends);
        const leftPct = ((rowStart - t0) / span) * 100;
        const widthPct = Math.max(((rowEnd - rowStart) / span) * 100, 1.5);
        const worst = row.calls.reduce((a, b) =>
          (a.latency_ms ?? 0) >= (b.latency_ms ?? 0) ? a : b,
        );
        const dur = rowEnd - rowStart;
        return (
          <button
            key={row.key}
            type="button"
            onClick={() => nav.push({ kind: "call", callId: worst.id })}
            title={`${row.label} · ${dur}ms span · ${row.calls.length} call(s)`}
            style={css(
              "display:grid;grid-template-columns:minmax(0,42%) 1fr;gap:8px;align-items:center;text-align:left;border:1px solid var(--line);border-radius:6px;padding:4px 8px;background:var(--bg3);cursor:pointer;color:var(--ink);font-family:var(--mono);font-size:10px",
            )}
          >
            <span style={css("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>
              {row.label}
              {row.calls.length > 1 ? (
                <span style={css("color:var(--dim)")}> ×{row.calls.length}</span>
              ) : null}
            </span>
            <div
              style={css(
                "position:relative;height:14px;border-radius:3px;background:color-mix(in srgb,var(--line) 40%,transparent)",
              )}
            >
              {row.calls.length === 1 ? (
                <div
                  style={{
                    position: "absolute",
                    left: `${leftPct}%`,
                    width: `${widthPct}%`,
                    top: 2,
                    bottom: 2,
                    borderRadius: 2,
                    background: callColor(row.calls[0]),
                    minWidth: 4,
                  }}
                />
              ) : (
                row.calls.map((c, i) => {
                  const s = starts[i];
                  const w = Math.max(((c.latency_ms ?? 80) / span) * 100, 1);
                  const l = ((s - t0) / span) * 100;
                  return (
                    <div
                      key={c.id}
                      style={{
                        position: "absolute",
                        left: `${l}%`,
                        width: `${w}%`,
                        top: 2,
                        bottom: 2,
                        borderRadius: 2,
                        background: callColor(c),
                        minWidth: 3,
                        opacity: 0.85,
                      }}
                    />
                  );
                })
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
