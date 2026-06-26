// Single source of truth for desk navigation. TopBar, CommandPalette, and the global keyboard
// shortcuts all read from here — there is no second copy of the nav list anywhere. Durable page
// identity lives in the URL (`href`); the in-app `screen` string is gone.
import type { Screen } from "./types";

export interface DeskRoute {
  id: Screen; // stable id
  label: string; // TopBar label
  paletteLabel: string; // Command palette "Go" command label
  href: string; // canonical route
  key?: string; // second key of the `g _` chord (e.g. "i" → g i); omitted = no chord
  shortcut?: string; // human-readable chord, e.g. "G I"
  icon: string;
  nav: boolean; // shown in the TopBar nav row
}

export const DESK_ROUTES: readonly DeskRoute[] = [
  {
    id: "inbox",
    label: "Inbox",
    paletteLabel: "Go to Inbox",
    href: "/inbox",
    key: "i",
    shortcut: "G I",
    icon: "◧",
    nav: true,
  },
  {
    id: "scene",
    label: "Scene",
    paletteLabel: "Open Scene · review queue",
    href: "/scene",
    key: "s",
    shortcut: "G S",
    icon: "❖",
    nav: true,
  },
  {
    id: "chapters",
    label: "Chapters",
    paletteLabel: "Open Chapter board & progress",
    href: "/chapters",
    key: "c",
    shortcut: "G C",
    icon: "▦",
    nav: true,
  },
  {
    id: "packets",
    label: "Packets",
    paletteLabel: "Open Knowledge packets",
    href: "/packets",
    key: "p",
    shortcut: "G P",
    icon: "▧",
    nav: true,
  },
  {
    id: "diff",
    label: "Versions",
    paletteLabel: "Compare versions",
    href: "/diff",
    key: "v",
    shortcut: "G V",
    icon: "⇄",
    nav: true,
  },
  {
    id: "manuscript",
    label: "Manuscript",
    paletteLabel: "Open Manuscript",
    href: "/manuscript",
    key: "m",
    shortcut: "G M",
    icon: "❡",
    nav: true,
  },
  {
    id: "ledger",
    label: "Ledger",
    paletteLabel: "Open World ledger",
    href: "/ledger",
    key: "l",
    shortcut: "G L",
    icon: "◍",
    nav: true,
  },
  {
    id: "docs",
    label: "Canon",
    paletteLabel: "Open Canon docs",
    href: "/docs",
    key: "d",
    shortcut: "G D",
    icon: "❡",
    nav: true,
  },
  {
    id: "settings",
    label: "Models",
    paletteLabel: "Open Model settings",
    href: "/settings",
    icon: "⚙",
    nav: true,
  },
] as const;

// g-chord key → href (e.g. pressing "g" then "i" navigates to /inbox). Routes without a key (e.g.
// settings) are reachable via nav/palette only, matching the prototype's shortcut set.
export const CHORD_TO_HREF: Record<string, string> = Object.fromEntries(
  DESK_ROUTES.flatMap((r) => (r.key ? [[r.key, r.href] as const] : [])),
);

// Which nav entry owns the current pathname. The longest matching href wins so that, e.g.,
// /scene/<id> still highlights "Scene" and /diff/<id> still highlights "Versions".
export function activeRouteId(pathname: string): Screen | null {
  let best: DeskRoute | null = null;
  for (const r of DESK_ROUTES) {
    if (pathname === r.href || pathname.startsWith(`${r.href}/`)) {
      if (!best || r.href.length > best.href.length) best = r;
    }
  }
  return best?.id ?? null;
}
