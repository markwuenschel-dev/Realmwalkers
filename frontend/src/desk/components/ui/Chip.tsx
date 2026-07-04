import { css } from "../../css";

export type ChipTone = "neutral" | "good" | "warn" | "bad" | "info" | "accent" | "accent2";

const TONE_VAR: Record<ChipTone, string> = {
  neutral: "--dim",
  good: "--good",
  warn: "--warn",
  bad: "--bad",
  info: "--info",
  accent: "--accent",
  accent2: "--accent2",
};

/** The Atelier status chip. Consolidates the two identical local `Chip`s (PacketsScreen /
 *  ScenePacketsPanel) — their `colorVar` prop is still accepted so call sites migrate 1:1;
 *  new code prefers the semantic `tone`. `onClick` renders a real button with .dk-chip hover. */
export default function Chip({
  label,
  tone = "neutral",
  colorVar,
  size = "md",
  title,
  onClick,
}: {
  label: string;
  tone?: ChipTone;
  /** Legacy escape hatch: a raw var name like "--good" (from the pre-Atelier Chips). */
  colorVar?: string;
  size?: "sm" | "md";
  title?: string;
  onClick?: () => void;
}) {
  const v = colorVar ?? TONE_VAR[tone];
  const base = css(
    `display:inline-flex;align-items:center;gap:5px;font-family:var(--mono);font-size:${size === "sm" ? "9.5px" : "10.5px"};letter-spacing:.05em;text-transform:uppercase;color:var(${v});border:1px solid color-mix(in srgb,var(${v}) 38%,var(--line));background:color-mix(in srgb,var(${v}) 7%,transparent);border-radius:999px;padding:${size === "sm" ? "1px 7px" : "2.5px 9px"};white-space:nowrap;${onClick ? "cursor:pointer" : ""}`,
  );
  if (onClick) {
    return (
      <button className="dk-chip" onClick={onClick} title={title} style={base}>
        {label}
      </button>
    );
  }
  return (
    <span title={title} style={base}>
      {label}
    </span>
  );
}
