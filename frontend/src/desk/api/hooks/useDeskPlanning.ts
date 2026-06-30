import { useCallback, useState } from "react";
import { api } from "../client";
import type { RunStartOut } from "../types";
import type { DeskFail } from "./shared";

export interface DeskPlanningState {
  planningChapters: Set<number>;
  startRun: (
    bookId: string | null,
    chapterNo: number,
    pov: string,
    outline: string,
    loadCollections: (id: string) => Promise<void>,
    maxBeats?: number,
    targetWords?: number,
  ) => Promise<RunStartOut | null>;
  approveAndDraft: (
    chapterId: string,
    beatIds: string[] | undefined,
    draftNext: () => Promise<void>,
    refreshAll: () => Promise<void>,
  ) => Promise<void>;
  resetPlanningOnBookChange: () => void;
}

export function useDeskPlanning(fail: DeskFail): DeskPlanningState {
  const [planningChapters, setPlanningChapters] = useState<Set<number>>(new Set());

  const resetPlanningOnBookChange = useCallback(() => {
    setPlanningChapters(new Set());
  }, []);

  const startRun = useCallback(
    async (
      bookId: string | null,
      chapterNo: number,
      pov: string,
      outline: string,
      loadCollections: (id: string) => Promise<void>,
      maxBeats?: number,
      targetWords?: number,
    ): Promise<RunStartOut | null> => {
      if (!bookId) return null;
      setPlanningChapters((s) => new Set(s).add(chapterNo));
      try {
        const out = await api.startRun({
          book_id: bookId,
          chapter_no: chapterNo,
          pov,
          outline,
          gate_mode: "pause_each",
          max_beats: maxBeats ?? null,
          target_words: targetWords ?? null,
        });
        await loadCollections(bookId);
        return out;
      } catch (e) {
        fail(e);
        return null;
      } finally {
        setPlanningChapters((s) => {
          const n = new Set(s);
          n.delete(chapterNo);
          return n;
        });
      }
    },
    [fail],
  );

  const approveAndDraft = useCallback(
    async (
      chapterId: string,
      beatIds: string[] | undefined,
      _draftNext: () => Promise<void>,
      refreshAll: () => Promise<void>,
    ): Promise<void> => {
      try {
        await api.approveBeats(chapterId, beatIds);
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [fail],
  );

  return { planningChapters, startRun, approveAndDraft, resetPlanningOnBookChange };
}
