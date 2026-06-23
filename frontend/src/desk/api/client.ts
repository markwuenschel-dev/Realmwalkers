// HTTP client for the Writers' Desk. One thin fetch wrapper over the FastAPI boundary; every screen
// reads real story state through here. Base URL comes from VITE_API_BASE (defaults to local dev).
import type {
  AnnotationIn,
  AnnotationOut,
  BeatCreateIn,
  BeatOut,
  BeatUpdateIn,
  BookIn,
  BookOut,
  CanonEntityOut,
  ChapterOut,
  CharacterStateOut,
  ContinuityResolveIn,
  DecisionIn,
  DocDetail,
  DocMeta,
  DraftNextOut,
  JobsStatusOut,
  ManuscriptOut,
  RunStartIn,
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

// API base resolution, in order: explicit VITE_API_BASE override → in a production build, same-origin
// (relative ""), because FastAPI serves this bundle itself, so there's no separate host/CORS/localhost
// → in dev, whatever host the page was loaded from on :8000 (so a LAN IP works, not just localhost).
const BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.PROD ? "" : `http://${window.location.hostname}:8000`);

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
  // mark/unmark a scene as a voice exemplar for its POV (the drafter few-shots on it)
  setExemplar: (id: string, enabled: boolean) =>
    http<{ scene: string; is_exemplar: boolean }>(
      `/scenes/${id}/exemplar`,
      { method: "POST", body: JSON.stringify({ enabled }) },
    ),

  // --- drafting (browser-driven worker) -----------------------------------------------------------
  // book_id scopes the indicator to the active book, so another book's drafting doesn't light it up.
  jobsStatus: (bookId?: string) => http<JobsStatusOut>(`/jobs/status${qs({ book_id: bookId })}`),
  draftNext: (bookId?: string) =>
    http<DraftNextOut>(`/jobs/draft-next${qs({ book_id: bookId })}`, { method: "POST" }),

  // --- gate 1: books, runs, chapters, beats -------------------------------------------------------
  books: () => http<BookOut[]>("/books"),
  createBook: (body: BookIn) =>
    http<BookOut>("/books", { method: "POST", body: JSON.stringify(body) }),
  startRun: (body: RunStartIn) =>
    http<RunStartOut>("/runs", { method: "POST", body: JSON.stringify(body) }),
  approveBeats: (chapterId: string, beatIds?: string[]) =>
    http<{ chapter_id: string; approved: number; jobs: string[] }>(
      `/chapters/${chapterId}/beats/approve`,
      { method: "POST", body: JSON.stringify({ beat_ids: beatIds ?? null }) },
    ),
  updateBeat: (beatId: string, body: BeatUpdateIn) =>
    http<BeatOut>(`/beats/${beatId}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteBeat: (beatId: string) =>
    http<{ deleted: string }>(`/beats/${beatId}`, { method: "DELETE" }),
  createBeat: (chapterId: string, body: BeatCreateIn) =>
    http<BeatOut>(`/chapters/${chapterId}/beats`, { method: "POST", body: JSON.stringify(body) }),

  // --- chapters + history -------------------------------------------------------------------------
  chapters: (bookId: string) => http<ChapterOut[]>(`/chapters${qs({ book_id: bookId })}`),
  chapterBeats: (chapterId: string) => http<BeatOut[]>(`/chapters/${chapterId}/beats`),
  chapterScenes: (chapterId: string) => http<SceneOut[]>(`/chapters/${chapterId}/scenes`),

  // --- manuscript ---------------------------------------------------------------------------------
  manuscript: (bookId: string) => http<ManuscriptOut>(`/books/${bookId}/manuscript`),

  // --- canon / planning / style docs (read-only Domain-B markdown) --------------------------------
  // route is /library (not /docs — FastAPI serves Swagger UI at /docs).
  docs: () => http<DocMeta[]>("/library"),
  // path segments are preserved (the route param is a :path); encode each so spaces/specials survive.
  doc: (path: string) =>
    http<DocDetail>(`/library/${path.split("/").map(encodeURIComponent).join("/")}`),

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

  // --- scene markup: annotations + suggestions ----------------------------------------------------
  annotations: (sceneId: string) => http<AnnotationOut[]>(`/scenes/${sceneId}/annotations`),
  createAnnotation: (sceneId: string, body: AnnotationIn) =>
    http<AnnotationOut>(`/scenes/${sceneId}/annotations`, { method: "POST", body: JSON.stringify(body) }),
  deleteAnnotation: (id: string) =>
    http<{ deleted: string }>(`/annotations/${id}`, { method: "DELETE" }),
  suggestions: (sceneId: string) => http<SuggestionOut[]>(`/scenes/${sceneId}/suggestions`),
  createSuggestion: (sceneId: string, body: SuggestionIn) =>
    http<SuggestionOut>(`/scenes/${sceneId}/suggestions`, { method: "POST", body: JSON.stringify(body) }),
  decideSuggestion: (id: string, status: SuggestionStatus) =>
    http<SuggestionOut>(`/suggestions/${id}/decision`, { method: "POST", body: JSON.stringify({ status }) }),
  deleteSuggestion: (id: string) =>
    http<{ deleted: string }>(`/suggestions/${id}`, { method: "DELETE" }),
};
