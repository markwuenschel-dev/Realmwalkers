import { useEffect, useRef, useState } from "react";
import { api } from "../client";
import { ACTIVITY_MAX, activityLabel, UNREACHABLE_AFTER } from "../constants";
import type { ActivityEntry, FailedJobOut, JobsStatusOut } from "../types";

export interface DeskJobsState {
  failedJobs: FailedJobOut[];
  jobsUnreachable: boolean;
  activity: ActivityEntry[];
  setActivity: React.Dispatch<React.SetStateAction<ActivityEntry[]>>;
}

export function useDeskJobs(
  bookId: string | null,
  jobs: JobsStatusOut,
  setJobs: React.Dispatch<React.SetStateAction<JobsStatusOut>>,
  loadCollections: (id: string) => Promise<void>,
): DeskJobsState {
  const [failedJobs, setFailedJobs] = useState<FailedJobOut[]>([]);
  const [jobsUnreachable, setJobsUnreachable] = useState(false);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);

  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const bookRef = useRef(bookId);
  bookRef.current = bookId;
  const failCountRef = useRef(0);
  const lastActivityRef = useRef("");

  useEffect(() => {
    let alive = true;
    let handle = 0;
    const tick = async () => {
      let busyNow = false;
      const id = bookRef.current;
      if (id) {
        try {
          const js = await api.jobsStatus(id);
          const was = jobsRef.current;
          setJobs(js);
          failCountRef.current = 0;
          setJobsUnreachable(false);
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

          if (busyNow || justFinished) {
            await loadCollections(id);
          }
        } catch {
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
  }, [loadCollections, setJobs]);

  return { failedJobs, jobsUnreachable, activity, setActivity };
}
