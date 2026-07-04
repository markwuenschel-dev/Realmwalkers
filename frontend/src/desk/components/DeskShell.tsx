"use client";

import type { ReactNode } from "react";
import { css } from "../css";
import { useDesk } from "../state";
import { themeRootStyle } from "../theme";
import TopBar from "./TopBar";
import CommandPalette from "./CommandPalette";
import DecisionToast from "./DecisionToast";
import ErrorToast, { BackendBanner } from "./ErrorToast";

// The persistent desk chrome: themed root, ambient background overlays, TopBar, and the global
// overlays (command palette, decision/error toasts, backend-unreachable banner). The active route
// renders into <main> as `children` — the shell itself never switches pages.
export default function DeskShell({ children }: { children: ReactNode }) {
  const { t, themeId, isDark, paletteOpen, decision } = useDesk();
  return (
    <div style={themeRootStyle(t, themeId)}>
      {/* One ambient overlay per variant — Ink gets a faint gilt glow from above; Vellum a
          near-invisible paper gradient. No noise, no images (Atelier texture restraint). */}
      <div
        style={css(
          `position:fixed;inset:0;pointer-events:none;z-index:0;background:${
            isDark
              ? "radial-gradient(120% 90% at 50% -10%, rgba(200,163,90,.05), transparent 55%)"
              : "linear-gradient(180deg, rgba(255,252,244,.55), transparent 240px)"
          }`,
        )}
      />

      <TopBar />

      <main
        style={css(
          "position:relative;z-index:1;max-width:1480px;margin:0 auto;padding:36px 32px 96px",
        )}
      >
        {children}
      </main>

      {paletteOpen && <CommandPalette />}
      {decision && <DecisionToast />}
      <BackendBanner />
      <ErrorToast />
    </div>
  );
}
