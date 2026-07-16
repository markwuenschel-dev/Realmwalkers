"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../../css";
import { api } from "../../api/client";
import type { AutonomyOut } from "../../api/types";
import { Button, Chip, Eyebrow } from "../ui";

// Autonomous self-repair sweeper controls. The kill switch + authority ceiling are the guardrails:
// the sweeper drives stalled runs forward but never auto-approves ABOVE the ceiling. human_required is
// NOT a ceiling option — it is a manual-grant requirement that always needs an explicit human "Approve
// & apply" (ADR-0031 D16). Persisted as KV settings, read live on the next tick.
const CEILINGS = [
  "span_only",
  "scene_local",
  "scene_structural",
  "cross_scene",
  "chapter_structural",
];

const FIELD =
  "background:var(--bg3);border:1px solid var(--line);border-radius:8px;color:var(--ink);font-size:13px;padding:7px 9px;font-family:var(--ui)";
const LABEL = "font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-bottom:4px";

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <label style={css("display:flex;flex-direction:column")}>
      <span style={css(LABEL)}>{label}</span>
      <input
        type="number"
        value={value}
        min={0}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
        style={css(`${FIELD};width:110px`)}
      />
    </label>
  );
}

export function AutonomyPanel() {
  const [form, setForm] = useState<AutonomyOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      setForm(await api.autonomy());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!form) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const next = await api.setAutonomy({
        autonomy_enabled: form.autonomy_enabled,
        interval_s: form.interval_s,
        stale_window_s: form.stale_window_s,
        authority_ceiling: form.authority_ceiling,
        max_attempts: form.max_attempts,
        retention_days: form.retention_days,
      });
      setForm(next);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof AutonomyOut>(key: K, value: AutonomyOut[K]) => {
    setSaved(false);
    setForm((f) => (f ? { ...f, [key]: value } : f));
  };

  if (!form) return null;

  return (
    <div
      style={css(
        "max-width:920px;margin:0 0 18px;border:1px solid var(--line);border-radius:var(--rLg);background:var(--bg2);padding:16px 18px",
      )}
    >
      <div style={css("display:flex;align-items:center;gap:10px;margin-bottom:12px")}>
        <Eyebrow>Autonomous self-repair</Eyebrow>
        <Chip
          label={form.autonomy_enabled ? "on" : "off"}
          tone={form.autonomy_enabled ? "good" : "warn"}
          size="sm"
        />
        <div style={css("flex:1")} />
        <Button
          size="sm"
          variant={form.autonomy_enabled ? "ghost" : "primary"}
          onClick={() => set("autonomy_enabled", !form.autonomy_enabled)}
        >
          {form.autonomy_enabled ? "Turn off" : "Turn on"}
        </Button>
      </div>
      <p style={css("margin:0 0 14px;color:var(--dim);font-size:13px;line-height:1.5")}>
        When on, the sweeper finds stalled production runs and drives repair without clicks —
        auto-approving repairs up to the authority ceiling and auto-verifying once revisions land.
        <b>human required</b> repairs are never auto-approved — they always wait for an explicit
        Approve &amp; apply. Everything it does appears in the Activity tab and stays rollbackable.
      </p>
      <div style={css("display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end")}>
        <label style={css("display:flex;flex-direction:column")}>
          <span style={css(LABEL)}>Authority ceiling</span>
          <select
            value={form.authority_ceiling}
            onChange={(e) => set("authority_ceiling", e.target.value)}
            style={css(`${FIELD};min-width:170px`)}
          >
            {CEILINGS.map((c) => (
              <option key={c} value={c}>
                {c.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <NumberField
          label="Sweep interval (s)"
          value={form.interval_s}
          onChange={(n) => set("interval_s", n)}
        />
        <NumberField
          label="Stale window (s)"
          value={form.stale_window_s}
          onChange={(n) => set("stale_window_s", n)}
        />
        <NumberField
          label="Max attempts / run"
          value={form.max_attempts}
          onChange={(n) => set("max_attempts", n)}
        />
        <NumberField
          label="Retention (days)"
          value={form.retention_days}
          onChange={(n) => set("retention_days", n)}
        />
        <Button size="sm" variant="primary" disabled={busy} onClick={() => void save()}>
          {busy ? "Saving…" : "Save"}
        </Button>
        {saved && <Chip label="saved" tone="good" size="sm" />}
      </div>
      {error && <div style={css("margin-top:10px;color:var(--bad);font-size:12.5px")}>{error}</div>}
    </div>
  );
}
