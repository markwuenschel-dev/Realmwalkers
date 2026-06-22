// HTTP client for the Writers' Desk. One thin fetch wrapper over the FastAPI boundary; every screen
// reads real story state through here. Base URL comes from VITE_API_BASE (defaults to local dev).
import type {
  BeatOut,
  BookIn,
  BookOut,
  CanonEntityOut,
  ChapterOut,
  CharacterStateOut,
  ContinuityResolveIn,
  DecisionIn,
  DraftNextOut,
  JobsStatusOut,
  ManuscriptOut,
  RunStartIn,
  RunStartOut,
  SceneDetail,
  SceneOut,
  SceneVersionOut,
  ThreadBeatIn,
  ThreadIn,
  ThreadOut,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body}` : ""}`);
  }
  // 204 / empty bodies (rare here) shouldn't blow up JSON.parse.
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

const qs = (params: Record<string, string | undefined>): string => {
  const pairs = Object.entries(params).filter(([, v]) => v != null) as [string, string][];
  return pairs.length ? `?${new URLSearchParams(pairs).toString()}` : "";
};

export const api = {
  // --- review inbox -------------------------------------------------------------------------------
  pending: () => http<SceneOut[]>("/scenes/pending"),
  scene: (id: string) => http<SceneDetail>(`/scenes/${id}`),
  sceneVersions: (id: string) => http<SceneVersionOut[]>(`/scenes/${id}/versions`),
  decide: (id: string, body: DecisionIn) =>
    http<{ scene: string; status: string; next_job: string | null }>(
      `/scenes/${id}/decision`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  resolveContinuity: (id: string, body: ContinuityResolveIn) =>
    http<{ resolved: string; job: string | null }>(
      `/scenes/${id}/continuity/resolve`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  // --- drafting (browser-driven worker) -----------------------------------------------------------
  jobsStatus: () => http<JobsStatusOut>("/jobs/status"),
  draftNext: () => http<DraftNextOut>("/jobs/draft-next", { method: "POST" }),

  // --- gate 1: books, runs, chapters, beats -------------------------------------------------------
  books: () => http<BookOut[]>("/books"),
  createBook: (body: BookIn) =>
    http<BookOut>("/books", { method: "POST", body: JSON.stringify(body) }),
  startRun: (body: RunStartIn) =>
    http<RunStartOut>("/runs", { method: "POST", body: JSON.stringify(body) }),
  approveBeats: (chapterId: string) =>
    http<{ chapter_id: string; approved: number; jobs: string[] }>(
      `/chapters/${chapterId}/beats/approve`,
      { method: "POST" },
    ),

  // --- chapters + history -------------------------------------------------------------------------
  chapters: (bookId: string) => http<ChapterOut[]>(`/chapters${qs({ book_id: bookId })}`),
  chapterBeats: (chapterId: string) => http<BeatOut[]>(`/chapters/${chapterId}/beats`),
  chapterScenes: (chapterId: string) => http<SceneOut[]>(`/chapters/${chapterId}/scenes`),

  // --- manuscript ---------------------------------------------------------------------------------
  manuscript: (bookId: string) => http<ManuscriptOut>(`/books/${bookId}/manuscript`),

  // --- world ledger -------------------------------------------------------------------------------
  characters: (bookId: string) => http<CharacterStateOut[]>(`/books/${bookId}/characters`),
  canon: (bookId: string, kind?: string) =>
    http<CanonEntityOut[]>(`/books/${bookId}/canon${qs({ kind })}`),

  // --- world threads (curated) --------------------------------------------------------------------
  threads: (bookId: string) => http<ThreadOut[]>(`/books/${bookId}/threads`),
  createThread: (bookId: string, body: ThreadIn) =>
    http<ThreadOut>(`/books/${bookId}/threads`, { method: "POST", body: JSON.stringify(body) }),
  addThreadBeat: (threadId: string, body: ThreadBeatIn) =>
    http<ThreadOut>(`/threads/${threadId}/beats`, { method: "POST", body: JSON.stringify(body) }),
  deleteThread: (threadId: string) =>
    http<{ deleted: string }>(`/threads/${threadId}`, { method: "DELETE" }),
};
