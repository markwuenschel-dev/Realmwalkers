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
  const { t, isGrim, isConsole, paletteOpen, decision } = useDesk();
  return (
    <div style={themeRootStyle(t)}>
      {isGrim && (
        <div
          style={css(
            "position:fixed;inset:0;pointer-events:none;background:radial-gradient(120% 90% at 50% -10%, rgba(201,162,83,.07), transparent 55%);z-index:0",
          )}
        />
      )}
      {isConsole && (
        <div
          style={css(
            "position:fixed;inset:0;pointer-events:none;background:radial-gradient(100% 70% at 50% -5%, rgba(79,214,224,.06), transparent 60%);z-index:0",
          )}
        />
      )}

      <TopBar />

      <main
        style={css(
          "position:relative;z-index:1;max-width:1480px;margin:0 auto;padding:30px 26px 80px",
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
