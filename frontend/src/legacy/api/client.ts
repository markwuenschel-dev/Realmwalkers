import type {
  ApproveBeatsOut,
  BeatOut,
  BeatUpdate,
  BookCreate,
  BookOut,
  ChapterOut,
  ContinuityResolveIn,
  DecisionIn,
  ManuscriptOut,
  RunPlanIn,
  RunPlanOut,
  SceneDetail,
  SceneOut,
  SceneVersionOut,
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
};
