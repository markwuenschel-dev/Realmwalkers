"use client";

import { css } from "../../css";
import type { SmokeTestOut } from "../../api/types";
import { Button, Chip, Eyebrow } from "../ui";

interface SmokeTestModalProps {
  result: SmokeTestOut | null;
  onClose: () => void;
}

export function SmokeTestModal({ result, onClose }: SmokeTestModalProps) {
  if (!result) return null;

  const live = result.mode === "live";

  return (
    <div
      style={css(
        "position:fixed;inset:0;background:var(--scrim);display:flex;align-items:center;justify-content:center;z-index:200;padding:20px;animation:scrimIn var(--dur) var(--ease)",
      )}
      onClick={onClose}
    >
      <div
        style={css(
          "background:var(--bg2);border:1px solid var(--line);border-radius:var(--rLg);box-shadow:var(--shadow);max-width:640px;width:100%;max-height:80vh;overflow:auto;padding:22px 24px;animation:fadeUp var(--dur) var(--ease-out)",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={css(
            "display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px",
          )}
        >
          <div>
            <Eyebrow>Smoke test · {result.mode}</Eyebrow>
            <h2
              style={css(
                "margin:4px 0 0;font-family:var(--display);font-weight:500;font-size:21px;line-height:28px;color:var(--ink)",
              )}
            >
              Smoke test results
            </h2>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} title="Close">
            ✕
          </Button>
        </div>
        {result.live_warning && (
          <p style={css("margin:0 0 10px;font-size:12.5px;color:var(--warn)")}>
            {result.live_warning}
          </p>
        )}
        {live && result.estimated_cost_usd != null && (
          <p style={css("margin:0 0 10px;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
            Est. ${result.estimated_cost_usd.toFixed(4)}
            {result.actual_cost_usd != null && ` · actual ~$${result.actual_cost_usd.toFixed(4)}`}
          </p>
        )}
        <p
          style={css(
            `margin:0 0 14px;font-size:13px;color:${result.all_passed ? "var(--good)" : "var(--bad)"}`,
          )}
        >
          {result.all_passed
            ? `All agents passed (${live ? "live API" : "offline fixtures"}).`
            : "Some agents failed — see details."}
        </p>
        <div style={css("display:flex;flex-direction:column;gap:10px")}>
          {result.results.map((r) => (
            <div
              key={r.setting}
              style={css(
                `border:1px solid color-mix(in srgb,${r.passed ? "var(--good)" : "var(--bad)"} 35%,var(--line));border-radius:var(--r);padding:12px`,
              )}
            >
              <div style={css("display:flex;align-items:center;gap:8px;margin-bottom:6px")}>
                <span style={css("font-weight:600;font-size:14px;color:var(--ink)")}>
                  {r.label}
                </span>
                <Chip
                  size="sm"
                  tone={r.passed ? "good" : "bad"}
                  label={r.passed ? "pass" : "fail"}
                />
              </div>
              <ul style={css("margin:0;padding-left:18px;font-size:12.5px;color:var(--dim)")}>
                {r.checks.map((c) => (
                  <li key={c.name} style={{ color: c.ok ? "var(--dim)" : "var(--bad)" }}>
                    {c.name}: {c.ok ? "ok" : (c.detail ?? "failed")}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
