import { css } from "../css";
import { useDesk } from "../state";
import { useDeskData } from "../api/data";
import type { ActiveScene } from "../api/types";

// Live drafting indicators, in one place so the top bar and the Inbox stay in sync. The data layer
// polls /jobs/status fast (~1.5s) while a draft is in flight, and the worker reports a sub-stage
// ("drafting prose", "enriching · combat", "reviewing", …) + elapsed seconds — so these never look
// frozen: there's always a spinner, a moving bar, a ticking clock, or a changing phase.

export function formatElapsed(s: number | null | undefined): string | null {
  if (s == null || s < 0) return null;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// Where a drafting scene is: "Ch 3 · Scene 2" (falls back gracefully if the worker hasn't reported
// chapter/scene yet).
function sceneLabelOf(a: ActiveScene | null): string {
  if (!a) return "a scene";
  const ch = a.chapter_no != null ? `Ch ${a.chapter_no} · ` : "";
  return `${ch}Scene ${a.scene_no ?? "?"}`;
}

export function Spinner({ size = 13, color = "var(--info)" }: { size?: number; color?: string }) {
  return (
    <span
      style={css(
        `display:inline-block;flex:none;width:${size}px;height:${size}px;border-radius:50%;` +
          `border:2px solid var(--line);border-top-color:${color};animation:spin .8s linear infinite`,
      )}
    />
  );
}

export function IndeterminateBar({ color = "var(--info)" }: { color?: string }) {
  return (
    <div style={css("position:relative;height:3px;border-radius:3px;background:var(--line);overflow:hidden")}>
      <div style={css(`position:absolute;top:0;bottom:0;border-radius:3px;background:${color};animation:indeterminate 1.1s ease-in-out infinite`)} />
    </div>
  );
}

// Compact pill for the top bar: spinner + which scene + live phase + elapsed; or a quiet "N queued".
export function DraftPill() {
  const { jobs } = useDeskData();
  const active = jobs.active_scene;
  const elapsed = formatElapsed(active?.elapsed_s);

  if (jobs.running) {
    return (
      <span style={css("display:flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;color:var(--info);background:color-mix(in srgb,var(--info) 10%,transparent);border:1px solid color-mix(in srgb,var(--info) 28%,var(--line));border-radius:999px;padding:4px 10px;max-width:340px")}>
        <Spinner size={12} />
        <span style={css("white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink)")}>
          {sceneLabelOf(active)}
          {active?.phase ? <span style={css("color:var(--info)")}> · {active.phase}</span> : null}
        </span>
        {elapsed && <span style={css("color:var(--dim)")}>{elapsed}</span>}
        {jobs.queued > 0 && <span style={css("color:var(--dim)")}>+{jobs.queued}</span>}
      </span>
    );
  }
  if (jobs.queued > 0) {
    return (
      <span style={css("display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;color:var(--dim)")}>
        <span style={css("width:7px;height:7px;border-radius:50%;background:var(--info);animation:pulseDot 1.4s ease-in-out infinite")} />
        {jobs.queued} queued
      </span>
    );
  }
  return null;
}

// Rich panel for the Inbox "Drafting" column: scene + phase + moving bar + elapsed, then the queued
// and failed tallies. When idle it just states the queue is clear.
export function DraftPanel() {
  const { t } = useDesk();
  const { jobs } = useDeskData();
  const active = jobs.active_scene;
  const elapsed = formatElapsed(active?.elapsed_s);
  const card = "background:var(--bg2);border-radius:10px;padding:13px 14px";

  if (jobs.running) {
    return (
      <div style={css(`${card};border:1px solid color-mix(in srgb,var(--info) 35%,var(--line))`)}>
        <div style={css("display:flex;align-items:center;gap:9px;margin-bottom:9px")}>
          <Spinner />
          <span style={css("font-family:var(--display);font-size:15px;color:var(--ink)")}>{sceneLabelOf(active)}</span>
          {elapsed && <span style={css("margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--dim)")}>{elapsed}</span>}
        </div>
        <div style={css("font-family:var(--mono);font-size:11.5px;color:var(--info);margin-bottom:9px")}>
          {active?.phase ?? "drafting…"}
        </div>
        <IndeterminateBar />
        {(jobs.queued > 0 || jobs.failed > 0) && (
          <div style={css("display:flex;gap:12px;margin-top:10px;font-family:var(--mono);font-size:10.5px;color:var(--dim)")}>
            {jobs.queued > 0 && <span>{jobs.queued} queued next</span>}
            {jobs.failed > 0 && <span style={css(`color:${t.bad}`)}>{jobs.failed} failed</span>}
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={css(`${card};border:1px dashed var(--line);text-align:center;font-family:var(--mono);font-size:11px;color:var(--dim)`)}>
      {jobs.queued > 0 ? (
        <span style={css("display:flex;align-items:center;justify-content:center;gap:7px")}>
          <span style={css("width:7px;height:7px;border-radius:50%;background:var(--info);animation:pulseDot 1.4s ease-in-out infinite")} />
          {jobs.queued} queued — starting…
        </span>
      ) : jobs.failed > 0 ? (
        <span style={css(`color:${t.bad}`)}>{jobs.failed} failed — open a scene to retry</span>
      ) : (
        "idle — nothing drafting"
      )}
    </div>
  );
}

function clock(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// A running log of what the worker just did — drafting phases as they change, then "Queue clear ✓".
// Reads as motion even between the ~1.5s polls, so the Desk never looks frozen mid-run.
export function ActivityFeed() {
  const { activity } = useDeskData();
  if (activity.length === 0) return null;
  return (
    <div style={css("background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:12px 13px")}>
      <div style={css("font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim);margin-bottom:9px")}>Activity</div>
      <div style={css("display:flex;flex-direction:column;gap:6px;max-height:230px;overflow:auto")}>
        {activity.map((e) => (
          <div key={e.id} style={css("display:flex;gap:9px;align-items:baseline;font-family:var(--mono);font-size:11px")}>
            <span style={css("color:var(--dim);flex:none")}>{clock(e.ts)}</span>
            <span style={css("color:var(--ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")}>{e.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
