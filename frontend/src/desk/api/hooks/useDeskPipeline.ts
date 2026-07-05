import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../client";
import type { PipelineStatusOut } from "../types";

// Live book-wide pipeline snapshot behind the Pipeline dashboard. Gated exactly like useDeskRecentJobs:
// polls ~3s ONLY while `active` (the Pipeline tab is mounted), with an immediate fetch on activation.
// The last snapshot stays in state so a revisit paints instantly; a transient fetch error keeps it.
// `refreshPipeline` is exposed so an action (verify/approve) can repaint the board right away.
export function useDeskPipeline(
  bookId: string | null,
  active: boolean,
): { pipeline: PipelineStatusOut | null; refreshPipeline: () => Promise<void> } {
  const [pipeline, setPipeline] = useState<PipelineStatusOut | null>(null);
  const bookRef = useRef(bookId);
  bookRef.current = bookId;

  const refreshPipeline = useCallback(async () => {
    const id = bookRef.current;
    if (!id) return;
    try {
      setPipeline(await api.pipeline(id));
    } catch {
      /* transient — keep the last snapshot; the top-bar draft pill owns health signalling */
    }
  }, []);

  // Drop a stale snapshot when the active book changes, so the board never shows another book's runs.
  useEffect(() => {
    setPipeline(null);
  }, [bookId]);

  useEffect(() => {
    if (!active) return; // gated off — nothing scheduled
    let alive = true;
    let handle = 0;
    const tick = async () => {
      await refreshPipeline();
      if (alive) handle = window.setTimeout(tick, 3000);
    };
    void tick(); // immediate fetch on activation
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [active, bookId, refreshPipeline]);

  return { pipeline, refreshPipeline };
}
