import type { ActivityEntry, JobsStatusOut } from "./types";

/** Default job-status shape before the first poll completes. */
export const EMPTY_JOBS: JobsStatusOut = {
  running: false,
  queued: 0,
  failed: 0,
  queue_paused: false,
  active_scene: null,
  last_cache_hit_ratio: null,
  last_cache_read_tokens: null,
  last_cache_creation_tokens: null,
  last_cache_tokens_saved: null,
};

/** Cap the live activity feed so it cannot grow unbounded across a long session. */
export const ACTIVITY_MAX = 14;

/** Consecutive failed polls before we call the backend unreachable. */
export const UNREACHABLE_AFTER = 2;

/** Consecutive successful polls before the "unreachable" banner clears. Symmetric hysteresis: one
 *  lucky success used to clear it instantly, so intermittent slowness flapped the full-width banner
 *  on/off (reflowing the whole page each time). */
export const REACHABLE_AFTER = 2;

/** Abort a status poll that hasn't answered in this long. Without it a stalled request blocks the
 *  self-rescheduling poll loop for the browser's default socket timeout (often minutes). */
export const POLL_TIMEOUT_MS = 8000;

/** One line for the live activity feed from the current job status. */
export function activityLabel(js: JobsStatusOut): string | null {
  if (js.running && js.active_scene) {
    const a = js.active_scene;
    const where = a.chapter_no != null ? `Ch ${a.chapter_no} · ` : "";
    return `${where}Scene ${a.scene_no ?? "?"}${a.phase ? ` · ${a.phase}` : ""}`;
  }
  if (js.queued > 0) return `${js.queued} queued`;
  return null;
}

/** Prepend a new activity line, capped at ACTIVITY_MAX (newest first). */
export function prependActivity(prev: ActivityEntry[], text: string): ActivityEntry[] {
  return [{ id: `${Date.now()}-${prev.length}`, ts: Date.now(), text }, ...prev].slice(
    0,
    ACTIVITY_MAX,
  );
}
