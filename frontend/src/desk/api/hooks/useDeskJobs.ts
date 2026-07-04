import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../client";
import {
  ACTIVITY_MAX,
  activityLabel,
  POLL_TIMEOUT_MS,
  REACHABLE_AFTER,
  UNREACHABLE_AFTER,
} from "../constants";
import type { ActivityEntry, FailedJobOut, JobsStatusOut } from "../types";

export interface DeskJobsState {
  failedJobs: FailedJobOut[];
  jobsUnreachable: boolean;
  activity: ActivityEntry[];
  setActivity: React.Dispatch<React.SetStateAction<ActivityEntry[]>>;
}

/** Identity of the scene a job is working on — deliberately ignores `phase`/`elapsed_s`, which tick
 *  several times per scene and must not count as drafting progress. */
const sceneKey = (s: JobsStatusOut["active_scene"]): string =>
  s ? `${s.chapter_no ?? ""}:${s.scene_no ?? ""}` : "";

export function useDeskJobs(
  bookId: string | null,
  jobs: JobsStatusOut,
  setJobs: React.Dispatch<React.SetStateAction<JobsStatusOut>>,
  loadCollections: (id: string) => Promise<void>,
  refreshScenes: (id: string | null) => Promise<void>,
): DeskJobsState {
  const [failedJobs, setFailedJobs] = useState<FailedJobOut[]>([]);
  const [jobsUnreachable, setJobsUnreachable] = useState(false);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);

  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const bookRef = useRef(bookId);
  bookRef.current = bookId;
  const failCountRef = useRef(0);
  const okCountRef = useRef(0);
  const lastActivityRef = useRef("");

  useEffect(() => {
    let alive = true;
    let handle = 0;
    const tick = async () => {
      let busyNow = false;
      const id = bookRef.current;
      if (id) {
        try {
          const js = await api.jobsStatus(id, { signal: AbortSignal.timeout(POLL_TIMEOUT_MS) });
          const was = jobsRef.current;
          // Only push a new jobs object when something actually changed. An identical poll — the common
          // idle case, every 4s — must NOT churn the context, or the whole Desk re-renders for nothing.
          if (JSON.stringify(js) !== JSON.stringify(was)) setJobs(js);
          failCountRef.current = 0;
          // Hysteresis: the banner needs REACHABLE_AFTER consecutive good polls to clear, so one
          // lucky success amid timeouts no longer flaps it on/off.
          okCountRef.current += 1;
          if (okCountRef.current >= REACHABLE_AFTER) setJobsUnreachable(false);
          busyNow = js.running || js.queued > 0;
          const justFinished = !busyNow && (was.running || was.queued > 0);

          const label = activityLabel(js);
          if (label && label !== lastActivityRef.current) {
            lastActivityRef.current = label;
            setActivity((a) =>
              [{ id: `${Date.now()}-${a.length}`, ts: Date.now(), text: label }, ...a].slice(
                0,
                ACTIVITY_MAX,
              ),
            );
          } else if (justFinished) {
            lastActivityRef.current = "";
            setActivity((a) =>
              [
                { id: `${Date.now()}-${a.length}`, ts: Date.now(), text: "Queue clear ✓" },
                ...a,
              ].slice(0, ACTIVITY_MAX),
            );
          }

          if (js.failed !== was.failed) {
            if (js.failed > 0)
              api
                .jobsFailed(id)
                .then(setFailedJobs)
                .catch(() => {});
            else setFailedJobs([]);
          }

          // Refresh gating. The old behavior — full loadCollections on EVERY busy tick (1.5s) — was
          // an N+1 chapter-scenes fan-out plus a megabytes-scale canon upgrade that saturated the
          // single-worker backend and starved this very poll. Now: the slim scene refresh runs only
          // when the status shows real progress (a job started/finished/failed or moved to another
          // scene), and the full reload runs once when the queue clears.
          const progressed =
            js.queued !== was.queued ||
            js.failed !== was.failed ||
            js.running !== was.running ||
            sceneKey(js.active_scene) !== sceneKey(was.active_scene);
          if (justFinished) {
            await loadCollections(id);
          } else if (busyNow && progressed) {
            await refreshScenes(id);
          }
        } catch {
          okCountRef.current = 0;
          failCountRef.current += 1;
          if (failCountRef.current >= UNREACHABLE_AFTER) setJobsUnreachable(true);
        }
      }
      if (alive) handle = window.setTimeout(tick, busyNow ? 1500 : 4000);
    };
    handle = window.setTimeout(tick, 1500);
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [loadCollections, refreshScenes, setJobs]);

  return useMemo(
    () => ({ failedJobs, jobsUnreachable, activity, setActivity }),
    [failedJobs, jobsUnreachable, activity, setActivity],
  );
}
