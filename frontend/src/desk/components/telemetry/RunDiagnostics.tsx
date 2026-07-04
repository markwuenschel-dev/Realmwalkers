"use client";

import { css } from "../../css";
import type { LlmCallOut } from "../../api/types";
import { fmtTokens } from "../Telemetry";
import { Eyebrow } from "../ui";
import { cacheHealthByStage, formatSettingsKey } from "./telemetryDiagnosis";
import { PRIME_STAGES } from "./telemetryStages";

export function RunSettingsSnapshot({ snapshot }: { snapshot: Record<string, unknown> }) {
  const entries = Object.entries(snapshot).filter(([, v]) => v != null && v !== "");
  if (!entries.length) return null;
  return (
    <div
      style={css(
        "display:flex;flex-direction:column;gap:4px;border:1px solid var(--line);border-radius:var(--r);padding:8px 10px;background:var(--boxbg)",
      )}
    >
      {entries.map(([k, v]) => (
        <div
          key={k}
          style={css(
            "display:flex;justify-content:space-between;gap:10px;font-family:var(--mono);font-size:10.5px",
          )}
        >
          <span style={css("color:var(--dim)")}>{formatSettingsKey(k)}</span>
          <span style={css("color:var(--ink)")}>{String(v)}</span>
        </div>
      ))}
    </div>
  );
}

export function RunCacheDiagnostics({ calls }: { calls: LlmCallOut[] }) {
  const rows = cacheHealthByStage(calls);
  const primes = rows.filter((r) => PRIME_STAGES.has(r.stage));
  const rest = rows.filter((r) => !PRIME_STAGES.has(r.stage));
  if (!rows.length) return null;

  return (
    <div style={css("display:flex;flex-direction:column;gap:8px")}>
      {primes.length > 0 && (
        <div style={css("font-family:var(--mono);font-size:10px;color:var(--dim)")}>
          Prefix primes:{" "}
          {primes
            .map((p) => `${p.stage.replace(/_/g, " ")} (${fmtTokens(p.input)} in)`)
            .join(" · ")}
        </div>
      )}
      <div style={css("display:flex;flex-direction:column;gap:3px")}>
        {rest.map((r) => {
          const low = r.hitPct < 25 && r.calls >= 2;
          const shortPrime = PRIME_STAGES.has(r.stage) && r.input < 1024;
          return (
            <div
              key={r.stage}
              style={css(
                "display:flex;justify-content:space-between;gap:8px;font-family:var(--mono);font-size:10.5px;padding:4px 8px;border-radius:6px;border:1px solid var(--line);background:var(--boxbg)",
              )}
            >
              <span style={css("color:var(--ink)")}>{r.stage.replace(/_/g, " ")}</span>
              <span style={css(`color:${low || shortPrime ? "var(--warn)" : "var(--dim)"}`)}>
                {r.hitPct}% hit · {r.calls} calls
                {shortPrime ? " · short prime" : ""}
                {low ? " · low cache" : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CallTruncationPanel({ call }: { call: LlmCallOut }) {
  if (!call.truncated && !call.error) return null;
  const meta = (call.metadata ?? {}) as Record<string, unknown>;
  const advice = call.stage.startsWith("scene_packet")
    ? "Increase scene_packet max_tokens or shorten chapter prefix."
    : call.stage === "drafter"
      ? "Increase draft max_tokens or reduce scene context."
      : "Raise max_tokens or shorten prompt for this stage.";

  return (
    <div
      style={css(
        "border:1px solid color-mix(in srgb,var(--warn) 45%,var(--line));background:color-mix(in srgb,var(--warn) 8%,var(--bg2));border-radius:var(--r);padding:10px 12px",
      )}
    >
      <Eyebrow tone="var(--warn)" style="margin-bottom:6px">
        {call.error ? "Error diagnosis" : "Truncation diagnosis"}
      </Eyebrow>
      {typeof meta.stop_reason === "string" && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--ink)")}>
          Stop reason: {meta.stop_reason}
        </div>
      )}
      {typeof meta.max_tokens === "number" && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:2px")}>
          Output: {fmtTokens(call.output_tokens)} / {meta.max_tokens} max
        </div>
      )}
      {typeof meta.section_name === "string" && (
        <div style={css("font-family:var(--mono);font-size:11px;color:var(--dim);margin-top:2px")}>
          Section: {meta.section_name}
        </div>
      )}
      <div style={css("font-size:11.5px;color:var(--dim);margin-top:6px")}>{advice}</div>
    </div>
  );
}
