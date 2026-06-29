import { describe, expect, it } from "vitest";
import { ACTIVITY_MAX, activityLabel, EMPTY_JOBS, prependActivity } from "./constants";
import type { JobsStatusOut } from "./types";

describe("EMPTY_JOBS", () => {
  it("is idle with zero queue and no active scene", () => {
    expect(EMPTY_JOBS.running).toBe(false);
    expect(EMPTY_JOBS.queued).toBe(0);
    expect(EMPTY_JOBS.active_scene).toBeNull();
  });
});

describe("activityLabel", () => {
  it("describes an in-flight draft with chapter and phase", () => {
    const js: JobsStatusOut = {
      ...EMPTY_JOBS,
      running: true,
      active_scene: {
        chapter_no: 2,
        scene_no: 3,
        phase: "drafting",
        elapsed_s: 10,
        cache_hit_ratio: null,
        total_cache_read_tokens: null,
        total_cache_creation_tokens: null,
      },
    };
    expect(activityLabel(js)).toBe("Ch 2 · Scene 3 · drafting");
  });

  it("shows queued count when idle but jobs are waiting", () => {
    expect(activityLabel({ ...EMPTY_JOBS, queued: 4 })).toBe("4 queued");
  });

  it("returns null when nothing is running or queued", () => {
    expect(activityLabel(EMPTY_JOBS)).toBeNull();
  });
});

describe("prependActivity", () => {
  it("caps the feed at ACTIVITY_MAX entries", () => {
    const prev = Array.from({ length: ACTIVITY_MAX }, (_, i) => ({
      id: String(i),
      ts: i,
      text: `line ${i}`,
    }));
    const next = prependActivity(prev, "new");
    expect(next).toHaveLength(ACTIVITY_MAX);
    expect(next[0]?.text).toBe("new");
  });
});
