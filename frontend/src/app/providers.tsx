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
    <DeskDataProvider>
      <DeskProvider value={desk}>
        <DeskShell>{children}</DeskShell>
      </DeskProvider>
    </DeskDataProvider>
  );
}
