"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useDeskActiveScene } from "./hooks/useDeskActiveScene";
import { useDeskBooks } from "./hooks/useDeskBooks";
import { useDeskCollections } from "./hooks/useDeskCollections";
import { useDeskError } from "./hooks/useDeskError";
import { useDeskJobs } from "./hooks/useDeskJobs";
import { useDeskMarkup } from "./hooks/useDeskMarkup";
import { useDeskPlanning } from "./hooks/useDeskPlanning";
import { useDeskRules } from "./hooks/useDeskRules";
import { useDeskSceneActions } from "./hooks/useDeskSceneActions";
import { useDeskWorld } from "./hooks/useDeskWorld";
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
  ActivityEntry,
  FailedJobOut,
  JobsStatusOut,
  ManuscriptOut,
  RuleProposalDecisionIn,
  RuleProposalOut,
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
    chapterNo: number,
    pov: string,
    outline: string,
    maxBeats?: number,
    targetWords?: number,
  ) => Promise<RunStartOut | null>;
  planningChapters: Set<number>;
  approveAndDraft: (chapterId: string, beatIds?: string[]) => Promise<void>;
  retryFailed: () => Promise<import("./types").RetryFailedOut | null>;
  clearFailed: () => Promise<import("./types").ClearFailedOut | null>;
  runBulk: (ids: string[], fn: (id: string) => Promise<unknown>) => Promise<void>;
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
  distillRules: (pov?: string) => Promise<number>;
  decideRuleProposal: (id: string, body: RuleProposalDecisionIn) => Promise<void>;
  addAnnotation: (body: AnnotationIn) => Promise<void>;
  deleteAnnotation: (id: string) => Promise<void>;
  addSuggestion: (body: SuggestionIn) => Promise<void>;
  decideSuggestion: (id: string, status: SuggestionStatus) => Promise<void>;
  deleteSuggestion: (id: string) => Promise<void>;
}

export function useDeskDataState(): DeskData {
  const [loading, setLoading] = useState(true);
  const { error, setError, clearError, fail } = useDeskError();

  const { books, bookId, setBook, createBook } = useDeskBooks(fail, setLoading);

  const planning = useDeskPlanning(fail);
  const collections = useDeskCollections(
    bookId,
    fail,
    setError,
    setLoading,
    planning.resetPlanningOnBookChange,
  );

  const refreshAll = useCallback(
    () => collections.refreshAll(bookId),
    [bookId, collections.refreshAll],
  );

  const scene = useDeskActiveScene(fail, setError);

  const sceneActions = useDeskSceneActions(fail, setError, {
    bookId,
    activeSceneId: scene.activeSceneId,
    setJobs: collections.setJobs,
    setChapters: collections.setChapters,
    setDetail: scene.setDetail,
    openSceneById: scene.openSceneById,
    refreshAll,
  });

  const jobs = useDeskJobs(
    bookId,
    collections.jobs,
    collections.setJobs,
    collections.loadCollections,
  );

  const world = useDeskWorld(
    fail,
    collections.setThreads,
    collections.setCharacters,
    collections.setCanon,
  );

  const rules = useDeskRules(fail, collections.setRuleProposals);

  const markup = useDeskMarkup(fail, scene.setAnnotations, scene.setSuggestions);

  const startRun = useCallback(
    (chapterNo: number, pov: string, outline: string, maxBeats?: number, targetWords?: number) =>
      planning.startRun(
        bookId,
        chapterNo,
        pov,
        outline,
        collections.loadCollections,
        maxBeats,
        targetWords,
      ),
    [bookId, collections.loadCollections, planning.startRun],
  );

  const approveAndDraft = useCallback(
    (chapterId: string, beatIds?: string[]) =>
      planning.approveAndDraft(chapterId, beatIds, sceneActions.draftNext, refreshAll),
    [planning.approveAndDraft, refreshAll, sceneActions.draftNext],
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

  const ingestCanon = useCallback(
    () => world.ingestCanon(bookId, collections.loadCollections),
    [bookId, collections.loadCollections, world.ingestCanon],
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
      detail: scene.detail,
      versions: scene.versions,
      activeBeat: scene.activeBeat,
      activeSceneId: scene.activeSceneId,
      annotations: scene.annotations,
      suggestions: scene.suggestions,
      openSceneById: scene.openSceneById,
      refreshAll,
      createBook,
      updateChapter: sceneActions.updateChapter,
      startRun,
      planningChapters: planning.planningChapters,
      approveAndDraft,
      retryFailed: sceneActions.retryFailed,
      clearFailed: sceneActions.clearFailed,
      runBulk: sceneActions.runBulk,
      decide: sceneActions.decide,
      revertScene: sceneActions.revertScene,
      resolveContinuity: sceneActions.resolveContinuity,
      setExemplar: sceneActions.setExemplar,
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
      scene,
      refreshAll,
      createBook,
      sceneActions,
      startRun,
      planning.planningChapters,
      approveAndDraft,
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

export function DeskDataProvider({ children }: { children: ReactNode }) {
  const value = useDeskDataState();
  return <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>;
}

export function useDeskData(): DeskData {
  const ctx = useContext(DeskDataContext);
  if (!ctx) throw new Error("useDeskData must be used inside <DeskDataProvider>");
  return ctx;
}
