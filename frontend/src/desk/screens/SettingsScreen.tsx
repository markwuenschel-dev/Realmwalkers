"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import type { ModelSettingsOut } from "../api/types";

// Per-agent model selection. Every agent reads `settings.<role>_model`; this screen flips that at
// runtime (Haiku / Sonnet / Opus) — applied to the live server and persisted, no redeploy.

const TIER_LABEL: Record<string, string> = { haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };
const TIER_ORDER = ["haiku", "sonnet", "opus"];
const TIER_HINT: Record<string, string> = {
  haiku: "fastest / cheapest",
  sonnet: "balanced",
  opus: "strongest / most expensive",
};

export default function SettingsScreen() {
  const { t } = useDesk();
  const [data, setData] = useState<ModelSettingsOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.modelSettings());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const pick = async (setting: string, tier: string) => {
    setBusy(setting);
    setError(null);
    try {
      const updated = await api.setModel(setting, tier);
      setData((d) =>
        d ? { ...d, agents: d.agents.map((a) => (a.setting === setting ? updated : a)) } : d,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <div style={css("margin-bottom:22px")}>
        <h1
          style={css(
            "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)",
          )}
        >
          Agent models
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px;max-width:680px")}>
          Pick the Claude model each agent runs on. Changes apply to the next call — drafting,
          reviewing, packets — and persist across restarts. The Oracle is deterministic (hard
          numbers, no model).
        </p>
      </div>

      {error && (
        <div
          style={css(
            `margin-bottom:16px;border:1px solid color-mix(in srgb,${t.bad} 40%,var(--line));background:color-mix(in srgb,${t.bad} 8%,var(--bg2));border-radius:9px;padding:11px 13px;color:${t.bad};font-size:13px`,
          )}
        >
          {error}
        </div>
      )}

      {!data && !error && (
        <div style={css("font-family:var(--mono);font-size:12px;color:var(--dim)")}>Loading…</div>
      )}

      <div style={css("display:flex;flex-direction:column;gap:12px;max-width:880px")}>
        {data?.agents.map((a) => (
          <div
            key={a.setting}
            style={css(
              "display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;background:var(--bg2);border:1px solid var(--line);border-radius:11px;padding:16px 18px",
            )}
          >
            <div style={css("flex:1 1 auto;min-width:0")}>
              <div style={css("font-family:var(--display);font-size:16px;color:var(--ink)")}>
                {a.label}
              </div>
              <div style={css("font-size:13px;color:var(--dim);margin-top:3px;line-height:1.45")}>
                {a.description}
              </div>
              <div
                style={css(
                  "font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:5px",
                )}
              >
                {a.model}
              </div>
            </div>
            <div
              style={css(
                `display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px;flex:none;opacity:${busy === a.setting ? ".6" : "1"}`,
              )}
            >
              {TIER_ORDER.map((tier) => {
                const on = a.tier === tier;
                return (
                  <button
                    key={tier}
                    disabled={busy === a.setting}
                    title={TIER_HINT[tier]}
                    onClick={() => {
                      if (!on) void pick(a.setting, tier);
                    }}
                    style={css(
                      `padding:6px 14px;border:none;border-radius:7px;cursor:${busy === a.setting ? "default" : "pointer"};font-family:var(--ui);font-size:12.5px;background:${on ? "var(--accent)" : "transparent"};color:${on ? "var(--onAccent)" : "var(--dim)"};font-weight:${on ? "600" : "400"}`,
                    )}
                  >
                    {TIER_LABEL[tier]}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
