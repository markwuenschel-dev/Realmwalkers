"use client";

import { useState } from "react";
import { css } from "../../css";
import type { AgentPresetOut, PipelineEstimateOut } from "../../api/types";
import { Button, Panel } from "../ui";

const BAND_COLOR: Record<string, string> = {
  low: "var(--good)",
  medium: "var(--warn)",
  high: "var(--bad)",
  custom: "var(--accent)",
};

interface PresetBarProps {
  presets: AgentPresetOut[];
  activePreset: string | null;
  pipeline: PipelineEstimateOut;
  busy: boolean;
  onSelectPreset: (id: string) => void;
  onDeletePreset: (id: string) => void;
  onSavePreset: (label: string) => void;
  onSmokeTest: (live: boolean) => void;
  smokeBusy: boolean;
}

export function PresetBar({
  presets,
  activePreset,
  pipeline,
  busy,
  onSelectPreset,
  onDeletePreset,
  onSavePreset,
  onSmokeTest,
  smokeBusy,
}: PresetBarProps) {
  const [liveSmoke, setLiveSmoke] = useState(false);
  const [saveLabel, setSaveLabel] = useState("");
  const active = presets.find((p) => p.id === activePreset);
  const customPresets = presets.filter((p) => p.is_custom);

  return (
    <Panel
      eyebrow="Active preset"
      title={active?.label ?? (activePreset === "custom" ? "Custom" : "Default")}
      actions={
        <>
          <label
            style={css(
              "display:flex;align-items:center;gap:6px;font-family:var(--ui);font-size:12px;color:var(--warn)",
            )}
          >
            <input
              type="checkbox"
              checked={liveSmoke}
              disabled={smokeBusy}
              onChange={(e) => setLiveSmoke(e.target.checked)}
            />
            Live API
          </label>
          <Button size="sm" disabled={smokeBusy} onClick={() => onSmokeTest(liveSmoke)}>
            {smokeBusy ? "Running…" : "Run smoke test"}
          </Button>
        </>
      }
      style="margin-bottom:18px"
    >
      <div style={css("display:flex;flex-wrap:wrap;gap:8px;align-items:center")}>
        {presets.map((p) => {
          const on = activePreset === p.id;
          return (
            <span key={p.id} style={css("display:inline-flex;align-items:center;gap:2px")}>
              <Button
                size="sm"
                variant={on ? "primary" : "secondary"}
                disabled={busy}
                onClick={() => {
                  if (!on) onSelectPreset(p.id);
                }}
              >
                {p.label}
                {p.is_custom ? " ★" : ""}
              </Button>
              {p.is_custom && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  title="Delete saved preset"
                  onClick={() => onDeletePreset(p.id)}
                >
                  ✕
                </Button>
              )}
            </span>
          );
        })}
        <span style={css("display:inline-flex;align-items:center;gap:8px;margin-left:auto")}>
          <input
            type="text"
            placeholder="Save as…"
            value={saveLabel}
            disabled={busy}
            onChange={(e) => setSaveLabel(e.target.value)}
            style={css(
              "padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg3);color:var(--ink);font-size:12.5px;width:120px",
            )}
          />
          <Button
            size="sm"
            disabled={busy || !saveLabel.trim()}
            onClick={() => {
              onSavePreset(saveLabel.trim());
              setSaveLabel("");
            }}
          >
            Save preset
          </Button>
        </span>
      </div>
      {liveSmoke && (
        <div style={css("margin-top:10px;font-size:12px;color:var(--warn)")}>
          Live mode spends real API credits (small ping per agent, or full fixture path for QA
          agents). Offline mode is free.
        </div>
      )}
      {customPresets.length > 0 && (
        <div style={css("margin-top:8px;font-size:11.5px;color:var(--dim)")}>
          {customPresets.length} saved custom preset{customPresets.length === 1 ? "" : "s"}
        </div>
      )}
      <div
        style={css(
          "margin-top:14px;padding-top:12px;border-top:1px solid var(--hairline);display:flex;flex-wrap:wrap;gap:16px;font-size:13px;color:var(--dim)",
        )}
      >
        <span>
          Est. cost:{" "}
          <strong style={{ color: BAND_COLOR[pipeline.cost_band] ?? "var(--ink)" }}>
            {pipeline.estimated_usd_per_chapter != null
              ? `$${pipeline.estimated_usd_per_chapter.toFixed(2)}/ch`
              : pipeline.cost_band}
          </strong>
          {pipeline.estimated_usd_low_per_chapter != null && (
            <span style={{ color: "var(--dim)", fontWeight: 400 }}>
              {" "}
              (as low as ${pipeline.estimated_usd_low_per_chapter.toFixed(2)} with cache)
            </span>
          )}
        </span>
        <span>
          Est. latency:{" "}
          <strong style={{ color: "var(--ink)" }}>
            {pipeline.estimated_latency_sec_per_chapter != null
              ? `~${Math.round(pipeline.estimated_latency_sec_per_chapter / 60)}m`
              : pipeline.latency_band}
          </strong>
        </span>
        <span>{active?.best_for ?? pipeline.summary}</span>
      </div>
      <div style={css("margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
        {pipeline.summary} · {pipeline.opus_calls} top-tier · {pipeline.sonnet_calls} mid-tier ·{" "}
        {pipeline.haiku_calls} fast-tier calls (est.)
      </div>
      <div style={css("margin-top:6px;font-size:11.5px;color:var(--dim)")}>
        Estimated from current tiers (not historical spend)
      </div>
    </Panel>
  );
}
