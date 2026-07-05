import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../client";
import type { ActivityOut } from "../types";

// The central Activity feed behind the drawer — production events, review decisions, draft results,
// and the autonomous sweeper's moves, all in one list. Gated exactly like useDeskRecentJobs: polls
// ONLY while the drawer is open or the queue is busy (2s busy / 5s open-idle / off otherwise), with
// an immediate fetch on open. The last snapshot stays in state so reopening paints instantly.
// `refresh` lets actions (clear) repaint the feed right away.
export function useDeskActivity(
  bookId: string | null,
  drawerOpen: boolean,
  busy: boolean,
): { activityFeed: ActivityOut[]; refreshActivity: () => Promise<void> } {
  const [activityFeed, setActivityFeed] = useState<ActivityOut[]>([]);
  const bookRef = useRef(bookId);
  bookRef.current = bookId;

  const refreshActivity = useCallback(async () => {
    try {
      setActivityFeed(await api.activity(bookRef.current ?? undefined));
    } catch {
      /* transient — keep the last snapshot; /jobs/status owns health signaling */
    }
  }, []);

  useEffect(() => {
    if (!drawerOpen && !busy) return; // gated off — keep the stale snapshot, schedule nothing
    let alive = true;
    let handle = 0;
    const tick = async () => {
      await refreshActivity();
      if (alive) handle = window.setTimeout(tick, busy ? 2000 : 5000);
    };
    void tick(); // immediate fetch on open / on busy start
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [drawerOpen, busy, refreshActivity]);

  return { activityFeed, refreshActivity };
}
