import type {
  AnnotationIn,
  AnnotationOut,
  ApproveBeatsOut,
  BeatOut,
  BeatUpdate,
  BookCreate,
  BookOut,
  CanonOut,
  ChapterOut,
  CharacterOut,
  ContinuityResolveIn,
  DecisionIn,
  ManuscriptOut,
  RunPlanIn,
  RunPlanOut,
  SceneDetail,
  SceneOut,
  SceneVersionOut,
  SuggestionDecisionIn,
  SuggestionIn,
  SuggestionOut,
  ThreadIn,
  ThreadOut,
  ThreadUpdateIn,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const api = {
  pending: () => http<SceneOut[]>("/scenes/pending"),
  scene: (id: string) => http<SceneDetail>(`/scenes/${id}`),
  decide: (id: string, body: DecisionIn) =>
    http<{ scene: string; status: string }>(`/scenes/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resolveContinuity: (id: string, body: ContinuityResolveIn) =>
    http<{ resolved: string; job: string | null }>(`/scenes/${id}/continuity/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Gate 1 (planning)
  books: () => http<BookOut[]>("/books"),
  createBook: (body: BookCreate) =>
    http<BookOut>("/books", { method: "POST", body: JSON.stringify(body) }),
  startRun: (body: RunPlanIn) =>
    http<RunPlanOut>("/runs", { method: "POST", body: JSON.stringify(body) }),
  updateBeat: (beatId: string, body: BeatUpdate) =>
    http<BeatOut>(`/beats/${beatId}`, { method: "PUT", body: JSON.stringify(body) }),
  approveBeats: (chapterId: string) =>
    http<ApproveBeatsOut>(`/chapters/${chapterId}/beats/approve`, { method: "POST" }),

  // History / version browsing
  chapters: (bookId: string) =>
    http<ChapterOut[]>(`/chapters?book_id=${encodeURIComponent(bookId)}`),
  chapterBeats: (chapterId: string) => http<BeatOut[]>(`/chapters/${chapterId}/beats`),
  chapterScenes: (chapterId: string) => http<SceneOut[]>(`/chapters/${chapterId}/scenes`),
  sceneVersions: (sceneId: string) => http<SceneVersionOut[]>(`/scenes/${sceneId}/versions`),

  // Manuscript reading
  manuscript: (bookId: string) => http<ManuscriptOut>(`/books/${bookId}/manuscript`),

  // Ledger read surfaces (PR-B)
  characters: (bookId: string) => http<CharacterOut[]>(`/books/${bookId}/characters`),
  canon: (bookId: string, kind?: string) =>
    http<CanonOut[]>(`/books/${bookId}/canon${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),

  // Threads (PR-C, author-curated)
  threads: (bookId: string) => http<ThreadOut[]>(`/books/${bookId}/threads`),
  createThread: (bookId: string, body: ThreadIn) =>
    http<ThreadOut>(`/books/${bookId}/threads`, { method: "POST", body: JSON.stringify(body) }),
  updateThread: (threadId: string, body: ThreadUpdateIn) =>
    http<ThreadOut>(`/threads/${threadId}`, { method: "PUT", body: JSON.stringify(body) }),

  // Annotations (PR-C, quote-anchored margin notes)
  annotations: (sceneId: string) => http<AnnotationOut[]>(`/scenes/${sceneId}/annotations`),
  createAnnotation: (sceneId: string, body: AnnotationIn) =>
    http<AnnotationOut>(`/scenes/${sceneId}/annotations`, { method: "POST", body: JSON.stringify(body) }),
  deleteAnnotation: (sceneId: string, annotationId: string) =>
    http<{ deleted: string }>(`/scenes/${sceneId}/annotations/${annotationId}`, { method: "DELETE" }),

  // Suggestions (PR-C, track-changes)
  suggestions: (sceneId: string) => http<SuggestionOut[]>(`/scenes/${sceneId}/suggestions`),
  createSuggestion: (sceneId: string, body: SuggestionIn) =>
    http<SuggestionOut>(`/scenes/${sceneId}/suggestions`, { method: "POST", body: JSON.stringify(body) }),
  decideSuggestion: (suggestionId: string, body: SuggestionDecisionIn) =>
    http<SuggestionOut>(`/suggestions/${suggestionId}/decision`, { method: "POST", body: JSON.stringify(body) }),
};
