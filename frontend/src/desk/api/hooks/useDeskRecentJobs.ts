import { useEffect, useRef, useState } from "react";
import { api } from "../client";
import type { RecentJobsOut } from "../types";

// Queue + recently-finished snapshot behind the Activity drawer. Strictly gated: polls ONLY while
// the drawer is open or the queue is busy (2s busy / 5s open-but-idle / zero otherwise), with an
// immediate fetch on drawer open. The last snapshot stays in state so reopening paints instantly.
export function useDeskRecentJobs(
  bookId: string | null,
  drawerOpen: boolean,
  busy: boolean,
): RecentJobsOut | null {
  const [recentJobs, setRecentJobs] = useState<RecentJobsOut | null>(null);
  const bookRef = useRef(bookId);
  bookRef.current = bookId;

  useEffect(() => {
    if (!drawerOpen && !busy) return; // gated off — keep the stale snapshot, schedule nothing
    let alive = true;
    let handle = 0;
    const tick = async () => {
      const id = bookRef.current;
      if (id) {
        try {
          const out = await api.jobsRecent(id);
          if (alive) setRecentJobs(out);
        } catch {
          /* transient — the drawer keeps its last snapshot; /jobs/status owns health signaling */
        }
      }
      if (alive) handle = window.setTimeout(tick, busy ? 2000 : 5000);
    };
    void tick(); // immediate fetch on open / on busy start
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [drawerOpen, busy]);

  return recentJobs;
}
