"use client";

import { css } from "../../css";
import type { SmokeTestOut } from "../../api/types";
import { useDesk } from "../../state";

interface SmokeTestModalProps {
  result: SmokeTestOut | null;
  onClose: () => void;
}

export function SmokeTestModal({ result, onClose }: SmokeTestModalProps) {
  const { t } = useDesk();
  if (!result) return null;

  return (
    <div
      style={css(
        "position:fixed;inset:0;background:color-mix(in srgb,#000 45%,transparent);display:flex;align-items:center;justify-content:center;z-index:200;padding:20px",
      )}
      onClick={onClose}
    >
      <div
        style={css(
          "background:var(--bg2);border:1px solid var(--line);border-radius:12px;max-width:640px;width:100%;max-height:80vh;overflow:auto;padding:20px",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={css("display:flex;justify-content:space-between;align-items:center;margin-bottom:14px")}>
          <h2 style={css("margin:0;font-family:var(--display);font-size:18px")}>Smoke test results</h2>
          <button onClick={onClose} style={css("border:none;background:transparent;color:var(--dim);cursor:pointer")}>
            ✕
          </button>
        </div>
        <p
          style={css(
            `margin:0 0 14px;font-size:13px;color:${result.all_passed ? t.good : t.bad}`,
          )}
        >
          {result.all_passed ? "All agents passed (offline fixtures)." : "Some agents failed — see details."}
        </p>
        <div style={css("display:flex;flex-direction:column;gap:10px")}>
          {result.results.map((r) => (
            <div
              key={r.setting}
              style={css(
                `border:1px solid color-mix(in srgb,${r.passed ? t.good : t.bad} 35%,var(--line));border-radius:9px;padding:12px`,
              )}
            >
              <div style={css("font-weight:600;font-size:14px;margin-bottom:6px")}>{r.label}</div>
              <ul style={css("margin:0;padding-left:18px;font-size:12.5px;color:var(--dim)")}>
                {r.checks.map((c) => (
                  <li key={c.name} style={{ color: c.ok ? "var(--dim)" : t.bad }}>
                    {c.name}: {c.ok ? "ok" : c.detail ?? "failed"}
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
