import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../client";
import type { RecentJobsOut } from "../types";

// Queue + recently-finished snapshot behind the Activity drawer. Strictly gated: polls ONLY while
// the drawer is open or the queue is busy (2s busy / 5s open-but-idle / zero otherwise), with an
// immediate fetch on drawer open. The last snapshot stays in state so reopening paints instantly.
// `refresh` is exposed for actions (cancel a job) that must repaint the drawer right away.
export function useDeskRecentJobs(
  bookId: string | null,
  drawerOpen: boolean,
  busy: boolean,
): { recentJobs: RecentJobsOut | null; refreshRecentJobs: () => Promise<void> } {
  const [recentJobs, setRecentJobs] = useState<RecentJobsOut | null>(null);
  const bookRef = useRef(bookId);
  bookRef.current = bookId;

  const refreshRecentJobs = useCallback(async () => {
    const id = bookRef.current;
    if (!id) return;
    try {
      setRecentJobs(await api.jobsRecent(id));
    } catch {
      /* transient — the drawer keeps its last snapshot; /jobs/status owns health signaling */
    }
  }, []);

  useEffect(() => {
    if (!drawerOpen && !busy) return; // gated off — keep the stale snapshot, schedule nothing
    let alive = true;
    let handle = 0;
    const tick = async () => {
      await refreshRecentJobs();
      if (alive) handle = window.setTimeout(tick, busy ? 2000 : 5000);
    };
    void tick(); // immediate fetch on open / on busy start
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [drawerOpen, busy, refreshRecentJobs]);

  return { recentJobs, refreshRecentJobs };
}
