import { useCallback } from "react";
import { api } from "../client";
import type {
  ChapterOut,
  ChapterUpdateIn,
  ContinuityResolveIn,
  DecisionIn,
  JobsStatusOut,
  RetryFailedOut,
  SceneDetail,
} from "../types";
import type { DeskFail } from "./shared";

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
  runBulk: (ids: string[], fn: (id: string) => Promise<unknown>) => Promise<void>;
  decide: (sceneId: string, body: DecisionIn) => Promise<void>;
  revertScene: (sceneId: string) => Promise<void>;
  resolveContinuity: (sceneId: string, body: ContinuityResolveIn) => Promise<void>;
  setExemplar: (enabled: boolean) => Promise<void>;
}

export function useDeskSceneActions(
  fail: DeskFail,
  setError: (msg: string | null) => void,
  deps: DeskSceneActionsDeps,
): DeskSceneActionsState {
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

  const runBulk = useCallback(
    async (ids: string[], fn: (id: string) => Promise<unknown>): Promise<void> => {
      if (ids.length === 0) return;
      try {
        const results = await Promise.allSettled(ids.map(fn));
        await refreshAll();
        const failures = results.filter((r) => r.status === "rejected").length;
        if (failures > 0) setError(`${failures} of ${ids.length} failed — others applied.`);
      } catch (e) {
        fail(e);
      }
    },
    [fail, refreshAll, setError],
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
    runBulk,
    decide,
    revertScene,
    resolveContinuity,
    setExemplar,
  };
}
