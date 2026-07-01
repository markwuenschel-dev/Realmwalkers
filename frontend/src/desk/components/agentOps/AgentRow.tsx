"use client";

import { useState } from "react";
import { css } from "../../css";
import { useDesk } from "../../state";
import type { AgentOpsAgentOut, AgentStatsOut } from "../../api/types";
import { AgentHealthStrip } from "./AgentHealthStrip";

const TIER_LABEL: Record<string, string> = { haiku: "Haiku", sonnet: "Sonnet", opus: "Opus" };
const TIER_HINT: Record<string, string> = {
  haiku: "fastest / cheapest",
  sonnet: "balanced",
  opus: "strongest / most expensive",
};
const QUALITY_ORDER = ["fast", "balanced", "quality"] as const;
const QUALITY_LABEL: Record<string, string> = {
  fast: "Fast",
  balanced: "Balanced",
  quality: "Quality",
};

// Brand colors for the active/selected model button. Anthropic uses the tier label (no per-model
// override needed); OpenAI and xAI need explicit labels since raw model ids don't read well.
const PROVIDER_COLOR: Record<string, string> = {
  anthropic: "#E67E51",
  openai: "#10A37F",
  xai: "#0A0A0A",
};
const MODEL_LABEL: Record<string, string> = {
  "gpt-5.4-nano": "GPT 5.4 Nano",
  "gpt-5.4-mini": "GPT 5.4 Mini",
  "gpt-5.5": "GPT 5.5",
  "grok-4.3": "Grok",
};

interface ModelOption {
  provider: string;
  tier: string;
  model: string;
  label: string;
  color: string;
}

function modelCatalog(providerTiers: Record<string, Record<string, string>>): ModelOption[] {
  const options: ModelOption[] = [];
  for (const [provider, tiers] of Object.entries(providerTiers)) {
    for (const tier of ["haiku", "sonnet", "opus"]) {
      const model = tiers[tier];
      if (!model) continue;
      options.push({
        provider,
        tier,
        model,
        label: MODEL_LABEL[model] ?? TIER_LABEL[tier] ?? model,
        color: PROVIDER_COLOR[provider] ?? "var(--accent)",
      });
    }
  }
  return options;
}

interface AgentRowProps {
  agent: AgentOpsAgentOut;
  stats: AgentStatsOut | undefined;
  busy: boolean;
  providerTiers: Record<string, Record<string, string>>;
  onPickTier: (setting: string, tier: string, provider: string) => void;
  onSetFallback: (setting: string, tier: string, provider: string) => void;
  onSetQuality: (setting: string, level: string) => void;
  onSetSemanticEscalation: (setting: string, enabled: boolean) => void;
  onSetAutoRun: (setting: string, enabled: boolean) => void;
}

export function AgentRow({
  agent,
  stats,
  busy,
  providerTiers,
  onPickTier,
  onSetFallback,
  onSetQuality,
  onSetSemanticEscalation,
  onSetAutoRun,
}: AgentRowProps) {
  const { t } = useDesk();
  const [open, setOpen] = useState(false);
  const a = agent;
  const catalog = modelCatalog(providerTiers);
  // Show the fallback as the actual model (e.g. "Grok", "GPT 5.5") when set, not the raw tier word:
  // the tier vocabulary (opus/sonnet/haiku) is provider-neutral internally but reads as Anthropic-only.
  const fbTier = a.policy.fallback_tier;
  const fbModel =
    fbTier && a.policy.fallback_provider
      ? providerTiers[a.policy.fallback_provider]?.[fbTier]
      : undefined;
  const fbLabel = fbTier ? ((fbModel && MODEL_LABEL[fbModel]) ?? TIER_LABEL[fbTier] ?? fbTier) : null;

  return (
    <div
      style={css(
        "background:var(--bg2);border:1px solid var(--line);border-radius:11px;padding:16px 18px",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={css(
          "display:flex;width:100%;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;background:transparent;border:none;padding:0;cursor:pointer;text-align:left",
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
            style={css("margin-top:6px;display:flex;flex-wrap:wrap;gap:10px;align-items:center")}
          >
            <span style={css("font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
              {a.model}
            </span>
            {a.policy.fallback_tier && (
              <span
                style={css(
                  "font-size:11px;padding:2px 8px;border-radius:6px;background:var(--bg3);color:var(--dim)",
                )}
              >
                fallback: {fbLabel}
              </span>
            )}
            <span style={css("font-size:11px;color:var(--dim)")}>
              ~${a.estimate.estimated_usd_per_chapter?.toFixed(2) ?? "—"}/ch ·{" "}
              {a.estimate.cost_band} · {a.contract.context_load}
            </span>
            {!a.permissions.auto_run && (
              <span style={css(`font-size:11px;color:${t.warn}`)}>manual only</span>
            )}
          </div>
          <div style={css("margin-top:6px")}>
            <AgentHealthStrip stats={stats} />
          </div>
        </div>
        <span style={css("color:var(--dim);font-size:12px")}>{open ? "▲" : "▼"}</span>
      </button>

      {a.warnings.length > 0 && (
        <div style={css(`margin-top:10px;font-size:12px;color:${t.warn}`)}>
          {a.warnings.map((w) => (
            <div key={w}>⚠ {w}</div>
          ))}
        </div>
      )}

      {open && (
        <div style={css("margin-top:16px;border-top:1px solid var(--line);padding-top:16px")}>
          <div style={css("display:flex;flex-wrap:wrap;gap:18px;margin-bottom:14px")}>
            <div data-testid="primary-model-picker">
              <div style={css("font-size:12px;color:var(--dim);margin-bottom:6px")}>
                Primary model
              </div>
              <ModelButtons
                options={catalog}
                active={{ provider: a.provider, tier: a.tier }}
                disabled={busy}
                onPick={(opt) => onPickTier(a.setting, opt.tier, opt.provider)}
              />
            </div>
            <div data-testid="fallback-model-picker">
              <div style={css("font-size:12px;color:var(--dim);margin-bottom:6px")}>Fallback</div>
              <ModelButtons
                options={catalog}
                active={{ provider: a.policy.fallback_provider, tier: a.policy.fallback_tier }}
                disabled={busy}
                allowEmpty
                onPickEmpty={() => onSetFallback(a.setting, "", "")}
                onPick={(opt) => onSetFallback(a.setting, opt.tier, opt.provider)}
              />
            </div>
            <div>
              <div style={css("font-size:12px;color:var(--dim);margin-bottom:6px")}>
                Quality / temperature
              </div>
              <TierButtons
                active={a.policy.quality_level}
                disabled={busy}
                onPick={(level) => onSetQuality(a.setting, level)}
                labels={QUALITY_LABEL}
                order={[...QUALITY_ORDER]}
              />
              <div style={css("font-size:11px;color:var(--dim);margin-top:4px")}>
                temp {a.contract.temperature?.toFixed(1) ?? "—"}
              </div>
            </div>
          </div>

          <div style={css("display:flex;flex-wrap:wrap;gap:16px;margin-bottom:14px")}>
            <div>
              <label
                style={css(
                  "display:flex;align-items:center;gap:8px;font-size:13px;color:var(--dim)",
                )}
              >
                <input
                  type="checkbox"
                  checked={a.policy.semantic_escalation}
                  disabled={busy}
                  onChange={(e) => onSetSemanticEscalation(a.setting, e.target.checked)}
                />
                Semantic escalation (canon conflict / high QA risk)
              </label>
              <div style={css("font-size:11px;color:var(--dim);margin-top:4px;padding-left:24px")}>
                Default on for QA/review agents; presets may override.
              </div>
            </div>
            <label
              style={css("display:flex;align-items:center;gap:8px;font-size:13px;color:var(--dim)")}
            >
              <input
                type="checkbox"
                checked={a.permissions.auto_run}
                disabled={busy}
                onChange={(e) => onSetAutoRun(a.setting, e.target.checked)}
              />
              Auto-run in pipeline
            </label>
          </div>

          <div style={css("font-size:13px;color:var(--dim);line-height:1.5")}>
            <div>
              <strong style={{ color: "var(--ink)" }}>Inputs:</strong>{" "}
              {a.contract.inputs.join(", ")}
            </div>
            <div>
              <strong style={{ color: "var(--ink)" }}>Outputs:</strong>{" "}
              {a.contract.outputs.join(", ")}
            </div>
            <div>
              <strong style={{ color: "var(--ink)" }}>Max retries:</strong> {a.contract.max_retries}{" "}
              · <strong style={{ color: "var(--ink)" }}>Approval:</strong>{" "}
              {a.contract.requires_approval ? "required" : "no"}
            </div>
            <div style={css("margin-top:8px")}>
              <strong style={{ color: "var(--ink)" }}>Escalation:</strong>{" "}
              {a.policy.escalation_rules.map((r) => r.trigger).join(", ")}
            </div>
            <div style={css("margin-top:8px")}>
              <strong style={{ color: "var(--ink)" }}>Permissions:</strong>{" "}
              {[
                a.permissions.can_modify_packet && "can propose packet",
                a.permissions.can_block_downstream && "can block",
                a.permissions.can_write_summaries && "writes summaries",
                a.permissions.require_approval && "requires approval",
              ]
                .filter(Boolean)
                .join(" · ") || "advisory only"}
            </div>
          </div>

          {stats && stats.calls > 0 && (
            <div
              style={css(
                "margin-top:12px;padding:10px 12px;background:var(--bg3);border-radius:8px;font-family:var(--mono);font-size:11px;color:var(--dim)",
              )}
            >
              Last window: {stats.calls} calls · err {((stats.error_rate ?? 0) * 100).toFixed(0)}% ·
              trunc {((stats.truncation_rate ?? 0) * 100).toFixed(0)}% · QA pass{" "}
              {stats.qa_pass_rate ?? "—"}
            </div>
          )}

          <a
            href="/telemetry"
            style={css("display:inline-block;margin-top:10px;font-size:12px;color:var(--accent)")}
          >
            Full telemetry →
          </a>
        </div>
      )}
    </div>
  );
}

function TierButtons({
  active,
  disabled,
  onPick,
  labels,
  order,
}: {
  active: string | null | undefined;
  disabled: boolean;
  onPick: (tier: string) => void;
  labels: Record<string, string>;
  order: string[];
}) {
  return (
    <div
      style={css(
        `display:flex;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px;opacity:${disabled ? ".6" : "1"}`,
      )}
    >
      {order.map((tier) => {
        const on = active === tier;
        return (
          <button
            key={tier}
            disabled={disabled}
            onClick={() => {
              if (!on) onPick(tier);
            }}
            style={css(
              `padding:5px 12px;border:none;border-radius:7px;cursor:${disabled ? "default" : "pointer"};font-family:var(--ui);font-size:12px;background:${on ? "var(--accent)" : "transparent"};color:${on ? "var(--onAccent)" : "var(--dim)"};font-weight:${on ? "600" : "400"}`,
            )}
          >
            {labels[tier] ?? tier}
          </button>
        );
      })}
    </div>
  );
}

function ModelButtons({
  options,
  active,
  disabled,
  allowEmpty,
  onPick,
  onPickEmpty,
}: {
  options: ModelOption[];
  active: { provider?: string | null; tier?: string | null };
  disabled: boolean;
  allowEmpty?: boolean;
  onPick: (option: ModelOption) => void;
  onPickEmpty?: () => void;
}) {
  const isActive = (opt: ModelOption) =>
    active.provider === opt.provider && active.tier === opt.tier;
  const noneActive = allowEmpty && !active.tier;
  return (
    <div
      style={css(
        `display:flex;flex-wrap:wrap;padding:3px;gap:2px;background:var(--bg3);border:1px solid var(--line);border-radius:9px;opacity:${disabled ? ".6" : "1"}`,
      )}
    >
      {allowEmpty && (
        <button
          disabled={disabled}
          onClick={() => onPickEmpty?.()}
          style={css(
            `padding:5px 10px;border:none;border-radius:7px;cursor:pointer;font-family:var(--ui);font-size:12px;background:${noneActive ? "var(--accent)" : "transparent"};color:${noneActive ? "var(--onAccent)" : "var(--dim)"}`,
          )}
        >
          None
        </button>
      )}
      {options.map((opt) => {
        const on = isActive(opt);
        return (
          <button
            key={opt.model}
            disabled={disabled}
            title={TIER_HINT[opt.tier] ?? opt.model}
            onClick={() => {
              if (!on) onPick(opt);
            }}
            style={css(
              `padding:5px 12px;border:none;border-radius:7px;cursor:${disabled ? "default" : "pointer"};font-family:var(--ui);font-size:12px;background:${on ? opt.color : "transparent"};color:${on ? "#FFFFFF" : "var(--dim)"};font-weight:${on ? "600" : "400"}`,
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
