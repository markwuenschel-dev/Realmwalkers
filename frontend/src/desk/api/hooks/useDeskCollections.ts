import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../client";
import { EMPTY_JOBS } from "../constants";
import type {
  CanonEntityOut,
  ChapterOut,
  CharacterStateOut,
  JobsStatusOut,
  ManuscriptOut,
  RuleProposalOut,
  SceneOut,
  ThreadOut,
} from "../types";
import type { DeskFail } from "./shared";

export interface DeskCollectionsState {
  chapters: ChapterOut[];
  scenes: SceneOut[];
  latestScenes: SceneOut[];
  pending: SceneOut[];
  manuscript: ManuscriptOut | null;
  characters: CharacterStateOut[];
  canon: CanonEntityOut[];
  threads: ThreadOut[];
  ruleProposals: RuleProposalOut[];
  jobs: JobsStatusOut;
  setJobs: React.Dispatch<React.SetStateAction<JobsStatusOut>>;
  setChapters: React.Dispatch<React.SetStateAction<ChapterOut[]>>;
  setRuleProposals: React.Dispatch<React.SetStateAction<RuleProposalOut[]>>;
  setThreads: React.Dispatch<React.SetStateAction<ThreadOut[]>>;
  setCharacters: React.Dispatch<React.SetStateAction<CharacterStateOut[]>>;
  setCanon: React.Dispatch<React.SetStateAction<CanonEntityOut[]>>;
  loadCollections: (id: string) => Promise<void>;
  refreshAll: (bookId: string | null) => Promise<void>;
  refreshScenes: (bookId: string | null) => Promise<void>;
  refreshManuscript: (bookId: string | null) => Promise<void>;
}

export function useDeskCollections(
  bookId: string | null,
  fail: DeskFail,
  setError: (msg: string | null) => void,
  setLoading: (v: boolean) => void,
  onBookChange: () => void,
): DeskCollectionsState {
  const [chapters, setChapters] = useState<ChapterOut[]>([]);
  const [scenes, setScenes] = useState<SceneOut[]>([]);
  const [pending, setPending] = useState<SceneOut[]>([]);
  const [manuscript, setManuscript] = useState<ManuscriptOut | null>(null);
  const [characters, setCharacters] = useState<CharacterStateOut[]>([]);
  const [canon, setCanon] = useState<CanonEntityOut[]>([]);
  const [threads, setThreads] = useState<ThreadOut[]>([]);
  const [ruleProposals, setRuleProposals] = useState<RuleProposalOut[]>([]);
  const [jobs, setJobs] = useState<JobsStatusOut>(EMPTY_JOBS);

  const latestScenes = useMemo(() => {
    const m = new Map<string, SceneOut>();
    for (const s of scenes) {
      const key = `${s.chapter_id}:${s.scene_no}`;
      const prev = m.get(key);
      if (!prev || s.version > prev.version) m.set(key, s);
    }
    return [...m.values()];
  }, [scenes]);

  const loadCollections = useCallback(async (id: string): Promise<void> => {
    const chs = await api.chapters(id);
    // Canon: first paint gets the slim index (id/kind/name — no bodies). The full corpus is
    // megabytes and was the single largest payload on EVERY page load; only command-palette body
    // search needs the bodies, so they upgrade in the background after render, never blocking it.
    const [sceneLists, pend, ms, chars, can, thr, rules, js] = await Promise.all([
      Promise.all(chs.map((c) => api.chapterScenes(c.id))),
      api.pending(),
      api.manuscript(id).catch(() => null),
      api.characters(id).catch(() => []),
      api.canon(id, undefined, { includeBodies: false }).catch(() => []),
      api.threads(id).catch(() => []),
      api.ruleProposals(id).catch(() => []),
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
    setRuleProposals(rules);
    setJobs(js);
    void api
      .canon(id)
      .then((full) => setCanon(full))
      .catch(() => {}); // slim index stays if the upgrade fails — palette just searches names/kinds
  }, []);

  const refreshAll = useCallback(
    async (activeBookId: string | null): Promise<void> => {
      if (!activeBookId) return;
      try {
        await loadCollections(activeBookId);
        setError(null);
      } catch (e) {
        fail(e);
      }
    },
    [fail, loadCollections, setError],
  );

  // Slim reconciliation used after a scene action (approve/revise/redraft/delete/…): refetch only the
  // collections a scene decision can change — chapters/scenes/pending/jobs — NOT the manuscript (the
  // heaviest payload) or world/rule data, which a scene decision never touches. The Manuscript screen
  // pulls its own compile on mount via refreshManuscript, so dropping it here won't leave it stale.
  const refreshScenes = useCallback(
    async (activeBookId: string | null): Promise<void> => {
      if (!activeBookId) return;
      try {
        const chs = await api.chapters(activeBookId);
        const [sceneLists, pend, js] = await Promise.all([
          Promise.all(chs.map((c) => api.chapterScenes(c.id))),
          api.pending(),
          api.jobsStatus(activeBookId).catch(() => EMPTY_JOBS),
        ]);
        const chIds = new Set(chs.map((c) => c.id));
        setChapters(chs);
        setScenes(sceneLists.flat());
        setPending(pend.filter((s) => chIds.has(s.chapter_id)));
        setJobs(js);
        setError(null);
      } catch (e) {
        fail(e);
      }
    },
    [fail, setError],
  );

  const refreshManuscript = useCallback(async (activeBookId: string | null): Promise<void> => {
    if (!activeBookId) return;
    try {
      setManuscript(await api.manuscript(activeBookId));
    } catch {
      /* keep the prior manuscript on a transient failure */
    }
  }, []);

  useEffect(() => {
    if (!bookId) return;
    let alive = true;
    onBookChange();
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
  }, [bookId, fail, loadCollections, onBookChange, setError, setLoading]);

  // Memoized so this object's identity is stable across renders (all setters are stable, callbacks are
  // useCallback'd, latestScenes is memoized) — the composing DeskData memo depends on it, so an unstable
  // container here would re-render every consumer on every render/poll tick.
  return useMemo(
    () => ({
      chapters,
      scenes,
      latestScenes,
      pending,
      manuscript,
      characters,
      canon,
      threads,
      ruleProposals,
      jobs,
      setJobs,
      setChapters,
      setRuleProposals,
      setThreads,
      setCharacters,
      setCanon,
      loadCollections,
      refreshAll,
      refreshScenes,
      refreshManuscript,
    }),
    [
      chapters,
      scenes,
      latestScenes,
      pending,
      manuscript,
      characters,
      canon,
      threads,
      ruleProposals,
      jobs,
      setJobs,
      setChapters,
      setRuleProposals,
      setThreads,
      setCharacters,
      setCanon,
      loadCollections,
      refreshAll,
      refreshScenes,
      refreshManuscript,
    ],
  );
}
