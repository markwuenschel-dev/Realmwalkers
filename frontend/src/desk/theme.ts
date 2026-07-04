import type { CSSProperties } from "react";

// Atelier — the Desk's single visual identity (elegant literary studio), in two variants:
// "dark" (Ink — night study) and "light" (Vellum — parchment page). Each is a flat token set that
// becomes the CSS custom properties on the desk root; every screen reads them via var(--token) in
// css() strings.
//
// Surface ladder (do NOT add parallel --surface* vars — these five ARE the ladder):
//   --bg     page background
//   --bg2    card / raised paper
//   --bg2b   top bar surface
//   --bg3    recessed wells / controls
//   --boxbg  inset data well (code, JSON, mono tables)
export type ThemeId = "dark" | "light";

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
  // Oxblood editorial secondary — revision states, "editor's pen" accents, selected prose marks.
  // Never competes with gold for primary emphasis.
  accent2: string;
  boxbg: string;
  line: string;
  hairline: string;
  good: string;
  warn: string;
  bad: string;
  info: string;
  r: string;
  rLg: string;
  display: string;
  prose: string;
  ui: string;
  mono: string;
  // Elevation tiers: shadow1 = card hover, shadow2 = popover, shadow = drawer/modal.
  shadow1: string;
  shadow2: string;
  shadow: string;
  scrim: string;
}

export const themes: Record<ThemeId, ThemeTokens> = {
  // Ink — warm umber-black night study. Gold reads as gilt edges, not neon.
  dark: {
    bg: "#16120c",
    bg2: "#1e1912",
    bg2b: "#211c14",
    bg3: "#292217",
    barbg: "rgba(22,18,12,.85)",
    ink: "#ede4d3",
    dim: "#998d78",
    accent: "#c8a35a",
    accentSoft: "rgba(200,163,90,.13)",
    accentLine: "rgba(200,163,90,.38)",
    onAccent: "#211807",
    accent2: "#b56d6d",
    boxbg: "#1a1610",
    line: "#332b1e",
    hairline: "rgba(237,228,211,.07)",
    good: "#79b98f",
    warn: "#d5aa54",
    bad: "#d4798a",
    info: "#8ba7d6",
    r: "8px",
    rLg: "14px",
    display: '"Newsreader",Georgia,serif',
    prose: '"Spectral",Georgia,serif',
    ui: '"Spectral",Georgia,serif',
    mono: '"Space Mono",monospace',
    shadow1: "0 3px 12px rgba(0,0,0,.35)",
    shadow2: "0 6px 24px rgba(0,0,0,.42)",
    shadow: "0 2px 6px rgba(0,0,0,.4), 0 22px 56px rgba(0,0,0,.5)",
    scrim: "rgba(8,6,3,.55)",
  },
  // Vellum — warm parchment page. Burnished gold clears 4.5:1 on bg2 for small text;
  // NEVER use the dark variant's gold (#c8a35a) as text on these surfaces.
  light: {
    bg: "#f5f1e8",
    bg2: "#fcfaf4",
    bg2b: "#f8f4ec",
    bg3: "#efe8d9",
    barbg: "rgba(250,247,240,.85)",
    ink: "#262019",
    dim: "#847a66",
    accent: "#8a6a24",
    accentSoft: "rgba(138,106,36,.10)",
    accentLine: "rgba(138,106,36,.34)",
    onAccent: "#fffaf0",
    accent2: "#7d3b3b",
    boxbg: "#f9f5eb",
    line: "#e3dbc8",
    hairline: "rgba(38,32,25,.08)",
    good: "#33734f",
    warn: "#96691c",
    bad: "#a03b48",
    info: "#44618f",
    r: "8px",
    rLg: "14px",
    display: '"Newsreader",Georgia,serif',
    prose: '"Spectral",Georgia,serif',
    ui: '"Spectral",Georgia,serif',
    mono: '"Space Mono",monospace',
    shadow1: "0 2px 10px rgba(60,45,20,.08)",
    shadow2: "0 5px 20px rgba(60,45,20,.10)",
    shadow: "0 2px 6px rgba(60,45,20,.05), 0 18px 44px rgba(60,45,20,.11)",
    scrim: "rgba(38,32,22,.30)",
  },
};

// Build the root element style: every token as a --custom-property plus the base page styles.
export function themeRootStyle(t: ThemeTokens, id: ThemeId): CSSProperties {
  return {
    "--bg": t.bg,
    "--bg2": t.bg2,
    "--bg2b": t.bg2b,
    "--bg3": t.bg3,
    "--barbg": t.barbg,
    "--ink": t.ink,
    "--dim": t.dim,
    "--accent": t.accent,
    "--accentSoft": t.accentSoft,
    "--accentLine": t.accentLine,
    "--onAccent": t.onAccent,
    "--accent2": t.accent2,
    "--boxbg": t.boxbg,
    "--line": t.line,
    "--hairline": t.hairline,
    "--good": t.good,
    // --ok is a permanent alias of --good: some components (e.g. CacheBadge) grew up on --ok while
    // the themes only defined --good; define both forever so neither spelling silently breaks.
    "--ok": t.good,
    "--warn": t.warn,
    "--bad": t.bad,
    "--info": t.info,
    "--r": t.r,
    "--rLg": t.rLg,
    "--display": t.display,
    "--prose": t.prose,
    "--ui": t.ui,
    "--mono": t.mono,
    "--shadow1": t.shadow1,
    "--shadow2": t.shadow2,
    "--shadow": t.shadow,
    "--scrim": t.scrim,
    minHeight: "100vh",
    background: t.bg,
    color: t.ink,
    fontFamily: t.ui,
    position: "relative",
    // Native scrollbars, form controls, and UA widgets follow the active variant.
    colorScheme: id,
  } as CSSProperties;
}
