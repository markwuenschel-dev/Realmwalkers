import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_JOBS } from "../../constants";
import { useDeskBooks } from "../useDeskBooks";
import { useDeskChapterCreate } from "../useDeskChapterCreate";
import { resetCanonBodyGuardForTests, useDeskCollections } from "../useDeskCollections";
import { useDeskJobs } from "../useDeskJobs";
import { useDeskSceneActions } from "../useDeskSceneActions";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("../../client", () => ({
  api: {
    books: vi.fn(),
    jobsStatus: vi.fn(),
    jobsFailed: vi.fn(),
    createChapter: vi.fn(),
    proposePacket: vi.fn(),
    decide: vi.fn(),
    draftNext: vi.fn(),
    redraftScenes: vi.fn(),
    chapters: vi.fn(),
    chapterScenes: vi.fn(),
    pending: vi.fn(),
    manuscript: vi.fn(),
    characters: vi.fn(),
    canon: vi.fn(),
    threads: vi.fn(),
    ruleProposals: vi.fn(),
  },
}));

import { api } from "../../client";

describe("useDeskBooks", () => {
  beforeEach(() => {
    vi.mocked(api.books).mockReset();
  });

  it("loads books and selects the first by default", async () => {
    vi.mocked(api.books).mockResolvedValue([
      { id: "b1", title: "One", premise: null, created_at: "2020-01-01T00:00:00Z" },
      { id: "b2", title: "Two", premise: null, created_at: "2020-01-01T00:00:00Z" },
    ]);
    const fail = vi.fn();
    const setLoading = vi.fn();

    const { result } = renderHook(() => useDeskBooks(fail, setLoading));

    await waitFor(() => expect(result.current.books).toHaveLength(2));
    expect(result.current.bookId).toBe("b1");
    expect(fail).not.toHaveBeenCalled();
  });

  it("surfaces load failures via fail", async () => {
    vi.mocked(api.books).mockRejectedValue(new Error("network down"));
    const fail = vi.fn();
    const setLoading = vi.fn();

    renderHook(() => useDeskBooks(fail, setLoading));

    await waitFor(() => expect(fail).toHaveBeenCalled());
  });
});

describe("useDeskJobs", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.jobsStatus).mockReset();
    vi.mocked(api.jobsFailed).mockReset();
  });

  it("starts with no failed jobs and backend reachable", () => {
    vi.mocked(api.jobsStatus).mockResolvedValue(EMPTY_JOBS);
    const setJobs = vi.fn();
    const loadCollections = vi.fn().mockResolvedValue(undefined);
    const refreshScenes = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() =>
      useDeskJobs("book-1", EMPTY_JOBS, setJobs, loadCollections, refreshScenes),
    );

    expect(result.current.failedJobs).toEqual([]);
    expect(result.current.jobsUnreachable).toBe(false);
  });

  it("marks backend unreachable after consecutive poll failures", async () => {
    vi.mocked(api.jobsStatus).mockRejectedValue(new Error("down"));
    const setJobs = vi.fn();
    const loadCollections = vi.fn();
    const refreshScenes = vi.fn();

    const { result } = renderHook(() =>
      useDeskJobs("book-1", EMPTY_JOBS, setJobs, loadCollections, refreshScenes),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(result.current.jobsUnreachable).toBe(true);
    vi.useRealTimers();
  });

  it("clears the unreachable banner only after consecutive successful polls (hysteresis)", async () => {
    // Regression: one lucky success amid timeouts used to clear the banner instantly, so
    // intermittent slowness flapped the full-width banner on/off every few seconds.
    vi.mocked(api.jobsStatus).mockRejectedValue(new Error("down"));
    const setJobs = vi.fn();
    const loadCollections = vi.fn();
    const refreshScenes = vi.fn();

    const { result } = renderHook(() =>
      useDeskJobs("book-1", EMPTY_JOBS, setJobs, loadCollections, refreshScenes),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500); // fail 1
      await vi.advanceTimersByTimeAsync(4000); // fail 2 -> banner on
    });
    expect(result.current.jobsUnreachable).toBe(true);

    vi.mocked(api.jobsStatus).mockResolvedValue(EMPTY_JOBS);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000); // success 1 -> banner must stay on
    });
    expect(result.current.jobsUnreachable).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000); // success 2 -> banner clears
    });
    expect(result.current.jobsUnreachable).toBe(false);
    vi.useRealTimers();
  });

  it("refreshes slim scenes only on drafting progress, full collections only when the queue clears", async () => {
    // Regression: the busy poll used to run the FULL loadCollections (N+1 chapter-scene fetches +
    // a megabytes-scale canon upgrade) every 1.5s while drafting, saturating the single-worker
    // backend. Now: slim refreshScenes on real progress only; loadCollections once on queue-clear;
    // a phase change within the same scene is NOT progress.
    const busy = {
      ...EMPTY_JOBS,
      running: true,
      queued: 1,
      active_scene: { chapter_no: 1, scene_no: 2, phase: "draft" },
    };
    const busyLaterPhase = {
      ...busy,
      active_scene: { chapter_no: 1, scene_no: 2, phase: "review" },
    };
    const setJobs = vi.fn();
    const loadCollections = vi.fn().mockResolvedValue(undefined);
    const refreshScenes = vi.fn().mockResolvedValue(undefined);

    const { rerender } = renderHook(
      ({ jobs }) => useDeskJobs("book-1", jobs, setJobs, loadCollections, refreshScenes),
      { initialProps: { jobs: EMPTY_JOBS } },
    );

    // tick 1: idle -> busy = progress -> slim refresh
    vi.mocked(api.jobsStatus).mockResolvedValue(busy);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(refreshScenes).toHaveBeenCalledTimes(1);
    expect(loadCollections).not.toHaveBeenCalled();
    rerender({ jobs: busy }); // context caught up

    // tick 2 (busy cadence 1500ms): same scene, phase changed -> NOT progress -> no refetch
    vi.mocked(api.jobsStatus).mockResolvedValue(busyLaterPhase);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(refreshScenes).toHaveBeenCalledTimes(1);
    expect(loadCollections).not.toHaveBeenCalled();
    rerender({ jobs: busyLaterPhase });

    // tick 3: queue cleared -> one full reload, no extra slim refresh
    vi.mocked(api.jobsStatus).mockResolvedValue(EMPTY_JOBS);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(loadCollections).toHaveBeenCalledTimes(1);
    expect(refreshScenes).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

describe("useDeskChapterCreate", () => {
  beforeEach(() => {
    vi.mocked(api.createChapter).mockReset();
    vi.mocked(api.proposePacket).mockReset();
  });

  it("creates the chapter, kicks off packet proposal, refreshes, and returns the chapter id", async () => {
    vi.mocked(api.createChapter).mockResolvedValue({
      id: "ch-1",
      book_id: "book-1",
      chapter_no: 1,
      title: null,
      pov: "Marcus",
      outline: "An outline",
      status: "planned",
      kind: "chapter",
    });
    vi.mocked(api.proposePacket).mockResolvedValue({
      running: true,
      phase: "authoring",
      elapsed_s: 0,
    });
    const fail = vi.fn();
    const loadCollections = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeskChapterCreate(fail));

    let chapterId: string | null = null;
    await act(async () => {
      chapterId = await result.current.createAndPropose(
        "book-1",
        1,
        "Marcus",
        "An outline",
        loadCollections,
      );
    });

    expect(chapterId).toBe("ch-1");
    expect(api.createChapter).toHaveBeenCalledWith({
      book_id: "book-1",
      chapter_no: 1,
      pov: "Marcus",
      outline: "An outline",
    });
    expect(api.proposePacket).toHaveBeenCalledWith("ch-1");
    expect(loadCollections).toHaveBeenCalledWith("book-1");
    expect(fail).not.toHaveBeenCalled();
  });

  it("returns null and never proposes a packet when no book is selected", async () => {
    const fail = vi.fn();
    const loadCollections = vi.fn();
    const { result } = renderHook(() => useDeskChapterCreate(fail));

    let chapterId: string | null = "unset";
    await act(async () => {
      chapterId = await result.current.createAndPropose(
        null,
        1,
        "Marcus",
        "An outline",
        loadCollections,
      );
    });

    expect(chapterId).toBeNull();
    expect(api.createChapter).not.toHaveBeenCalled();
    expect(loadCollections).not.toHaveBeenCalled();
  });

  it("surfaces failures via fail and returns null", async () => {
    vi.mocked(api.createChapter).mockRejectedValue(new Error("boom"));
    const fail = vi.fn();
    const loadCollections = vi.fn();
    const { result } = renderHook(() => useDeskChapterCreate(fail));

    let chapterId: string | null = "unset";
    await act(async () => {
      chapterId = await result.current.createAndPropose(
        "book-1",
        1,
        "Marcus",
        "An outline",
        loadCollections,
      );
    });

    expect(chapterId).toBeNull();
    expect(fail).toHaveBeenCalled();
    expect(api.proposePacket).not.toHaveBeenCalled();
  });
});

describe("useDeskSceneActions runBulk", () => {
  beforeEach(() => {
    vi.mocked(api.decide).mockReset();
    vi.mocked(api.draftNext).mockReset();
  });

  function setup() {
    const fail = vi.fn();
    const setError = vi.fn();
    const refreshScenes = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useDeskSceneActions(fail, setError, {
        bookId: "book-1",
        activeSceneId: null,
        setJobs: vi.fn(),
        setChapters: vi.fn(),
        setDetail: vi.fn(),
        openSceneById: vi.fn(),
        refreshScenes,
      }),
    );
    return { result, fail, setError, refreshScenes };
  }

  it("drains the job queue after a bulk action when drainAfter is set", async () => {
    // Regression: bulk-approve/bulk-revise in the Inbox queue a Job per scene via api.decide, but
    // unlike the single-scene decide() path, this generic runner has no per-call next_job to key a
    // draftNext() off of -- without an explicit drain, queued jobs never start (they sit forever as
    // "N queued" with nothing running, since /jobs/draft-next is the only thing that kicks off the
    // background drain).
    vi.mocked(api.decide).mockResolvedValue({
      scene: "s1",
      status: "revision_requested",
      next_job: "j1",
    });
    vi.mocked(api.draftNext).mockResolvedValue({ scheduled: true, queued: 4, running: true });
    const { result } = setup();

    await act(async () => {
      await result.current.runBulk(
        ["s1", "s2", "s3", "s4"],
        (id) => api.decide(id, { decision: "revise", feedback: "note" }),
        { drainAfter: true },
      );
    });

    expect(api.decide).toHaveBeenCalledTimes(4);
    expect(api.draftNext).toHaveBeenCalledTimes(1);
  });

  it("does not drain when drainAfter is omitted (e.g. unrelated bulk deletes)", async () => {
    vi.mocked(api.decide).mockResolvedValue({ scene: "s1", status: "approved", next_job: null });
    const { result } = setup();

    await act(async () => {
      await result.current.runBulk(["s1"], (id) => api.decide(id, { decision: "approve" }));
    });

    expect(api.draftNext).not.toHaveBeenCalled();
  });
});

describe("useDeskSceneActions restartRedraft", () => {
  beforeEach(() => {
    vi.mocked(api.redraftScenes).mockReset();
    vi.mocked(api.draftNext).mockReset();
  });

  function setup() {
    const fail = vi.fn();
    const setError = vi.fn();
    const refreshScenes = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useDeskSceneActions(fail, setError, {
        bookId: "book-1",
        activeSceneId: null,
        setJobs: vi.fn(),
        setChapters: vi.fn(),
        setDetail: vi.fn(),
        openSceneById: vi.fn(),
        refreshScenes,
      }),
    );
    return { result, fail, setError, refreshScenes };
  }

  it("re-queues a fresh draft job and drains the queue when a job is scheduled", async () => {
    // Regression: a scene stuck in "revision_requested" (its auto-queued revision job failed, or
    // one was never queued) has no manual escape hatch without this -- restartRedraft is the Desk's
    // only way to re-queue drafting for it (via POST /chapters/{id}/scenes/redraft).
    vi.mocked(api.redraftScenes).mockResolvedValue({
      chapter_id: "ch-1",
      queued_job_ids: ["job-1"],
      queued: 1,
      skipped: [],
      repaired_beats: 0,
    });
    vi.mocked(api.draftNext).mockResolvedValue({ scheduled: true, queued: 1, running: true });
    const { result, fail, refreshScenes } = setup();

    await act(async () => {
      await result.current.restartRedraft("ch-1", "scene-1");
    });

    expect(api.redraftScenes).toHaveBeenCalledWith("ch-1", ["scene-1"]);
    expect(api.draftNext).toHaveBeenCalledTimes(1);
    expect(refreshScenes).toHaveBeenCalled();
    expect(fail).not.toHaveBeenCalled();
  });

  it("does not drain when nothing was queued (e.g. a job for this scene is already active)", async () => {
    vi.mocked(api.redraftScenes).mockResolvedValue({
      chapter_id: "ch-1",
      queued_job_ids: [],
      queued: 0,
      skipped: [],
      repaired_beats: 0,
    });
    const { result, refreshScenes } = setup();

    await act(async () => {
      await result.current.restartRedraft("ch-1", "scene-1");
    });

    expect(api.draftNext).not.toHaveBeenCalled();
    expect(refreshScenes).toHaveBeenCalled();
  });

  it("surfaces failures via fail (e.g. the 409 raised when no scene_ids resolve)", async () => {
    vi.mocked(api.redraftScenes).mockRejectedValue(new Error("409 Conflict"));
    const { result, fail, refreshScenes } = setup();

    await act(async () => {
      await result.current.restartRedraft("ch-1", "scene-1");
    });

    expect(fail).toHaveBeenCalled();
    expect(api.draftNext).not.toHaveBeenCalled();
    expect(refreshScenes).not.toHaveBeenCalled();
  });

  it("surfaces a 409's blocker required_action (not an opaque error) when no ScenePacket resolves", async () => {
    // The no-approved-ScenePacket case: redraft returns 409 with actionable blockers. restartRedraft
    // must show that reason, not a generic red toast — see draftBlockerMessage / ApiError.
    const apiErr = Object.assign(new Error("409 Conflict"), {
      status: 409,
      data: {
        blockers: [
          {
            chapter_id: "ch-1",
            scene_no: 3,
            reason: "beat_scene_packet_mismatch",
            message: "Scene 3 has no approved ScenePacket.",
            required_action: "Approve ScenePackets first",
          },
        ],
      },
    });
    vi.mocked(api.redraftScenes).mockRejectedValue(apiErr);
    const { result, fail } = setup();

    await act(async () => {
      await result.current.restartRedraft("ch-1", "scene-1");
    });

    expect(fail).toHaveBeenCalledTimes(1);
    const arg = fail.mock.calls[0]![0];
    expect(arg).toBeInstanceOf(Error);
    expect((arg as Error).message).toContain("Scene 3");
    expect((arg as Error).message).toContain("Approve ScenePackets first");
  });
});

describe("useDeskCollections", () => {
  const MARA = {
    id: "c1",
    kind: "person",
    name: "Mara",
    body: null,
    source: "manual",
    status: "active",
  };
  const CANON_SLIM = [MARA];
  const CANON_FULL = [{ ...MARA, body: "full body text" }];
  // The slim index request passes { includeBodies: false }; the heavy upgrade omits the option.
  const fullBodyCanonCalls = () =>
    vi.mocked(api.canon).mock.calls.filter((c) => c[2]?.includeBodies !== false).length;

  beforeEach(() => {
    vi.useRealTimers();
    resetCanonBodyGuardForTests(); // module-level session guard would leak between cases
    vi.mocked(api.chapters).mockReset().mockResolvedValue([]);
    vi.mocked(api.chapterScenes).mockReset().mockResolvedValue([]);
    vi.mocked(api.pending).mockReset().mockResolvedValue([]);
    vi.mocked(api.manuscript)
      .mockReset()
      .mockResolvedValue({ book_id: "book-1", title: "T", chapters: [] } as never);
    vi.mocked(api.characters).mockReset().mockResolvedValue([]);
    vi.mocked(api.threads).mockReset().mockResolvedValue([]);
    vi.mocked(api.ruleProposals).mockReset().mockResolvedValue([]);
    vi.mocked(api.jobsStatus).mockReset().mockResolvedValue(EMPTY_JOBS);
    vi.mocked(api.canon)
      .mockReset()
      .mockImplementation(async (_id, _kind, opts) =>
        opts?.includeBodies === false ? CANON_SLIM : CANON_FULL,
      );
  });

  function setup() {
    const fail = vi.fn();
    const setError = vi.fn();
    const setLoading = vi.fn();
    const onBookChange = vi.fn();
    return renderHook(() =>
      useDeskCollections("book-1", fail, setError, setLoading, onBookChange),
    );
  }

  it("downloads full canon bodies once per book per session across repeated loads", async () => {
    const { result } = setup();
    // Initial load: slim index paints first, the body corpus upgrades in the background.
    await waitFor(() => expect(result.current.canon).toEqual(CANON_FULL));
    expect(fullBodyCanonCalls()).toBe(1);

    await act(async () => {
      await result.current.loadCollections("book-1");
    });

    // The reload refetched the slim index but NOT the multi-megabyte body corpus…
    expect(fullBodyCanonCalls()).toBe(1);
    expect(vi.mocked(api.canon).mock.calls.length).toBe(3); // 2 slim + 1 body upgrade
    // …and the already-downloaded bodies survived the slim refresh (no downgrade to bodiless rows).
    expect(result.current.canon).toEqual(CANON_FULL);
  });

  it("serves the warm manuscript with zero refetch and refetches only once stale", async () => {
    const { result } = setup();
    await waitFor(() => expect(result.current.manuscript).not.toBeNull());
    expect(api.manuscript).toHaveBeenCalledTimes(1); // the initial full load

    await act(async () => {
      await result.current.refreshManuscript("book-1");
    });
    expect(api.manuscript).toHaveBeenCalledTimes(1); // warm: a Manuscript tab revisit fetches nothing

    await act(async () => {
      await result.current.refreshScenes("book-1"); // a scene decision's reconciliation path
    });
    await act(async () => {
      await result.current.refreshManuscript("book-1");
    });
    expect(api.manuscript).toHaveBeenCalledTimes(2); // stale → exactly one background refetch
  });
});
