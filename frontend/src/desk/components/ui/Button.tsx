import type { ReactNode } from "react";
import { css } from "../../css";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

/** The Atelier button. Consolidates the ad-hoc `btn()` helpers (PacketsScreen,
 *  ScenePacketsPanel, LedgerScreen). Hover/focus/active live on .dk-btn in globals.css. */
export default function Button({
  children,
  onClick,
  variant = "secondary",
  size = "md",
  disabled = false,
  title,
  type = "button",
  style = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
  style?: string;
}) {
  const height = size === "sm" ? "30px" : "34px";
  const padX = size === "sm" ? "12px" : "14px";
  const font = `font-family:var(--ui);font-size:${size === "sm" ? "12px" : "12.5px"};font-weight:500`;
  const look = {
    primary: "border:1px solid transparent;background:var(--accent);color:var(--onAccent)",
    secondary: "border:1px solid var(--line);background:var(--bg3);color:var(--ink)",
    ghost: "border:1px solid transparent;background:transparent;color:var(--dim)",
    danger:
      "border:1px solid color-mix(in srgb,var(--bad) 45%,var(--line));background:color-mix(in srgb,var(--bad) 10%,var(--bg3));color:var(--bad)",
  }[variant];
  return (
    <button
      className="dk-btn"
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={css(
        `display:inline-flex;align-items:center;justify-content:center;gap:7px;height:${height};padding:0 ${padX};border-radius:9px;${look};${font};cursor:${disabled ? "default" : "pointer"};opacity:${disabled ? ".55" : "1"};white-space:nowrap;${style}`,
      )}
    >
      {children}
    </button>
  );
}
