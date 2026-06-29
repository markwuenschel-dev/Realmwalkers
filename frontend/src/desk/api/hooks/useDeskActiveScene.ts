import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../client";
import type { AnnotationOut, BeatOut, SceneDetail, SceneVersionOut, SuggestionOut } from "../types";
import type { DeskFail } from "./shared";

export interface DeskActiveSceneState {
  detail: SceneDetail | null;
  versions: SceneVersionOut[];
  activeBeat: BeatOut | null;
  activeSceneId: string | null;
  annotations: AnnotationOut[];
  suggestions: SuggestionOut[];
  openSceneById: (id: string | null) => void;
  setDetail: React.Dispatch<React.SetStateAction<SceneDetail | null>>;
  setAnnotations: React.Dispatch<React.SetStateAction<AnnotationOut[]>>;
  setSuggestions: React.Dispatch<React.SetStateAction<SuggestionOut[]>>;
}

export function useDeskActiveScene(
  fail: DeskFail,
  setError: (msg: string | null) => void,
): DeskActiveSceneState {
  const [detail, setDetail] = useState<SceneDetail | null>(null);
  const [versions, setVersions] = useState<SceneVersionOut[]>([]);
  const [activeBeat, setActiveBeat] = useState<BeatOut | null>(null);
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<AnnotationOut[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionOut[]>([]);

  const mountedRef = useRef(true);
  useEffect(
    () => () => {
      mountedRef.current = false;
    },
    [],
  );
  const openSeqRef = useRef(0);

  const openSceneById = useCallback(
    (id: string | null): void => {
      const seq = ++openSeqRef.current;
      setActiveSceneId(id);
      if (!id) {
        setDetail(null);
        setVersions([]);
        setActiveBeat(null);
        setAnnotations([]);
        setSuggestions([]);
        return;
      }
      const live = () => mountedRef.current && openSeqRef.current === seq;
      (async () => {
        try {
          const d = await api.scene(id);
          if (!live()) return;
          setDetail(d);
          const [vs, beats, anns, sugs] = await Promise.all([
            api.sceneVersions(id),
            api.chapterBeats(d.chapter_id),
            api.annotations(id),
            api.suggestions(id),
          ]);
          if (!live()) return;
          setVersions(vs);
          setActiveBeat(beats.find((b) => b.scene_no === d.scene_no) ?? null);
          setAnnotations(anns);
          setSuggestions(sugs);
          setError(null);
        } catch (e) {
          if (live()) fail(e);
        }
      })();
    },
    [fail, setError],
  );

  return {
    detail,
    versions,
    activeBeat,
    activeSceneId,
    annotations,
    suggestions,
    openSceneById,
    setDetail,
    setAnnotations,
    setSuggestions,
  };
}
