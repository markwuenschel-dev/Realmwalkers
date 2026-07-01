import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "../client";
import type {
  ChapterOut,
  ChapterUpdateIn,
  ClearDraftScenesOut,
  ClearFailedOut,
  ContinuityResolveIn,
  DecisionIn,
  JobsStatusOut,
  RetryFailedOut,
  SceneDetail,
} from "../types";
import { purgeDraftLocalStorage, isHttpNotFound } from "../../lib/draftStorage";
import { draftBlockerMessage, type DeskFail } from "./shared";

export interface DeskSceneActionsDeps {
  bookId: string | null;
  activeSceneId: string | null;
  setJobs: React.Dispatch<React.SetStateAction<JobsStatusOut>>;
  setChapters: React.Dispatch<React.SetStateAction<ChapterOut[]>>;
  setDetail: React.Dispatch<React.SetStateAction<SceneDetail | null>>;
  openSceneById: (id: string | null) => void;
  refreshAll: () => Promise<void>;
}

export interface DeskSceneActionsState {
  updateChapter: (chapterId: string, body: ChapterUpdateIn) => Promise<void>;
  draftNext: () => Promise<void>;
  retryFailed: () => Promise<RetryFailedOut | null>;
  clearFailed: (chapterId?: string | null) => Promise<ClearFailedOut | null>;
  clearDraftScenes: (chapterId?: string | null) => Promise<ClearDraftScenesOut | null>;
  deleteScenes: (ids: string[]) => Promise<void>;
  runBulk: (
    ids: string[],
    fn: (id: string) => Promise<unknown>,
    opts?: { drainAfter?: boolean },
  ) => Promise<void>;
  decide: (sceneId: string, body: DecisionIn) => Promise<void>;
  revertScene: (sceneId: string) => Promise<void>;
  resolveContinuity: (sceneId: string, body: ContinuityResolveIn) => Promise<void>;
  setExemplar: (enabled: boolean) => Promise<void>;
  restartRedraft: (chapterId: string, sceneId: string) => Promise<void>;
}

export function useDeskSceneActions(
  fail: DeskFail,
  setError: (msg: string | null) => void,
  deps: DeskSceneActionsDeps,
): DeskSceneActionsState {
  const router = useRouter();
  const { bookId, activeSceneId, setJobs, setChapters, setDetail, openSceneById, refreshAll } =
    deps;

  const draftNext = useCallback(async (): Promise<void> => {
    try {
      const out = await api.draftNext(bookId ?? undefined);
      setJobs((j) => ({ ...j, queued: out.queued, running: out.running || j.running }));
    } catch (e) {
      fail(e);
    }
  }, [bookId, fail, setJobs]);

  const retryFailed = useCallback(async (): Promise<RetryFailedOut | null> => {
    if (!bookId) return null;
    try {
      const out = await api.retryFailed(bookId);
      setJobs((j) => ({
        ...j,
        queued: out.queued,
        running: out.running || j.running,
        failed: out.skipped?.length ? j.failed : 0,
      }));
      await refreshAll();
      return out;
    } catch (e) {
      fail(e);
      return null;
    }
  }, [bookId, fail, refreshAll, setJobs]);

  const clearFailed = useCallback(
    async (chapterId?: string | null): Promise<ClearFailedOut | null> => {
      if (!bookId) return null;
      try {
        const out = await api.clearFailed(bookId, chapterId ?? undefined);
        setJobs((j) => ({ ...j, failed: out.failed }));
        await refreshAll();
        return out;
      } catch (e) {
        fail(e);
        return null;
      }
    },
    [bookId, fail, refreshAll, setJobs],
  );

  const clearDraftScenes = useCallback(
    async (chapterId?: string | null): Promise<ClearDraftScenesOut | null> => {
      if (!bookId) return null;
      try {
        const out = await api.clearDraftScenes(bookId, chapterId ?? undefined);
        try {
          for (let i = localStorage.length - 1; i >= 0; i--) {
            const key = localStorage.key(i);
            if (key?.startsWith("dominion:draft:")) localStorage.removeItem(key);
          }
        } catch {
          /* ignore */
        }
        if (activeSceneId) {
          try {
            await api.scene(activeSceneId);
          } catch (e) {
            if (isHttpNotFound(e)) {
              openSceneById(null);
              router.push("/");
            }
          }
        }
        await refreshAll();
        return out;
      } catch (e) {
        fail(e);
        return null;
      }
    },
    [activeSceneId, bookId, fail, openSceneById, refreshAll, router],
  );

  const deleteScenes = useCallback(
    async (ids: string[]): Promise<void> => {
      if (ids.length === 0) return;
      try {
        const results = await Promise.allSettled(ids.map((id) => api.deleteScene(id)));
        purgeDraftLocalStorage(ids);
        if (activeSceneId && ids.includes(activeSceneId)) {
          openSceneById(null);
          router.push("/");
        }
        await refreshAll();
        const failures = results.filter((r) => r.status === "rejected").length;
        if (failures > 0) setError(`${failures} of ${ids.length} failed — others applied.`);
      } catch (e) {
        fail(e);
      }
    },
    [activeSceneId, fail, openSceneById, refreshAll, router, setError],
  );

  const runBulk = useCallback(
    async (
      ids: string[],
      fn: (id: string) => Promise<unknown>,
      opts?: { drainAfter?: boolean },
    ): Promise<void> => {
      if (ids.length === 0) return;
      try {
        const results = await Promise.allSettled(ids.map(fn));
        // Bulk scene decisions (approve/revise) can each queue a draft Job, but unlike the
        // single-scene `decide()` path below, this generic runner has no per-call `next_job` to key
        // off of -- drain once, unconditionally, after the batch settles. draftNext() is a cheap
        // no-op when nothing's queued, so callers with no drafting side effects (e.g. bulk-deleting
        // ledger entries) just skip this by leaving drainAfter unset.
        if (opts?.drainAfter) await draftNext();
        await refreshAll();
        const failures = results.filter((r) => r.status === "rejected").length;
        if (failures > 0) setError(`${failures} of ${ids.length} failed — others applied.`);
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, fail, refreshAll, setError],
  );

  const updateChapter = useCallback(
    async (chapterId: string, body: ChapterUpdateIn): Promise<void> => {
      try {
        const updated = await api.updateChapter(chapterId, body);
        setChapters((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setChapters],
  );

  const decide = useCallback(
    async (sceneId: string, body: DecisionIn): Promise<void> => {
      try {
        const res = await api.decide(sceneId, body);
        if (res.next_job) await draftNext();
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, fail, refreshAll],
  );

  const revertScene = useCallback(
    async (sceneId: string): Promise<void> => {
      try {
        const created = await api.revertScene(sceneId);
        openSceneById(created.id);
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [fail, openSceneById, refreshAll],
  );

  const resolveContinuity = useCallback(
    async (sceneId: string, body: ContinuityResolveIn): Promise<void> => {
      try {
        const res = await api.resolveContinuity(sceneId, body);
        if (res.job) await draftNext();
        openSceneById(sceneId);
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, fail, openSceneById, refreshAll],
  );

  // Manual restart for a scene stuck in "revision_requested" (its auto-queued revision job failed,
  // or one was never queued). Re-queues a fresh DRAFT job for this exact scene via the same
  // contract-first redraft path the Chapters board's bulk "Redraft" action uses — deliberately NOT
  // schedule_revision()'s REVISE_* job, because retry-failed/clear-failed only reconcile JobKind.DRAFT
  // (see draft_queue.py), so a REVISE_* job that fails can never be retried from the Desk today.
  const restartRedraft = useCallback(
    async (chapterId: string, sceneId: string): Promise<void> => {
      try {
        const out = await api.redraftScenes(chapterId, [sceneId]);
        if (out.queued > 0) await draftNext();
        await refreshAll();
      } catch (e) {
        // A 409 here means no approved ScenePacket resolved for the scene (stale / unapproved /
        // missing) — surface the blocker's actionable reason instead of an opaque red toast.
        const msg = draftBlockerMessage(e);
        fail(msg ? new Error(msg) : e);
      }
    },
    [draftNext, fail, refreshAll],
  );

  const setExemplar = useCallback(
    async (enabled: boolean): Promise<void> => {
      if (!activeSceneId) return;
      try {
        const res = await api.setExemplar(activeSceneId, enabled);
        setDetail((d) => (d && d.id === res.scene ? { ...d, is_exemplar: res.is_exemplar } : d));
      } catch (e) {
        fail(e);
      }
    },
    [activeSceneId, fail, setDetail],
  );

  return {
    updateChapter,
    draftNext,
    retryFailed,
    clearFailed,
    clearDraftScenes,
    deleteScenes,
    runBulk,
    decide,
    revertScene,
    resolveContinuity,
    setExemplar,
    restartRedraft,
  };
}
