import { useCallback } from "react";
import { api } from "../client";
import type {
  AnnotationIn,
  AnnotationOut,
  SuggestionIn,
  SuggestionOut,
  SuggestionStatus,
} from "../types";
import type { DeskFail } from "./shared";

export interface DeskMarkupState {
  addAnnotation: (activeSceneId: string | null, body: AnnotationIn) => Promise<void>;
  deleteAnnotation: (id: string) => Promise<void>;
  addSuggestion: (activeSceneId: string | null, body: SuggestionIn) => Promise<void>;
  decideSuggestion: (id: string, status: SuggestionStatus) => Promise<void>;
  deleteSuggestion: (id: string) => Promise<void>;
}

export function useDeskMarkup(
  fail: DeskFail,
  setAnnotations: React.Dispatch<React.SetStateAction<AnnotationOut[]>>,
  setSuggestions: React.Dispatch<React.SetStateAction<SuggestionOut[]>>,
): DeskMarkupState {
  const addAnnotation = useCallback(
    async (activeSceneId: string | null, body: AnnotationIn): Promise<void> => {
      if (!activeSceneId) return;
      try {
        const created = await api.createAnnotation(activeSceneId, body);
        setAnnotations((as) => [...as, created]);
      } catch (e) {
        fail(e);
      }
    },
    [fail, setAnnotations],
  );

  const deleteAnnotation = useCallback(
    async (id: string): Promise<void> => {
      try {
        await api.deleteAnnotation(id);
        setAnnotations((as) => as.filter((a) => a.id !== id));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setAnnotations],
  );

  const addSuggestion = useCallback(
    async (activeSceneId: string | null, body: SuggestionIn): Promise<void> => {
      if (!activeSceneId) return;
      try {
        const created = await api.createSuggestion(activeSceneId, body);
        setSuggestions((ss) => [...ss, created]);
      } catch (e) {
        fail(e);
      }
    },
    [fail, setSuggestions],
  );

  const decideSuggestion = useCallback(
    async (id: string, status: SuggestionStatus): Promise<void> => {
      try {
        const updated = await api.decideSuggestion(id, status);
        setSuggestions((ss) => ss.map((s) => (s.id === updated.id ? updated : s)));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setSuggestions],
  );

  const deleteSuggestion = useCallback(
    async (id: string): Promise<void> => {
      try {
        await api.deleteSuggestion(id);
        setSuggestions((ss) => ss.filter((s) => s.id !== id));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setSuggestions],
  );

  return {
    addAnnotation,
    deleteAnnotation,
    addSuggestion,
    decideSuggestion,
    deleteSuggestion,
  };
}
