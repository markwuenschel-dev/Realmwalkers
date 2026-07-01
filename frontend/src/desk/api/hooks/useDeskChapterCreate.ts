import { useCallback, useState } from "react";
import { api } from "../client";
import type { DeskFail } from "./shared";

export interface DeskChapterCreateState {
  creating: boolean;
  createAndPropose: (
    bookId: string | null,
    chapterNo: number,
    pov: string,
    outline: string,
    loadCollections: (id: string) => Promise<void>,
  ) => Promise<string | null>;
}

// Contract-first entry point: create the chapter (no LLM call), kick off chapter-packet authoring in
// the background, then return the chapter id so the caller can navigate straight into the Packets
// screen — which already knows how to re-attach to an in-flight proposal on mount.
export function useDeskChapterCreate(fail: DeskFail): DeskChapterCreateState {
  const [creating, setCreating] = useState(false);

  const createAndPropose = useCallback(
    async (
      bookId: string | null,
      chapterNo: number,
      pov: string,
      outline: string,
      loadCollections: (id: string) => Promise<void>,
    ): Promise<string | null> => {
      if (!bookId) return null;
      setCreating(true);
      try {
        const chapter = await api.createChapter({
          book_id: bookId,
          chapter_no: chapterNo,
          pov,
          outline,
        });
        await api.proposePacket(chapter.id);
        await loadCollections(bookId);
        return chapter.id;
      } catch (e) {
        fail(e);
        return null;
      } finally {
        setCreating(false);
      }
    },
    [fail],
  );

  return { creating, createAndPropose };
}
