import type { ReactNode } from "react";
import { css } from "../css";

// A sticky action bar that floats in once one or more rows are selected. The screen supplies the
// action buttons (Approve / Request revision / Delete …) as children; this just frames the count and
// a Clear. Shared by the inbox and the ledger so bulk actions look and feel identical everywhere.
export default function BulkBar({
  count,
  noun,
  onClear,
  children,
}: {
  count: number;
  noun: string;
  onClear: () => void;
  children: ReactNode;
}) {
  if (count <= 0) return null;
  const plural = count === 1 ? noun : noun.endsWith("y") ? `${noun.slice(0, -1)}ies` : `${noun}s`;
  return (
    <div
      style={css(
        "position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:80;display:flex;align-items:center;gap:14px;max-width:min(720px,94vw);padding:11px 14px 11px 18px;background:var(--bg2);border:1px solid var(--accentLine);border-radius:12px;box-shadow:0 20px 50px rgba(0,0,0,.4);animation:fadeUp .2s ease both",
      )}
    >
      <span
        style={css("font-family:var(--mono);font-size:12px;color:var(--ink);white-space:nowrap")}
      >
        {count} {plural} selected
      </span>
      <div style={css("display:flex;align-items:center;gap:8px;flex-wrap:wrap")}>{children}</div>
      <button
        onClick={onClear}
        style={css(
          "margin-left:auto;font-family:var(--mono);font-size:11.5px;color:var(--dim);background:none;border:none;cursor:pointer;flex:none",
        )}
      >
        Clear
      </button>
    </div>
  );
}

// A standard bulk-action button. `tone` picks the accent: good (approve), bad (delete), or neutral.
export function BulkButton({
  onClick,
  disabled,
  tone = "neutral",
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  tone?: "good" | "bad" | "neutral";
  children: ReactNode;
}) {
  const v = tone === "good" ? "--good" : tone === "bad" ? "--bad" : "--accent";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={css(
        `padding:7px 13px;border-radius:8px;border:1px solid color-mix(in srgb,var(${v}) 45%,var(--line));background:color-mix(in srgb,var(${v}) 13%,var(--bg3));color:var(${v});font-family:var(--ui);font-size:12.5px;cursor:${disabled ? "default" : "pointer"};opacity:${disabled ? 0.6 : 1};white-space:nowrap`,
      )}
    >
      {children}
    </button>
  );
}
