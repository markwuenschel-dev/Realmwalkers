"use client";

import { css } from "../../css";
import type { LlmCallOut } from "../../api/types";
import type { DrawerNav } from "./types";

function callColor(c: LlmCallOut): string {
  if (c.error) return "var(--bad)";
  if (c.truncated) return "var(--warn, #e8a020)";
  return "color-mix(in srgb, var(--info, #5b9bd5) 55%, var(--dim))";
}

function callStartMs(c: LlmCallOut, fallback: number): number {
  return c.created_at ? new Date(c.created_at).getTime() : fallback;
}

export function RunCallTimeline({ calls, nav }: { calls: LlmCallOut[]; nav: DrawerNav }) {
  if (!calls.length) {
    return <div style={css("font-size:12px;color:var(--dim)")}>No calls to chart</div>;
  }

  const sorted = [...calls].sort((a, b) => {
    const ai = (a.metadata as Record<string, unknown> | null)?.call_index;
    const bi = (b.metadata as Record<string, unknown> | null)?.call_index;
    if (typeof ai === "number" && typeof bi === "number" && ai !== bi) return ai - bi;
    return callStartMs(a, 0) - callStartMs(b, 0);
  });

  const t0 = callStartMs(sorted[0], Date.now());
  let tEnd = t0;
  for (const c of sorted) {
    const start = callStartMs(c, t0);
    const dur = c.latency_ms ?? 80;
    tEnd = Math.max(tEnd, start + dur);
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
        <span>0s</span>
        <span>{totalMs >= 1000 ? `${(totalMs / 1000).toFixed(1)}s` : `${totalMs}ms`} total</span>
      </div>
      {sorted.map((c) => {
        const start = callStartMs(c, t0);
        const dur = c.latency_ms ?? 80;
        const leftPct = ((start - t0) / span) * 100;
        const widthPct = Math.max((dur / span) * 100, 1.5);
        const label = c.stage.replace(/_/g, " ");
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => nav.push({ kind: "call", callId: c.id })}
            title={`${label}${c.scene_no != null ? ` · Sc${c.scene_no}` : ""} · ${dur}ms`}
            style={css(
              "display:grid;grid-template-columns:minmax(0,38%) 1fr;gap:8px;align-items:center;text-align:left;border:1px solid var(--line);border-radius:6px;padding:4px 8px;background:var(--bg3);cursor:pointer;color:var(--ink);font-family:var(--mono);font-size:10px",
            )}
          >
            <span style={css("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>
              {label}
              {c.scene_no != null ? (
                <span style={css("color:var(--dim)")}> · Sc{c.scene_no}</span>
              ) : null}
            </span>
            <div
              style={css(
                "position:relative;height:14px;border-radius:3px;background:color-mix(in srgb,var(--line) 40%,transparent)",
              )}
            >
              <div
                style={{
                  position: "absolute",
                  left: `${leftPct}%`,
                  width: `${widthPct}%`,
                  top: 2,
                  bottom: 2,
                  borderRadius: 2,
                  background: callColor(c),
                  minWidth: 4,
                }}
              />
            </div>
          </button>
        );
      })}
    </div>
  );
}
