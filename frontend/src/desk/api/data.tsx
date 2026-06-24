import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "./client";
import type {
  AnnotationIn,
  AnnotationOut,
  BeatOut,
  BookOut,
  CanonEntityIn,
  CanonEntityOut,
  CanonEntityUpdateIn,
  ChapterOut,
  ChapterUpdateIn,
  CharacterStateIn,
  CharacterStateOut,
  ContinuityResolveIn,
  DecisionIn,
  JobsStatusOut,
  ManuscriptOut,
  RunStartOut,
  SceneDetail,
  SceneOut,
  SceneVersionOut,
  SuggestionIn,
  SuggestionOut,
  SuggestionStatus,
  ThreadBeatIn,
  ThreadIn,
  ThreadOut,
} from "./types";

// Live data for the Writers' Desk: one place that talks to the API, polls while the worker drafts,
// and exposes the actions the screens fire. This is what replaces desk/data.ts — nothing here is a
// fixture. Server data lives here; ephemeral view state (which tab, which theme) lives in state.ts.

const EMPTY_JOBS: JobsStatusOut = { running: false, queued: 0, failed: 0, active_scene: null };

export interface DeskData {
  loading: boolean;
  error: string | null;

  books: BookOut[];
  bookId: string | null;
  setBook: (id: string) => void;

  chapters: ChapterOut[];
  scenes: SceneOut[]; // every scene of the book, all statuses
  pending: SceneOut[]; // pending_review, oldest first (the review queue)
  manuscript: ManuscriptOut | null;
  characters: CharacterStateOut[];
  canon: CanonEntityOut[];
  threads: ThreadOut[];
  jobs: JobsStatusOut;

  detail: SceneDetail | null;
  versions: SceneVersionOut[];
  activeBeat: BeatOut | null;
  activeSceneId: string | null;
  annotations: AnnotationOut[];
  suggestions: SuggestionOut[];
  openSceneById: (id: string | null) => void;

  refreshAll: () => Promise<void>;
  createBook: (title: string) => Promise<void>;
  updateChapter: (chapterId: string, body: ChapterUpdateIn) => Promise<void>;
  startRun: (
    chapterNo: number, pov: string, outline: string, maxBeats?: number, targetWords?: number,
  ) => Promise<RunStartOut | null>;
  approveAndDraft: (chapterId: string, beatIds?: string[]) => Promise<void>;
  decide: (sceneId: string, body: DecisionIn) => Promise<void>;
  revertScene: (sceneId: string) => Promise<void>;
  resolveContinuity: (sceneId: string, body: ContinuityResolveIn) => Promise<void>;
  setExemplar: (enabled: boolean) => Promise<void>;
  draftNext: () => Promise<void>;
  createThread: (body: ThreadIn) => Promise<void>;
  addThreadBeat: (threadId: string, body: ThreadBeatIn) => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
  upsertCharacter: (name: string, body: CharacterStateIn) => Promise<void>;
  deleteCharacter: (name: string) => Promise<void>;
  createCanon: (body: CanonEntityIn) => Promise<void>;
  updateCanon: (id: string, body: CanonEntityUpdateIn) => Promise<void>;
  deleteCanon: (id: string) => Promise<void>;
  ingestCanon: () => Promise<number | null>;
  addAnnotation: (body: AnnotationIn) => Promise<void>;
  deleteAnnotation: (id: string) => Promise<void>;
  addSuggestion: (body: SuggestionIn) => Promise<void>;
  decideSuggestion: (id: string, status: SuggestionStatus) => Promise<void>;
  deleteSuggestion: (id: string) => Promise<void>;
}

export function useDeskDataState(): DeskData {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [books, setBooks] = useState<BookOut[]>([]);
  const [bookId, setBookId] = useState<string | null>(null);

  const [chapters, setChapters] = useState<ChapterOut[]>([]);
  const [scenes, setScenes] = useState<SceneOut[]>([]);
  const [pending, setPending] = useState<SceneOut[]>([]);
  const [manuscript, setManuscript] = useState<ManuscriptOut | null>(null);
  const [characters, setCharacters] = useState<CharacterStateOut[]>([]);
  const [canon, setCanon] = useState<CanonEntityOut[]>([]);
  const [threads, setThreads] = useState<ThreadOut[]>([]);
  const [jobs, setJobs] = useState<JobsStatusOut>(EMPTY_JOBS);

  const [detail, setDetail] = useState<SceneDetail | null>(null);
  const [versions, setVersions] = useState<SceneVersionOut[]>([]);
  const [activeBeat, setActiveBeat] = useState<BeatOut | null>(null);
  const [activeSceneId, setActiveSceneId] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<AnnotationOut[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionOut[]>([]);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  // --- collections for the active book ------------------------------------------------------------
  const loadCollections = useCallback(async (id: string): Promise<void> => {
    const chs = await api.chapters(id);
    const [sceneLists, pend, ms, chars, can, thr, js] = await Promise.all([
      Promise.all(chs.map((c) => api.chapterScenes(c.id))),
      api.pending(),
      api.manuscript(id).catch(() => null),
      api.characters(id).catch(() => []),
      api.canon(id).catch(() => []),
      api.threads(id).catch(() => []),
      api.jobsStatus(id).catch(() => EMPTY_JOBS),
    ]);
    const chIds = new Set(chs.map((c) => c.id));
    setChapters(chs);
    setScenes(sceneLists.flat());
    setPending(pend.filter((s) => chIds.has(s.chapter_id)));
    setManuscript(ms);
    setCharacters(chars);
    setCanon(can);
    setThreads(thr);
    setJobs(js);
  }, []);

  const refreshAll = useCallback(async (): Promise<void> => {
    if (!bookId) return;
    try {
      await loadCollections(bookId);
      setError(null);
    } catch (e) {
      fail(e);
    }
  }, [bookId, loadCollections]);

  // initial: load books, pick the first
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const bks = await api.books();
        if (!alive) return;
        setBooks(bks);
        setBookId((cur) => cur ?? bks[0]?.id ?? null);
        setError(null);
      } catch (e) {
        if (alive) fail(e);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // when the active book changes, load its collections
  useEffect(() => {
    if (!bookId) return;
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        await loadCollections(bookId);
        if (alive) setError(null);
      } catch (e) {
        if (alive) fail(e);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [bookId, loadCollections]);

  // Poll job status so drafting progress (and freshly-drafted scenes) appear without a reload.
  // Adaptive cadence via self-scheduling setTimeout: ~1.5s while a draft is in flight so the live
  // phase/elapsed indicator feels real-time, backing off to ~4s when idle to spare the API.
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const bookRef = useRef(bookId);
  bookRef.current = bookId;
  useEffect(() => {
    let alive = true;
    let handle = 0;
    const tick = async () => {
      let busyNow = false;
      const id = bookRef.current;
      if (id) {
        try {
          const js = await api.jobsStatus(id);
          setJobs(js);
          const was = jobsRef.current;
          busyNow = js.running || js.queued > 0;
          const justFinished = !busyNow && (was.running || was.queued > 0);
          if (busyNow || justFinished) {
            await loadCollections(id);
          }
        } catch {
          /* transient — next tick retries */
        }
      }
      if (alive) handle = window.setTimeout(tick, busyNow ? 1500 : 4000);
    };
    handle = window.setTimeout(tick, 1500);
    return () => {
      alive = false;
      window.clearTimeout(handle);
    };
  }, [loadCollections]);

  // --- active scene detail ------------------------------------------------------------------------
  const openSceneById = useCallback((id: string | null): void => {
    setActiveSceneId(id);
    if (!id) {
      setDetail(null);
      setVersions([]);
      setActiveBeat(null);
      setAnnotations([]);
      setSuggestions([]);
      return;
    }
    (async () => {
      try {
        const d = await api.scene(id);
        setDetail(d);
        const [vs, beats, anns, sugs] = await Promise.all([
          api.sceneVersions(id),
          api.chapterBeats(d.chapter_id),
          api.annotations(id),
          api.suggestions(id),
        ]);
        setVersions(vs);
        setActiveBeat(beats.find((b) => b.scene_no === d.scene_no) ?? null);
        setAnnotations(anns);
        setSuggestions(sugs);
        setError(null);
      } catch (e) {
        fail(e);
      }
    })();
  }, []);

  // --- actions ------------------------------------------------------------------------------------
  const setBook = useCallback((id: string) => setBookId(id), []);

  const createBook = useCallback(async (title: string): Promise<void> => {
    try {
      const book = await api.createBook({ title });
      setBooks((bs) => [...bs, book]);
      setBookId(book.id);
    } catch (e) {
      fail(e);
    }
  }, []);

  const startRun = useCallback(
    async (
      chapterNo: number, pov: string, outline: string, maxBeats?: number, targetWords?: number,
    ): Promise<RunStartOut | null> => {
      if (!bookId) return null;
      try {
        const out = await api.startRun({
          book_id: bookId, chapter_no: chapterNo, pov, outline,
          max_beats: maxBeats ?? null, target_words: targetWords ?? null,
        });
        await loadCollections(bookId);
        return out;
      } catch (e) {
        fail(e);
        return null;
      }
    },
    [bookId, loadCollections],
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
    [],
  );

  const draftNext = useCallback(async (): Promise<void> => {
    try {
      const out = await api.draftNext(bookId ?? undefined);
      setJobs((j) => ({ ...j, queued: out.queued, running: out.running || j.running }));
    } catch (e) {
      fail(e);
    }
  }, [bookId]);

  const approveAndDraft = useCallback(
    async (chapterId: string, beatIds?: string[]): Promise<void> => {
      try {
        await api.approveBeats(chapterId, beatIds);
        await draftNext();
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, refreshAll],
  );

  const decide = useCallback(
    async (sceneId: string, body: DecisionIn): Promise<void> => {
      try {
        const res = await api.decide(sceneId, body);
        if (res.next_job) await draftNext(); // approve auto-advances; revise re-drafts — drive it
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, refreshAll],
  );

  // Roll a scene back to an earlier version: the API clones it into a new approved version; open it.
  const revertScene = useCallback(async (sceneId: string): Promise<void> => {
    try {
      const created = await api.revertScene(sceneId);
      openSceneById(created.id);
      await refreshAll();
    } catch (e) {
      fail(e);
    }
  }, [openSceneById, refreshAll]);

  const resolveContinuity = useCallback(
    async (sceneId: string, body: ContinuityResolveIn): Promise<void> => {
      try {
        const res = await api.resolveContinuity(sceneId, body);
        if (res.job) await draftNext();
        openSceneById(sceneId); // re-pull critiques (the resolved one is gone)
        await refreshAll();
      } catch (e) {
        fail(e);
      }
    },
    [draftNext, openSceneById, refreshAll],
  );

  // toggle the loaded scene as a voice exemplar; reflect the new state on the detail in place
  const setExemplar = useCallback(async (enabled: boolean): Promise<void> => {
    if (!activeSceneId) return;
    try {
      const res = await api.setExemplar(activeSceneId, enabled);
      setDetail((d) => (d && d.id === res.scene ? { ...d, is_exemplar: res.is_exemplar } : d));
    } catch (e) {
      fail(e);
    }
  }, [activeSceneId]);

  const createThread = useCallback(
    async (body: ThreadIn): Promise<void> => {
      if (!bookId) return;
      try {
        // Use the returned row optimistically. The API commits in a yield-dependency teardown (after
        // the response), so an immediate re-fetch can race and miss the just-created row.
        const created = await api.createThread(bookId, body);
        setThreads((ts) => [...ts, created]);
      } catch (e) {
        fail(e);
      }
    },
    [bookId],
  );

  const addThreadBeat = useCallback(
    async (threadId: string, body: ThreadBeatIn): Promise<void> => {
      try {
        const updated = await api.addThreadBeat(threadId, body);
        setThreads((ts) => ts.map((t) => (t.id === updated.id ? updated : t)));
      } catch (e) {
        fail(e);
      }
    },
    [],
  );

  const deleteThread = useCallback(async (id: string): Promise<void> => {
    try {
      await api.deleteThread(id);
      setThreads((ts) => ts.filter((t) => t.id !== id));
    } catch (e) {
      fail(e);
    }
  }, []);

  // --- world authoring: character state (Oracle baseline) + canon entities ------------------------
  const upsertCharacter = useCallback(async (name: string, body: CharacterStateIn): Promise<void> => {
    if (!bookId) return;
    try {
      const updated = await api.upsertCharacter(bookId, name, body);
      setCharacters((cs) => {
        const i = cs.findIndex((c) => c.character === updated.character);
        if (i < 0) return [...cs, updated].sort((a, b) => a.character.localeCompare(b.character));
        const next = [...cs];
        next[i] = updated;
        return next;
      });
    } catch (e) {
      fail(e);
    }
  }, [bookId]);

  const deleteCharacter = useCallback(async (name: string): Promise<void> => {
    if (!bookId) return;
    try {
      await api.deleteCharacter(bookId, name);
      setCharacters((cs) => cs.filter((c) => c.character !== name));
    } catch (e) {
      fail(e);
    }
  }, [bookId]);

  const createCanon = useCallback(async (body: CanonEntityIn): Promise<void> => {
    if (!bookId) return;
    try {
      const created = await api.createCanon(bookId, body);
      setCanon((cs) => [...cs, created]);
    } catch (e) {
      fail(e);
    }
  }, [bookId]);

  const updateCanon = useCallback(async (id: string, body: CanonEntityUpdateIn): Promise<void> => {
    try {
      const updated = await api.updateCanon(id, body);
      setCanon((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
    } catch (e) {
      fail(e);
    }
  }, []);

  const deleteCanon = useCallback(async (id: string): Promise<void> => {
    try {
      await api.deleteCanon(id);
      setCanon((cs) => cs.filter((c) => c.id !== id));
    } catch (e) {
      fail(e);
    }
  }, []);

  // Rebuild the retrieval index from the on-disk canon docs; returns the chunk count (or null on error).
  const ingestCanon = useCallback(async (): Promise<number | null> => {
    if (!bookId) return null;
    try {
      const out = await api.ingestCanon(bookId);
      await loadCollections(bookId); // passage rows changed — refresh the canon view
      return out.indexed;
    } catch (e) {
      fail(e);
      return null;
    }
  }, [bookId, loadCollections]);

  // markup actions operate on the loaded scene; re-pull the affected list after each write
  const addAnnotation = useCallback(async (body: AnnotationIn): Promise<void> => {
    if (!activeSceneId) return;
    try {
      const created = await api.createAnnotation(activeSceneId, body);
      setAnnotations((as) => [...as, created]); // optimistic — avoids the commit-after-response re-fetch race
    } catch (e) {
      fail(e);
    }
  }, [activeSceneId]);

  const deleteAnnotation = useCallback(async (id: string): Promise<void> => {
    try {
      await api.deleteAnnotation(id);
      setAnnotations((as) => as.filter((a) => a.id !== id));
    } catch (e) {
      fail(e);
    }
  }, []);

  const addSuggestion = useCallback(async (body: SuggestionIn): Promise<void> => {
    if (!activeSceneId) return;
    try {
      const created = await api.createSuggestion(activeSceneId, body);
      setSuggestions((ss) => [...ss, created]); // optimistic — avoids the commit-after-response re-fetch race
    } catch (e) {
      fail(e);
    }
  }, [activeSceneId]);

  const decideSuggestion = useCallback(async (id: string, status: SuggestionStatus): Promise<void> => {
    try {
      const updated = await api.decideSuggestion(id, status);
      setSuggestions((ss) => ss.map((s) => (s.id === updated.id ? updated : s)));
    } catch (e) {
      fail(e);
    }
  }, []);

  const deleteSuggestion = useCallback(async (id: string): Promise<void> => {
    try {
      await api.deleteSuggestion(id);
      setSuggestions((ss) => ss.filter((s) => s.id !== id));
    } catch (e) {
      fail(e);
    }
  }, []);

  return {
    loading, error,
    books, bookId, setBook,
    chapters, scenes, pending, manuscript, characters, canon, threads, jobs,
    detail, versions, activeBeat, activeSceneId, annotations, suggestions, openSceneById,
    refreshAll, createBook, updateChapter, startRun, approveAndDraft, decide, revertScene, resolveContinuity, draftNext,
    setExemplar,
    createThread, addThreadBeat, deleteThread,
    upsertCharacter, deleteCharacter, createCanon, updateCanon, deleteCanon, ingestCanon,
    addAnnotation, deleteAnnotation, addSuggestion, decideSuggestion, deleteSuggestion,
  };
}

const DeskDataContext = createContext<DeskData | null>(null);

export function DeskDataProvider({ children }: { children: ReactNode }) {
  const value = useDeskDataState();
  return <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>;
}

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (!ctx) throw new Error("useDeskData must be used inside <DeskDataProvider>");
  return ctx;
}
