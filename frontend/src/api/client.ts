import type { ContinuityResolveIn, DecisionIn, SceneDetail, SceneOut } from "../types";

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
};
