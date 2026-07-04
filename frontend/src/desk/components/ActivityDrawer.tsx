"use client";

import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import { formatElapsed } from "./DraftActivity";
import { Button, Chip, Eyebrow, ProgressBar, Spinner } from "./ui";
import type { RecentJobOut } from "../api/types";

// The Activity drawer — everything the pipeline is doing, has queued, and just finished, one click
// from the top-bar pill. Fixed right panel over a scrim; Esc (global handler in state.ts), scrim
// click, or the × closes it. Polling is gated in useDeskRecentJobs; opening paints the last
// snapshot instantly.

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function duration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function jobPlace(j: { chapter_no?: number | null; scene_no?: number | null }): string {
  return `${j.chapter_no != null ? `Ch ${j.chapter_no} · ` : ""}Scene ${j.scene_no ?? "?"}`;
}

const ROW = "display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:8px";
const MONO = "font-family:var(--mono);font-size:10.5px;color:var(--dim)";

function DoneTick() {
  return (
    <span
      style={css(
        "display:flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:var(--good);flex:none;animation:checkPop 200ms var(--ease-out)",
      )}
    >
      <svg width="8" height="8" viewBox="0 0 10 10" fill="none" aria-hidden>
        <path
          d="M1.5 5.5l2.3 2.3L8.5 2.5"
          stroke="var(--bg2)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

function Section({
  label,
  actions,
  children,
}: {
  label: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div
        style={css(
          "display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px",
        )}
      >
        <Eyebrow>{label}</Eyebrow>
        {actions}
      </div>
      {children}
    </div>
  );
}

function RecentRow({ job }: { job: RecentJobOut }) {
  const failed = job.status === "failed";
  return (
    <div style={css(`${ROW};background:var(--bg3)`)}>
      {failed ? (
        <span
          style={css(
            "display:flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;border:1.5px solid var(--bad);color:var(--bad);font-family:var(--mono);font-size:10px;flex:none",
          )}
        >
          !
        </span>
      ) : (
        <DoneTick />
      )}
      <div style={css("min-width:0;flex:1")}>
        <div style={css("font-family:var(--ui);font-size:13px;color:var(--ink)")}>
          {jobPlace(job)}
          {job.kind !== "draft" && (
            <span style={css("color:var(--dim)")}> · {job.kind.replace(/_/g, " ")}</span>
          )}
        </div>
        {failed && job.last_error && (
          <div
            style={css(
              "font-family:var(--mono);font-size:10px;color:var(--bad);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap",
            )}
            title={job.last_error}
          >
            {job.last_error}
          </div>
        )}
      </div>
      <div style={css(`${MONO};text-align:right;flex:none;line-height:1.5`)}>
        {!failed && (
          <div>
            {duration(job.duration_s)}
            {job.word_count != null && <span> · {job.word_count.toLocaleString()}w</span>}
          </div>
        )}
        <div>{relTime(job.finished_at)}</div>
      </div>
    </div>
  );
}

export default function ActivityDrawer() {
  const { activityOpen, closeActivity } = useDesk();
  const { jobs, recentJobs, failedJobs, retryFailed, clearFailed, cancelJob, setQueuePaused } =
    useDeskData();
  if (!activityOpen) return null;

  const active = jobs.active_scene;
  const elapsed = formatElapsed(active?.elapsed_s);
  const queued = recentJobs?.queued ?? [];
  const recent = recentJobs?.recent ?? [];
  const finished = recent.filter((j) => j.status !== "failed");
  const failed = recent.filter((j) => j.status === "failed");

  return (
    <div className="no-print">
      <div
        onClick={closeActivity}
        style={css(
          "position:fixed;inset:0;z-index:70;background:var(--scrim);animation:scrimIn var(--dur) var(--ease)",
        )}
      />
      <aside
        role="dialog"
        aria-label="Pipeline activity"
        style={css(
          "position:fixed;top:0;right:0;bottom:0;z-index:71;width:min(380px,92vw);background:var(--bg2);border-left:1px solid var(--line);border-radius:var(--rLg) 0 0 var(--rLg);box-shadow:var(--shadow);display:flex;flex-direction:column;animation:drawerIn var(--dur-slow) var(--ease-out)",
        )}
      >
        <header
          style={css(
            "display:flex;align-items:center;gap:10px;padding:18px 20px 14px;border-bottom:1px solid var(--hairline)",
          )}
        >
          <div
            style={css(
              "font-family:var(--display);font-weight:500;font-size:21px;color:var(--ink);flex:1",
            )}
          >
            Activity
          </div>
          <button
            onClick={closeActivity}
            aria-label="Close activity"
            className="dk-btn"
            style={css(
              "border:1px solid var(--line);background:var(--bg3);color:var(--dim);border-radius:999px;width:28px;height:28px;cursor:pointer;font-size:14px;line-height:1",
            )}
          >
            ×
          </button>
        </header>

        <div
          style={css(
            "flex:1;overflow-y:auto;padding:18px 20px;display:flex;flex-direction:column;gap:22px",
          )}
        >
          <Section label="Now drafting">
            {jobs.running && active ? (
              <div
                style={css(
                  "border:1px solid color-mix(in srgb,var(--info) 35%,var(--line));border-radius:var(--r);padding:13px 14px;background:var(--bg3)",
                )}
              >
                <div style={css("display:flex;align-items:center;gap:9px;margin-bottom:8px")}>
                  <Spinner size={13} />
                  <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>
                    {jobPlace({ chapter_no: active.chapter_no, scene_no: active.scene_no })}
                  </span>
                  {elapsed && <span style={css(`${MONO};margin-left:auto`)}>{elapsed}</span>}
                </div>
                <div
                  style={css(
                    "font-family:var(--mono);font-size:11px;color:var(--info);margin-bottom:9px",
                  )}
                >
                  {active.phase ?? "drafting…"}
                </div>
                <ProgressBar />
              </div>
            ) : (
              <div style={css(`${MONO};padding:2px 1px`)}>idle — nothing drafting</div>
            )}
          </Section>

          <Section
            label={`Queued · ${queued.length}`}
            actions={
              <span style={css("display:flex;align-items:center;gap:8px")}>
                {jobs.queue_paused && <Chip label="paused" size="sm" tone="warn" />}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void setQueuePaused(!jobs.queue_paused)}
                  title={
                    jobs.queue_paused
                      ? "Resume — the drain picks the queue back up"
                      : "Pause — the current scene finishes, nothing new starts (survives redeploys)"
                  }
                >
                  {jobs.queue_paused ? "Resume" : "Pause queue"}
                </Button>
              </span>
            }
          >
            {queued.length ? (
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                {queued.map((q, i) => (
                  <div key={q.id} style={css(`${ROW};background:var(--bg3)`)}>
                    <span style={css(`${MONO};width:16px;text-align:center;flex:none`)}>
                      {i + 1}
                    </span>
                    <span
                      style={css("font-family:var(--ui);font-size:13px;color:var(--ink);flex:1")}
                    >
                      {jobPlace(q)}
                    </span>
                    {q.kind !== "draft" && (
                      <Chip label={q.kind.replace(/_/g, " ")} size="sm" tone="info" />
                    )}
                    <button
                      className="dk-btn"
                      onClick={() => void cancelJob(q.id)}
                      aria-label={`Cancel ${jobPlace(q)}`}
                      title={`Cancel ${jobPlace(q)}`}
                      style={css(
                        "flex:none;width:22px;height:22px;border-radius:50%;border:1px solid var(--line);background:transparent;color:var(--dim);cursor:pointer;font-size:12px;line-height:1;display:flex;align-items:center;justify-content:center",
                      )}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div style={css(`${MONO};padding:2px 1px`)}>
                {jobs.queue_paused ? "queue paused" : "queue clear"}
              </div>
            )}
          </Section>

          <Section label="Recently finished">
            {finished.length ? (
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                {finished.map((j) => (
                  <RecentRow key={j.id} job={j} />
                ))}
              </div>
            ) : (
              <div style={css(`${MONO};padding:2px 1px`)}>
                nothing finished yet{recentJobs ? "" : " — loading…"}
              </div>
            )}
          </Section>

          {(failed.length > 0 || failedJobs.length > 0) && (
            <Section label={`Failed · ${Math.max(failed.length, failedJobs.length)}`}>
              <div style={css("display:flex;flex-direction:column;gap:6px")}>
                {(failed.length ? failed : []).map((j) => (
                  <RecentRow key={j.id} job={j} />
                ))}
                <div style={css("display:flex;gap:8px;margin-top:4px")}>
                  <Button size="sm" variant="danger" onClick={() => void retryFailed()}>
                    Retry all
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void clearFailed()}>
                    Clear
                  </Button>
                </div>
              </div>
            </Section>
          )}
        </div>
      </aside>
    </div>
  );
}
