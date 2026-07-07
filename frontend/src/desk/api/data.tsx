"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ApiError, api } from "./client";
import { useDeskActiveScene } from "./hooks/useDeskActiveScene";
import { useDeskBooks } from "./hooks/useDeskBooks";
import { useDeskChapterCreate } from "./hooks/useDeskChapterCreate";
import { invalidateCanonBodies, useDeskCollections } from "./hooks/useDeskCollections";
import { useDeskError } from "./hooks/useDeskError";
import { useDeskJobs } from "./hooks/useDeskJobs";
import { useDeskMarkup } from "./hooks/useDeskMarkup";
import { useDeskRecentJobs } from "./hooks/useDeskRecentJobs";
import { useDeskActivity } from "./hooks/useDeskActivity";
import { useDeskRules } from "./hooks/useDeskRules";
import { useDeskToasts } from "./hooks/useDeskToasts";
import { useDeskSceneActions } from "./hooks/useDeskSceneActions";
import { useDeskWorld } from "./hooks/useDeskWorld";
import type {
  AnnotationIn,
  AnnotationOut,
  BeatOut,
  BookOut,
  CanonEntityIn,
  CanonEntityOut,
  CanonRebuildStartedOut,
  CanonEntityUpdateIn,
  ChapterOut,
  ChapterUpdateIn,
  CharacterStateIn,
  CharacterStateOut,
  ContinuityResolveIn,
  DecisionIn,
  ActivityEntry,
  ActivityOut,
  FailedJobOut,
  JobsStatusOut,
  ManuscriptOut,
  RuleProposalDecisionIn,
  RuleProposalOut,
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

// Live data for the Writers' Desk: composes domain hooks that talk to the API, poll while the worker
// drafts, and expose the actions screens fire. Public API (DeskData, useDeskData) is unchanged.

export interface DeskData {
  loading: boolean;
  error: string | null;
  clearError: () => void;

  books: BookOut[];
  bookId: string | null;
  setBook: (id: string) => void;

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
  failedJobs: FailedJobOut[];
  jobsUnreachable: boolean;
  activity: ActivityEntry[];
  // Central Activity feed (persisted, cross-page) behind the drawer — the single source of truth for
  // "what happened". `clearActivityFeed` dismisses finished activity + purges DONE jobs.
  activityFeed: ActivityOut[];
  refreshActivity: () => Promise<void>;
  clearActivityFeed: (scope?: "all" | "finished") => Promise<void>;
  // Activity drawer + completion toasts (Atelier). recentJobs is null until first gated poll.
  recentJobs: import("./types").RecentJobsOut | null;
  refreshRecentJobs: () => Promise<void>;
  toasts: import("../components/ui/Toast").ToastItem[];
  pushToast: (toast: Omit<import("../components/ui/Toast").ToastItem, "id">) => void;
  dismissToast: (id: string) => void;
  // Queue control (Desk Control Round): cancel one queued job; flip the persisted pause switch.
  cancelJob: (jobId: string) => Promise<void>;
  setQueuePaused: (paused: boolean) => Promise<void>;

  detail: SceneDetail | null;
  versions: SceneVersionOut[];
  activeBeat: BeatOut | null;
  activeSceneId: string | null;
  loadingScene: boolean;
  missingSceneId: string | null;
  annotations: AnnotationOut[];
  suggestions: SuggestionOut[];
  openSceneById: (id: string | null) => void;

  refreshAll: () => Promise<void>;
  refreshManuscript: () => Promise<void>;
  createBook: (title: string) => Promise<void>;
  updateChapter: (chapterId: string, body: ChapterUpdateIn) => Promise<void>;
  creatingChapter: boolean;
  createAndPropose: (chapterNo: number, pov: string, outline: string) => Promise<string | null>;
  retryFailed: () => Promise<import("./types").RetryFailedOut | null>;
  clearFailed: (chapterId?: string | null) => Promise<import("./types").ClearFailedOut | null>;
  clearDraftScenes: (
    chapterId?: string | null,
  ) => Promise<import("./types").ClearDraftScenesOut | null>;
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
  draftNext: () => Promise<void>;
  createThread: (body: ThreadIn) => Promise<void>;
  addThreadBeat: (threadId: string, body: ThreadBeatIn) => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
  upsertCharacter: (name: string, body: CharacterStateIn) => Promise<void>;
  deleteCharacter: (name: string) => Promise<void>;
  createCanon: (body: CanonEntityIn) => Promise<void>;
  updateCanon: (id: string, body: CanonEntityUpdateIn) => Promise<void>;
  deleteCanon: (id: string) => Promise<void>;
  // Ledger "Clean rebuild from docs" action. Returns the full result (indexed, skipped, retired, total)
  // so the UI can report how many stale repo-ingested rows were purged.
  ingestCanon: () => Promise<CanonRebuildStartedOut | null>;
  distillRules: (pov?: string) => Promise<number>;
  decideRuleProposal: (id: string, body: RuleProposalDecisionIn) => Promise<void>;
  addAnnotation: (body: AnnotationIn) => Promise<void>;
  deleteAnnotation: (id: string) => Promise<void>;
  addSuggestion: (body: SuggestionIn) => Promise<void>;
  decideSuggestion: (id: string, status: SuggestionStatus) => Promise<void>;
  deleteSuggestion: (id: string) => Promise<void>;
}

// Stable identity for the onBookChange callback. An inline `() => {}` here re-armed the collections
// bootstrap effect (it's in that effect's dependency array) on EVERY provider render — so any state
// change in this hook (each drafting poll tick, every action) re-ran the full loadCollections
// fan-out. Hoisting it means the bootstrap effect fires only when the book actually changes.
const noopOnBookChange = (): void => {};

export function useDeskDataState(activityOpen = false): DeskData {
  const [loading, setLoading] = useState(true);
  const { error, setError, clearError, fail } = useDeskError();
  const { toasts, pushToast, dismissToast } = useDeskToasts();

  const { books, bookId, setBook, createBook } = useDeskBooks(fail, setLoading);

  const chapterCreate = useDeskChapterCreate(fail);
  const collections = useDeskCollections(bookId, fail, setError, setLoading, noopOnBookChange);

  const refreshAll = useCallback(
    () => collections.refreshAll(bookId),
    [bookId, collections.refreshAll],
  );
  // Slim post-action reconciliation (scenes/pending/jobs, no manuscript/world) — see useDeskCollections.
  const refreshScenes = useCallback(
    () => collections.refreshScenes(bookId),
    [bookId, collections.refreshScenes],
  );
  const refreshManuscript = useCallback(
    () => collections.refreshManuscript(bookId),
    [bookId, collections.refreshManuscript],
  );

  const scene = useDeskActiveScene(fail, setError);

  const sceneActions = useDeskSceneActions(fail, setError, {
    bookId,
    activeSceneId: scene.activeSceneId,
    setJobs: collections.setJobs,
    setChapters: collections.setChapters,
    setDetail: scene.setDetail,
    openSceneById: scene.openSceneById,
    refreshScenes,
  });

  const jobs = useDeskJobs(
    bookId,
    collections.jobs,
    collections.setJobs,
    collections.loadCollections,
    collections.refreshScenes,
    pushToast,
  );
  // Drawer feed — gated: polls only while the drawer is open or the queue is busy.
  const { recentJobs, refreshRecentJobs } = useDeskRecentJobs(
    bookId,
    activityOpen,
    collections.jobs.running || collections.jobs.queued > 0,
  );
  // Central activity feed — same gating; the drawer's history + all cross-page events read from here.
  const { activityFeed, refreshActivity } = useDeskActivity(
    bookId,
    activityOpen,
    collections.jobs.running || collections.jobs.queued > 0,
  );
  const clearActivityFeed = useCallback(
    async (scope: "all" | "finished" = "finished") => {
      try {
        await Promise.all([
          // "finished" soft-hides only terminal job history; "all" clears everything showing (errors,
          // canon/packet/sweeper events, run lifecycle) — the latter is what "Clear all" needs.
          api.clearActivity({ scope, book_id: bookId ?? undefined }),
          api.clearFinishedJobs(bookId ?? undefined),
        ]);
        await Promise.all([refreshActivity(), refreshRecentJobs()]);
      } catch {
        pushToast({ tone: "error", message: "Couldn't clear activity" });
      }
    },
    [bookId, refreshActivity, refreshRecentJobs, pushToast],
  );

  // Queue control (Desk Control Round). Cancel repaints the drawer + queue count immediately;
  // pause flips optimistically off the authoritative response.
  const cancelJob = useCallback(
    async (jobId: string) => {
      try {
        const out = await api.cancelJob(jobId);
        pushToast({
          tone: "info",
          message: `Cancelled ${out.chapter_no != null ? `Ch ${out.chapter_no} · ` : ""}Scene ${out.scene_no ?? "?"}`,
        });
        collections.setJobs((j) => ({ ...j, queued: out.queued }));
        await refreshRecentJobs();
      } catch (e) {
        pushToast({
          tone: "error",
          message:
            e instanceof ApiError && e.status === 409
              ? "That job is already running — it can't be cancelled"
              : "Cancel failed",
        });
      }
    },
    [pushToast, collections.setJobs, refreshRecentJobs],
  );
  const setQueuePaused = useCallback(
    async (paused: boolean) => {
      try {
        const out = await api.pauseQueue(paused, bookId ?? undefined);
        collections.setJobs((j) => ({ ...j, queue_paused: out.queue_paused }));
        pushToast(
          paused
            ? {
                tone: "warn",
                message: "Queue paused — the current scene will finish; nothing new starts",
              }
            : {
                tone: "success",
                message: out.scheduled ? "Queue resumed — drafting restarted" : "Queue resumed",
              },
        );
      } catch {
        pushToast({ tone: "error", message: "Couldn't change the queue state" });
      }
    },
    [bookId, pushToast, collections.setJobs],
  );

  const world = useDeskWorld(
    fail,
    collections.setThreads,
    collections.setCharacters,
    collections.setCanon,
  );

  const rules = useDeskRules(fail, collections.setRuleProposals);

  const markup = useDeskMarkup(fail, scene.setAnnotations, scene.setSuggestions);

  const createAndPropose = useCallback(
    (chapterNo: number, pov: string, outline: string) =>
      chapterCreate.createAndPropose(bookId, chapterNo, pov, outline, collections.loadCollections),
    [bookId, collections.loadCollections, chapterCreate.createAndPropose],
  );

  const createThread = useCallback(
    (body: ThreadIn) => world.createThread(bookId, body),
    [bookId, world.createThread],
  );

  const upsertCharacter = useCallback(
    (name: string, body: CharacterStateIn) => world.upsertCharacter(bookId, name, body),
    [bookId, world.upsertCharacter],
  );

  const deleteCharacter = useCallback(
    (name: string) => world.deleteCharacter(bookId, name),
    [bookId, world.deleteCharacter],
  );

  const createCanon = useCallback(
    (body: CanonEntityIn) => world.createCanon(bookId, body),
    [bookId, world.createCanon],
  );

  const ingestCanon = useCallback(() => {
    // A rebuild replaces the corpus wholesale (new entity ids) — the once-per-session canon body
    // upgrade must re-run on the reload that follows, or palette body search goes dark.
    if (bookId) invalidateCanonBodies(bookId);
    return world.ingestCanon(bookId, collections.loadCollections);
  }, [bookId, collections.loadCollections, world.ingestCanon]);

  // Chapter edits (title/epigraph/POV/kind) surface in the compiled manuscript — mark the cached
  // compile stale so the next Manuscript visit refetches instead of serving it warm.
  const updateChapter = useCallback(
    async (chapterId: string, body: ChapterUpdateIn) => {
      collections.markManuscriptStale();
      await sceneActions.updateChapter(chapterId, body);
    },
    [collections.markManuscriptStale, sceneActions.updateChapter],
  );

  const distillRules = useCallback(
    (pov?: string) => rules.distillRules(bookId, pov),
    [bookId, rules.distillRules],
  );

  const addAnnotation = useCallback(
    (body: AnnotationIn) => markup.addAnnotation(scene.activeSceneId, body),
    [markup.addAnnotation, scene.activeSceneId],
  );

  const addSuggestion = useCallback(
    (body: SuggestionIn) => markup.addSuggestion(scene.activeSceneId, body),
    [markup.addSuggestion, scene.activeSceneId],
  );

  return useMemo(
    () => ({
      loading,
      error,
      clearError,
      books,
      bookId,
      setBook,
      chapters: collections.chapters,
      scenes: collections.scenes,
      latestScenes: collections.latestScenes,
      pending: collections.pending,
      manuscript: collections.manuscript,
      characters: collections.characters,
      canon: collections.canon,
      threads: collections.threads,
      ruleProposals: collections.ruleProposals,
      jobs: collections.jobs,
      failedJobs: jobs.failedJobs,
      jobsUnreachable: jobs.jobsUnreachable,
      activity: jobs.activity,
      activityFeed,
      refreshActivity,
      clearActivityFeed,
      recentJobs,
      refreshRecentJobs,
      toasts,
      pushToast,
      dismissToast,
      cancelJob,
      setQueuePaused,
      detail: scene.detail,
      versions: scene.versions,
      activeBeat: scene.activeBeat,
      activeSceneId: scene.activeSceneId,
      loadingScene: scene.loadingScene,
      missingSceneId: scene.missingSceneId,
      annotations: scene.annotations,
      suggestions: scene.suggestions,
      openSceneById: scene.openSceneById,
      refreshAll,
      refreshManuscript,
      createBook,
      updateChapter,
      creatingChapter: chapterCreate.creating,
      createAndPropose,
      retryFailed: sceneActions.retryFailed,
      clearFailed: sceneActions.clearFailed,
      clearDraftScenes: sceneActions.clearDraftScenes,
      deleteScenes: sceneActions.deleteScenes,
      runBulk: sceneActions.runBulk,
      decide: sceneActions.decide,
      revertScene: sceneActions.revertScene,
      resolveContinuity: sceneActions.resolveContinuity,
      setExemplar: sceneActions.setExemplar,
      restartRedraft: sceneActions.restartRedraft,
      draftNext: sceneActions.draftNext,
      createThread,
      addThreadBeat: world.addThreadBeat,
      deleteThread: world.deleteThread,
      upsertCharacter,
      deleteCharacter,
      createCanon,
      updateCanon: world.updateCanon,
      deleteCanon: world.deleteCanon,
      ingestCanon,
      distillRules,
      decideRuleProposal: rules.decideRuleProposal,
      addAnnotation,
      deleteAnnotation: markup.deleteAnnotation,
      addSuggestion,
      decideSuggestion: markup.decideSuggestion,
      deleteSuggestion: markup.deleteSuggestion,
    }),
    [
      loading,
      error,
      clearError,
      books,
      bookId,
      setBook,
      collections,
      jobs,
      activityFeed,
      refreshActivity,
      clearActivityFeed,
      recentJobs,
      refreshRecentJobs,
      toasts,
      pushToast,
      dismissToast,
      cancelJob,
      setQueuePaused,
      scene,
      refreshAll,
      refreshManuscript,
      createBook,
      sceneActions,
      updateChapter,
      chapterCreate.creating,
      createAndPropose,
      createThread,
      world,
      upsertCharacter,
      deleteCharacter,
      createCanon,
      ingestCanon,
      distillRules,
      rules.decideRuleProposal,
      addAnnotation,
      markup,
      addSuggestion,
    ],
  );
}

const DeskDataContext = createContext<DeskData | null>(null);

export function DeskDataProvider({
  children,
  activityOpen = false,
}: {
  children: ReactNode;
  // Injected from Providers: the drawer flag lives in DeskProvider, which mounts BELOW this
  // provider, so it arrives as a prop rather than via useDesk().
  activityOpen?: boolean;
}) {
  const value = useDeskDataState(activityOpen);
  return <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>;
}

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (!ctx) throw new Error("useDeskData must be used inside <DeskDataProvider>");
  return ctx;
}
