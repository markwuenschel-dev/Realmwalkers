"use client";

import { useCallback, useEffect, useState } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { api } from "../api/client";
import type { AgentOpsOut, AgentStatsListOut, SmokeTestOut } from "../api/types";
import { PresetBar } from "../components/agentOps/PresetBar";
import { BudgetControls } from "../components/agentOps/BudgetControls";
import { AgentRow } from "../components/agentOps/AgentRow";
import { ProviderCards } from "../components/agentOps/ProviderCards";
import { SmokeTestModal } from "../components/agentOps/SmokeTestModal";

export default function SettingsScreen() {
  const { t } = useDesk();
  const [data, setData] = useState<AgentOpsOut | null>(null);
  const [stats, setStats] = useState<AgentStatsListOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [presetBusy, setPresetBusy] = useState(false);
  const [smokeBusy, setSmokeBusy] = useState(false);
  const [smokeResult, setSmokeResult] = useState<SmokeTestOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [ops, st] = await Promise.all([api.agentOps(), api.agentStats()]);
      setData(ops);
      setStats(st);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshStats = useCallback(async () => {
    try {
      const st = await api.agentStats();
      setStats(st);
    } catch {
      // keep prior stats on refresh failure
    }
  }, []);

  const pickTier = async (setting: string, tier: string, provider: string) => {
    setBusy(setting);
    setError(null);
    try {
      await api.setModel(setting, tier, provider);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const setFallback = async (setting: string, tier: string) => {
    setBusy(setting);
    setError(null);
    try {
      const updated = await api.setAgentPolicy(setting, { fallback_tier: tier || null });
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const patchPolicy = async (setting: string, patch: Parameters<typeof api.setAgentPolicy>[1]) => {
    setBusy(setting);
    setError(null);
    try {
      const updated = await api.setAgentPolicy(setting, patch);
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const setQuality = (setting: string, level: string) =>
    void patchPolicy(setting, { quality_level: level });

  const setSemanticEscalation = (setting: string, enabled: boolean) =>
    void patchPolicy(setting, { semantic_escalation: enabled });

  const setAutoRun = (setting: string, enabled: boolean) =>
    void patchPolicy(setting, { permissions: { auto_run: enabled } });

  const applyPreset = async (presetId: string) => {
    setPresetBusy(true);
    setError(null);
    try {
      const updated = await api.applyPreset(presetId);
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPresetBusy(false);
    }
  };

  const runSmoke = async (live: boolean) => {
    setSmokeBusy(true);
    setError(null);
    try {
      const result = await api.smokeTest({ live });
      setSmokeResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSmokeBusy(false);
    }
  };

  const savePreset = async (label: string) => {
    setPresetBusy(true);
    setError(null);
    try {
      const updated = await api.saveCustomPreset(label);
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPresetBusy(false);
    }
  };

  const deletePreset = async (presetId: string) => {
    setPresetBusy(true);
    setError(null);
    try {
      const updated = await api.deleteCustomPreset(presetId);
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPresetBusy(false);
    }
  };

  const saveGlobals = async (patch: {
    scene_token_budget?: number;
    scene_time_budget_s?: number;
  }) => {
    setPresetBusy(true);
    setError(null);
    try {
      const updated = await api.setAgentGlobals(patch);
      setData(updated);
      await refreshStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPresetBusy(false);
    }
  };

  const statsBySetting = Object.fromEntries((stats?.agents ?? []).map((s) => [s.setting, s]));

  return (
    <div>
      <div style={css("margin-bottom:22px")}>
        <h1
          style={css(
            "margin:0 0 6px;font-family:var(--display);font-weight:600;font-size:30px;color:var(--ink)",
          )}
        >
          Agent operations
        </h1>
        <p style={css("margin:0;color:var(--dim);font-size:14.5px;max-width:720px")}>
          Define quality, cost, and escalation behavior per agent — not just which model runs.
          Changes apply on the next call and persist across restarts. Oracle is deterministic (no
          model).
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

      {data && (
        <>
          <PresetBar
            presets={data.presets}
            activePreset={data.active_preset}
            pipeline={data.pipeline_estimate}
            busy={presetBusy}
            onSelectPreset={applyPreset}
            onDeletePreset={deletePreset}
            onSavePreset={savePreset}
            onSmokeTest={runSmoke}
            smokeBusy={smokeBusy}
          />
          <BudgetControls globals={data.globals} busy={presetBusy} onSave={saveGlobals} />
          <div style={css("display:flex;flex-direction:column;gap:12px;max-width:920px")}>
            {data.agents.map((a) => (
              <AgentRow
                key={a.setting}
                agent={a}
                stats={statsBySetting[a.setting]}
                busy={busy === a.setting}
                providerTiers={data.provider_tiers ?? {}}
                providers={data.providers}
                onPickTier={pickTier}
                onSetFallback={setFallback}
                onSetQuality={setQuality}
                onSetSemanticEscalation={setSemanticEscalation}
                onSetAutoRun={setAutoRun}
              />
            ))}
          </div>
          <ProviderCards providers={data.providers} />
        </>
      )}

      <SmokeTestModal result={smokeResult} onClose={() => setSmokeResult(null)} />
    </div>
  );
}
