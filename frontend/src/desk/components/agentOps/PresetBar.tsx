"use client";

import { css } from "../../css";
import { useDesk } from "../../state";
import type { AgentPresetOut, PipelineEstimateOut } from "../../api/types";

const BAND_COLOR: Record<string, string> = {
  low: "var(--good)",
  medium: "var(--warn)",
  high: "var(--bad)",
};

interface PresetBarProps {
  presets: AgentPresetOut[];
  activePreset: string | null;
  pipeline: PipelineEstimateOut;
  busy: boolean;
  onSelectPreset: (id: string) => void;
  onSmokeTest: () => void;
  smokeBusy: boolean;
}

export function PresetBar({
  presets,
  activePreset,
  pipeline,
  busy,
  onSelectPreset,
  onSmokeTest,
  smokeBusy,
}: PresetBarProps) {
  const { t } = useDesk();
  const active = presets.find((p) => p.id === activePreset);

  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:18px",
      )}
    >
      <div style={css("display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between")}>
        <div>
          <div style={css("font-family:var(--display);font-size:13px;color:var(--dim);margin-bottom:4px")}>
            Active preset
          </div>
          <div style={css("font-family:var(--display);font-size:20px;font-weight:600;color:var(--ink)")}>
            {active?.label ?? (activePreset === "custom" ? "Custom" : "Default")}
          </div>
        </div>
        <div style={css("display:flex;flex-wrap:wrap;gap:8px")}>
          {presets.map((p) => {
            const on = activePreset === p.id;
            return (
              <button
                key={p.id}
                disabled={busy}
                onClick={() => {
                  if (!on) onSelectPreset(p.id);
                }}
                style={css(
                  `padding:7px 12px;border-radius:8px;border:1px solid ${on ? "var(--accent)" : "var(--line)"};background:${on ? "color-mix(in srgb,var(--accent) 12%,var(--bg2))" : "var(--bg3)"};color:${on ? "var(--accent)" : "var(--dim)"};font-family:var(--ui);font-size:12.5px;cursor:${busy ? "default" : "pointer"};font-weight:${on ? "600" : "400"}`,
                )}
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <button
          disabled={smokeBusy}
          onClick={onSmokeTest}
          style={css(
            `padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-family:var(--ui);font-size:12.5px;cursor:${smokeBusy ? "default" : "pointer"}`,
          )}
        >
          {smokeBusy ? "Running smoke test…" : "Run smoke test"}
        </button>
      </div>
      <div style={css("margin-top:14px;display:flex;flex-wrap:wrap;gap:16px;font-size:13px;color:var(--dim)")}>
        <span>
          Est. cost:{" "}
          <strong style={{ color: BAND_COLOR[pipeline.cost_band] ?? "var(--ink)" }}>{pipeline.cost_band}</strong>
        </span>
        <span>
          Est. latency: <strong style={{ color: "var(--ink)" }}>{pipeline.latency_band}</strong>
        </span>
        <span>{active?.best_for ?? pipeline.summary}</span>
      </div>
      <div style={css("margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
        {pipeline.summary} · {pipeline.opus_calls} Opus · {pipeline.sonnet_calls} Sonnet ·{" "}
        {pipeline.haiku_calls} Haiku calls (est.)
      </div>
    </div>
  );
}
