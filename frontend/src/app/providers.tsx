"use client";

import type { ReactNode } from "react";
import { DeskProvider, useDeskState } from "../desk/state";
import { DeskDataProvider } from "../desk/api/data";
import DeskShell from "../desk/components/DeskShell";

// Client root: owns the two desk contexts (server data + UI state) and the persistent shell. Route
// pages render as `children` inside the shell's themed <main>.
export default function Providers({ children }: { children: ReactNode }) {
  const desk = useDeskState();
  return (
    // activityOpen is injected downward: the drawer flag lives in UI state (DeskProvider) but the
    // gated recent-jobs poll lives in the data layer, which mounts above it.
    <DeskDataProvider activityOpen={desk.activityOpen}>
      <DeskProvider value={desk}>
        <DeskShell>{children}</DeskShell>
      </DeskProvider>
    </DeskDataProvider>
  );
}
