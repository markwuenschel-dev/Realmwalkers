import type { CSSProperties } from "react";

// Three swappable themes, copied 1:1 from the prototype. Each is a flat token set that becomes the
// CSS custom properties on the root element; every screen reads them via var(--token) in css() strings.
export type ThemeId = "grimoire" | "manuscript" | "console";

export interface ThemeTokens {
  bg: string;
  bg2: string;
  bg2b: string;
  bg3: string;
  barbg: string;
  ink: string;
  dim: string;
  accent: string;
  accentSoft: string;
  accentLine: string;
  onAccent: string;
  boxbg: string;
  line: string;
  hairline: string;
  good: string;
  warn: string;
  bad: string;
  info: string;
  r: string;
  display: string;
  prose: string;
  ui: string;
  mono: string;
  shadow: string;
}

export const themes: Record<ThemeId, ThemeTokens> = {
  grimoire: {
    bg: "#0b0a0f", bg2: "#16131c", bg2b: "#1a1622", bg3: "#211c2b", barbg: "rgba(13,11,18,.82)",
    ink: "#ece3d0", dim: "#94897a", accent: "#c9a253", accentSoft: "rgba(201,162,83,.14)",
    accentLine: "rgba(201,162,83,.4)", onAccent: "#1a1306", boxbg: "#120f18",
    line: "#2a2433", hairline: "rgba(236,227,208,.07)",
    good: "#6fcf97", warn: "#e0b15a", bad: "#e1719b", info: "#7aa2f7",
    r: "9px", display: '"Newsreader",Georgia,serif', prose: '"Spectral",Georgia,serif',
    ui: '"Spectral",Georgia,serif', mono: '"Space Mono",monospace',
    shadow: "0 16px 50px rgba(0,0,0,.5)",
  },
  manuscript: {
    bg: "#f3efe6", bg2: "#fbf9f3", bg2b: "#f6f2e9", bg3: "#efe9dc", barbg: "rgba(248,245,238,.82)",
    ink: "#241f18", dim: "#8a8170", accent: "#8a2f2f", accentSoft: "rgba(138,47,47,.08)",
    accentLine: "rgba(138,47,47,.28)", onAccent: "#fff", boxbg: "#f6f1e7",
    line: "#e2dac9", hairline: "rgba(36,31,24,.07)",
    good: "#2f7d57", warn: "#9a6a1f", bad: "#a23a52", info: "#355f9e",
    r: "6px", display: '"Newsreader",Georgia,serif', prose: '"Spectral",Georgia,serif',
    ui: '"Spectral",Georgia,serif', mono: '"Space Mono",monospace',
    shadow: "0 12px 36px rgba(80,60,30,.10)",
  },
  console: {
    bg: "#0a0d12", bg2: "#10141b", bg2b: "#141922", bg3: "#19202b", barbg: "rgba(10,13,18,.82)",
    ink: "#d4dee8", dim: "#76879a", accent: "#4fd6e0", accentSoft: "rgba(79,214,224,.12)",
    accentLine: "rgba(79,214,224,.32)", onAccent: "#04181a", boxbg: "#0c1117",
    line: "#1e2731", hairline: "rgba(212,222,232,.06)",
    good: "#57d98a", warn: "#e3b341", bad: "#f0738f", info: "#6aa8ff",
    r: "4px", display: '"IBM Plex Sans",sans-serif', prose: '"Spectral",Georgia,serif',
    ui: '"IBM Plex Sans",sans-serif', mono: '"JetBrains Mono",monospace',
    shadow: "0 16px 50px rgba(0,0,0,.6)",
  },
};

// Build the root element style: every token as a --custom-property plus the base page styles.
export function themeRootStyle(t: ThemeTokens): CSSProperties {
  return {
    "--bg": t.bg, "--bg2": t.bg2, "--bg2b": t.bg2b, "--bg3": t.bg3, "--barbg": t.barbg,
    "--ink": t.ink, "--dim": t.dim, "--accent": t.accent, "--accentSoft": t.accentSoft,
    "--accentLine": t.accentLine, "--onAccent": t.onAccent, "--boxbg": t.boxbg,
    "--line": t.line, "--hairline": t.hairline, "--good": t.good, "--warn": t.warn,
    "--bad": t.bad, "--info": t.info, "--r": t.r, "--display": t.display,
    "--prose": t.prose, "--ui": t.ui, "--mono": t.mono, "--shadow": t.shadow,
    minHeight: "100vh", background: t.bg, color: t.ink,
    fontFamily: t.ui, position: "relative",
  } as CSSProperties;
}
