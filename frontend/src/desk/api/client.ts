// HTTP client for the Writers' Desk. One thin fetch wrapper over the FastAPI boundary; every screen
// reads real story state through here. Requests go to the same-origin Next BFF proxy at /api/desk,
// which forwards to FastAPI (server-side API_BASE) — so the browser never needs the backend host.
import type {
  AnnotationIn,
  AnnotationOut,
  BatchRunOut,
  BatchRunStart,
  BeatCreateIn,
  BeatOut,
  BeatUpdateIn,
  BookIn,
  BookOut,
  BookTelemetryOut,
  ChapterTelemetryOut,
  CanonEntityIn,
  CanonEntityOut,
  CanonEntityUpdateIn,
  CanonIngestOut,
  ChapterCreateIn,
  ChapterOut,
  ChapterUpdateIn,
  CharacterStateIn,
  CharacterStateOut,
  ClearDraftScenesOut,
  ClearFailedOut,
  DeleteSceneOut,
  ContinuityResolveIn,
  DecisionIn,
  DocDetail,
  DocMeta,
  DraftAttemptOut,
  DraftNextOut,
  FailedJobOut,
  JobsStatusOut,
  KnowledgeFactOut,
  LlmCallListOut,
  LlmCallOut,
  ManuscriptOut,
  ModelSettingOut,
  ModelSettingsOut,
  AgentOpsOut,
  AgentStatsListOut,
  SmokeTestOut,
  HumanSceneIn,
  PacketOut,
  PacketProposeOut,
  PacketUpdateIn,
  PacketWarnings,
  RedraftIn,
  RetryFailedOut,
  RuleProposalDecisionIn,
  RuleProposalOut,
  RunStartIn,
  RunStartOut,
  RunCompareOut,
  RunTelemetryOut,
  SceneDetail,
  SceneOut,
  ScenePacketDeriveStatusOut,
  ScenePacketOut,
  ScenePacketUpdateIn,
  SceneVersionOut,
  SuggestionIn,
  SuggestionOut,
  SuggestionStatus,
  TelemetryDeleteOut,
  TelemetryProblemsOut,
  ThreadBeatIn,
  ThreadIn,
  ThreadOut,
} from "./types";

// Same-origin proxy mount. The Next route handler at /api/desk/[...path] forwards to FastAPI, so
// there is no separate host, CORS, or localhost concern in the browser; point the backend at the
// API_BASE env var on the server instead (see src/app/api/desk/[...path]/route.ts).
const BASE = "/api/desk";

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
  revertScene: (id: string) => http<SceneOut>(`/scenes/${id}/revert`, { method: "POST" }),
  deleteScene: (id: string) => http<DeleteSceneOut>(`/scenes/${id}`, { method: "DELETE" }),
  decide: (id: string, body: DecisionIn) =>
    http<{ scene: string; status: string; next_job: string | null }>(`/scenes/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resolveContinuity: (id: string, body: ContinuityResolveIn) =>
    http<{ resolved: string; job: string | null }>(`/scenes/${id}/continuity/resolve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // mark/unmark a scene as a voice exemplar for its POV (the drafter few-shots on it)
  setExemplar: (id: string, enabled: boolean) =>
    http<{ scene: string; is_exemplar: boolean }>(`/scenes/${id}/exemplar`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  // --- drafting (browser-driven worker) -----------------------------------------------------------
  // book_id scopes the indicator to the active book, so another book's drafting doesn't light it up.
  jobsStatus: (bookId?: string) => http<JobsStatusOut>(`/jobs/status${qs({ book_id: bookId })}`),
  jobsFailed: (bookId?: string) => http<FailedJobOut[]>(`/jobs/failed${qs({ book_id: bookId })}`),
  draftNext: (bookId?: string) =>
    http<DraftNextOut>(`/jobs/draft-next${qs({ book_id: bookId })}`, { method: "POST" }),
  retryFailed: (bookId?: string) =>
    http<RetryFailedOut>(`/jobs/retry-failed${qs({ book_id: bookId })}`, { method: "POST" }),
  clearFailed: (bookId?: string, chapterId?: string) =>
    http<ClearFailedOut>(`/jobs/clear-failed${qs({ book_id: bookId, chapter_id: chapterId })}`, {
      method: "POST",
    }),

  // --- gate 1: books, runs, chapters, beats -------------------------------------------------------
  books: () => http<BookOut[]>("/books"),
  createBook: (body: BookIn) =>
    http<BookOut>("/books", { method: "POST", body: JSON.stringify(body) }),
  startRun: (body: RunStartIn) =>
    http<RunStartOut>("/runs", { method: "POST", body: JSON.stringify(body) }),
  // Plan several chapters in one call; auto_draft runs gate 1 → draft without a manual approve.
  batchRun: (body: BatchRunStart) =>
    http<BatchRunOut>("/runs/batch", { method: "POST", body: JSON.stringify(body) }),
  approveBeats: (chapterId: string, beatIds?: string[]) =>
    http<{ chapter_id: string; approved: number; message: string }>(
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
  createChapter: (body: ChapterCreateIn) =>
    http<ChapterOut>("/chapters", { method: "POST", body: JSON.stringify(body) }),
  updateChapter: (chapterId: string, body: ChapterUpdateIn) =>
    http<ChapterOut>(`/chapters/${chapterId}`, { method: "PATCH", body: JSON.stringify(body) }),
  chapterBeats: (chapterId: string) => http<BeatOut[]>(`/chapters/${chapterId}/beats`),
  chapterScenes: (chapterId: string) => http<SceneOut[]>(`/chapters/${chapterId}/scenes`),
  createHumanScene: (chapterId: string, body: HumanSceneIn) =>
    http<SceneOut>(`/chapters/${chapterId}/scenes`, { method: "POST", body: JSON.stringify(body) }),
  redraftScenes: (chapterId: string, sceneIds: string[]) =>
    http<import("./types").DraftScheduleOut>(`/chapters/${chapterId}/scenes/redraft`, {
      method: "POST",
      body: JSON.stringify({ scene_ids: sceneIds } satisfies RedraftIn),
    }),
  draftChapter: (chapterId: string) =>
    http<import("./types").DraftScheduleOut>(`/chapters/${chapterId}/draft`, {
      method: "POST",
    }),
  draftReadiness: (chapterId: string) =>
    http<import("./types").DraftReadinessOut>(`/chapters/${chapterId}/draft/readiness`),

  // --- manuscript ---------------------------------------------------------------------------------
  manuscript: (bookId: string) => http<ManuscriptOut>(`/books/${bookId}/manuscript`),
  clearDraftScenes: (bookId: string, chapterId?: string) =>
    http<ClearDraftScenesOut>(
      `/books/${bookId}/scenes/clear-draft${qs({ chapter_id: chapterId })}`,
      { method: "POST" },
    ),

  // --- contract-first drafting: chapter knowledge packets (Phase 1) -------------------------------
  // GET may 404 (no packet yet); callers treat that as "none".
  packet: (chapterId: string) => http<PacketOut>(`/chapters/${chapterId}/packet`),
  // Author+QA now run in the background (so a tab switch never loses the work). POST kicks it off and
  // returns the live status; poll packetStatus until running flips false, then refetch the packet.
  proposePacket: (chapterId: string) =>
    http<PacketProposeOut>(`/chapters/${chapterId}/packet`, { method: "POST" }),
  packetStatus: (chapterId: string) =>
    http<PacketProposeOut>(`/chapters/${chapterId}/packet/status`),
  updatePacket: (chapterId: string, body: PacketUpdateIn) =>
    http<PacketOut>(`/chapters/${chapterId}/packet`, { method: "PUT", body: JSON.stringify(body) }),
  approvePacket: (chapterId: string) =>
    http<PacketOut>(`/chapters/${chapterId}/packet/approve`, { method: "POST" }),
  deletePacket: (chapterId: string) =>
    http<{ deleted_chapter_packets: number; deleted_scene_packets: number }>(
      `/chapters/${chapterId}/packet`,
      { method: "DELETE" },
    ),

  // --- scene packets (scene-local contract; derive runs Author+QA per scene in the background) -----
  scenePackets: (chapterId: string) =>
    http<ScenePacketOut[]>(`/chapters/${chapterId}/scene-packets`),
  deriveScenePackets: (chapterId: string) =>
    http<ScenePacketDeriveStatusOut>(`/chapters/${chapterId}/scene-packets/derive`, {
      method: "POST",
    }),
  deriveStatus: (chapterId: string) =>
    http<ScenePacketDeriveStatusOut>(`/chapters/${chapterId}/scene-packets/derive/status`),
  updateScenePacket: (id: string, body: ScenePacketUpdateIn) =>
    http<ScenePacketOut>(`/scene-packets/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  qaScenePacket: (id: string) =>
    http<{ packet_id: string; verdict: string; warnings: PacketWarnings | null }>(
      `/scene-packets/${id}/qa`,
      { method: "POST" },
    ),
  approveScenePacket: (id: string) =>
    http<ScenePacketOut>(`/scene-packets/${id}/approve`, { method: "POST" }),
  approveScenePackets: (chapterId: string, packetIds?: string[]) =>
    http<ScenePacketOut[]>(`/chapters/${chapterId}/scene-packets/approve`, {
      method: "POST",
      body: JSON.stringify({ packet_ids: packetIds ?? null }),
    }),
  markScenePacketsStale: (chapterId: string, packetIds?: string[]) =>
    http<ScenePacketOut[]>(`/chapters/${chapterId}/scene-packets/mark-stale`, {
      method: "POST",
      body: JSON.stringify({ packet_ids: packetIds ?? null }),
    }),
  deleteScenePacket: (id: string) =>
    http<{ deleted: string; jobs_purged: number }>(`/scene-packets/${id}`, { method: "DELETE" }),
  deleteScenePackets: (chapterId: string) =>
    http<{ deleted: number; jobs_purged: number }>(`/chapters/${chapterId}/scene-packets`, {
      method: "DELETE",
    }),

  // --- LLM call telemetry (persisted per-call cost/cache, aggregated) -----------------------------
  chapterTelemetry: (chapterId: string) =>
    http<ChapterTelemetryOut>(`/chapters/${chapterId}/telemetry`),
  // by_run is paginated (newest first); limit/offset page it while totals/by_chapter/by_stage/by_model
  // stay full-book. run_total carries the unsliced run count so callers know when to stop paging.
  bookTelemetry: (bookId: string, opts?: { limit?: number; offset?: number }) =>
    http<BookTelemetryOut>(
      `/books/${bookId}/telemetry${qs({
        limit: opts?.limit != null ? String(opts.limit) : undefined,
        offset: opts?.offset != null ? String(opts.offset) : undefined,
      })}`,
    ),
  runTelemetry: (runId: string) => http<RunTelemetryOut>(`/runs/${runId}/telemetry`),
  llmCalls: (opts?: {
    book_id?: string;
    chapter_id?: string;
    run_id?: string;
    scene_no?: number;
    stage?: string;
    stage_prefix?: string;
    stages?: string;
    model?: string;
    truncated?: boolean;
    errors_only?: boolean;
    problems_only?: boolean;
    fallbacks_only?: boolean;
    min_latency_ms?: number;
    min_input_tokens?: number;
    cache_miss_only?: boolean;
    limit?: number;
    offset?: number;
  }) =>
    http<LlmCallListOut>(
      `/llm-calls${qs({
        book_id: opts?.book_id,
        chapter_id: opts?.chapter_id,
        run_id: opts?.run_id,
        scene_no: opts?.scene_no != null ? String(opts.scene_no) : undefined,
        stage: opts?.stage,
        stage_prefix: opts?.stage_prefix,
        stages: opts?.stages,
        model: opts?.model,
        truncated: opts?.truncated != null ? String(opts.truncated) : undefined,
        errors_only: opts?.errors_only != null ? String(opts.errors_only) : undefined,
        problems_only: opts?.problems_only != null ? String(opts.problems_only) : undefined,
        fallbacks_only: opts?.fallbacks_only != null ? String(opts.fallbacks_only) : undefined,
        min_latency_ms: opts?.min_latency_ms != null ? String(opts.min_latency_ms) : undefined,
        min_input_tokens:
          opts?.min_input_tokens != null ? String(opts.min_input_tokens) : undefined,
        cache_miss_only: opts?.cache_miss_only != null ? String(opts.cache_miss_only) : undefined,
        limit: opts?.limit != null ? String(opts.limit) : undefined,
        offset: opts?.offset != null ? String(opts.offset) : undefined,
      })}`,
    ),
  llmCall: (callId: string) => http<LlmCallOut>(`/llm-calls/${callId}`),
  telemetryProblems: (bookId: string) =>
    http<TelemetryProblemsOut>(`/books/${bookId}/telemetry/problems`),
  compareRuns: (bookId: string, runA: string, runB: string) =>
    http<RunCompareOut>(`/books/${bookId}/telemetry/compare${qs({ run_a: runA, run_b: runB })}`),
  deleteBookTelemetry: (bookId: string) =>
    http<TelemetryDeleteOut>(`/books/${bookId}/telemetry`, { method: "DELETE" }),
  deleteRunTelemetry: (bookId: string, runId: string) =>
    http<TelemetryDeleteOut>(`/books/${bookId}/telemetry/runs/${runId}`, { method: "DELETE" }),
  deleteAllTelemetry: (confirm: string) =>
    http<TelemetryDeleteOut>("/telemetry", {
      method: "DELETE",
      body: JSON.stringify({ confirm }),
    }),

  // --- draft-attempt provenance (preserved prose stages for a scene) ------------------------------
  draftAttempts: (sceneId: string) => http<DraftAttemptOut[]>(`/scenes/${sceneId}/draft-attempts`),

  // --- knowledge ledger ---------------------------------------------------------------------------
  knowledge: (bookId: string) => http<KnowledgeFactOut[]>(`/books/${bookId}/knowledge`),

  // --- canon / planning / style docs (read-only Domain-B markdown) --------------------------------
  // route is /library (not /docs — FastAPI serves Swagger UI at /docs).
  docs: () => http<DocMeta[]>("/library"),
  // path segments are preserved (the route param is a :path); encode each so spaces/specials survive.
  doc: (path: string) =>
    http<DocDetail>(`/library/${path.split("/").map(encodeURIComponent).join("/")}`),

  // --- runtime model selection per agent ----------------------------------------------------------
  modelSettings: () => http<ModelSettingsOut>("/settings/models"),
  setModel: (setting: string, tier: string, provider: string = "anthropic") =>
    http<ModelSettingOut>("/settings/models", {
      method: "PUT",
      body: JSON.stringify({ setting, tier, provider }),
    }),

  // --- agent operations panel ---------------------------------------------------------------------
  agentOps: () => http<AgentOpsOut>("/settings/agents"),
  applyPreset: (presetId: string) =>
    http<AgentOpsOut>(`/settings/presets/${encodeURIComponent(presetId)}`, { method: "PUT" }),
  setAgentPolicy: (
    setting: string,
    body: {
      fallback_tier?: string | null;
      never_fallback?: string[] | null;
      semantic_escalation?: boolean | null;
      quality_level?: string | null;
      permissions?: { auto_run?: boolean } | null;
    },
  ) =>
    http<AgentOpsOut>(`/settings/agents/${encodeURIComponent(setting)}/policy`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  agentStats: () => http<AgentStatsListOut>("/settings/agents/stats"),
  smokeTest: (opts?: { agents?: string[]; live?: boolean }) =>
    http<SmokeTestOut>("/settings/agents/smoke-test", {
      method: "POST",
      body: JSON.stringify(opts ?? {}),
    }),
  saveCustomPreset: (label: string, description?: string) =>
    http<AgentOpsOut>("/settings/presets/custom", {
      method: "POST",
      body: JSON.stringify({ label, description }),
    }),
  deleteCustomPreset: (presetId: string) =>
    http<AgentOpsOut>(`/settings/presets/${encodeURIComponent(presetId)}`, { method: "DELETE" }),
  setAgentGlobals: (body: { scene_token_budget?: number; scene_time_budget_s?: number }) =>
    http<AgentOpsOut>("/settings/agents/globals", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  // --- world ledger -------------------------------------------------------------------------------
  characters: (bookId: string) => http<CharacterStateOut[]>(`/books/${bookId}/characters`),
  upsertCharacter: (bookId: string, name: string, body: CharacterStateIn) =>
    http<CharacterStateOut>(`/books/${bookId}/characters/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteCharacter: (bookId: string, name: string) =>
    http<{ deleted: string }>(`/books/${bookId}/characters/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  canon: (bookId: string, kind?: string) =>
    http<CanonEntityOut[]>(`/books/${bookId}/canon${qs({ kind })}`),
  createCanon: (bookId: string, body: CanonEntityIn) =>
    http<CanonEntityOut>(`/books/${bookId}/canon`, { method: "POST", body: JSON.stringify(body) }),
  updateCanon: (id: string, body: CanonEntityUpdateIn) =>
    http<CanonEntityOut>(`/canon/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteCanon: (id: string) => http<{ deleted: string }>(`/canon/${id}`, { method: "DELETE" }),
  ingestCanon: (bookId: string) =>
    http<CanonIngestOut>(`/books/${bookId}/canon/ingest`, { method: "POST" }),

  // --- world threads (curated) --------------------------------------------------------------------
  threads: (bookId: string) => http<ThreadOut[]>(`/books/${bookId}/threads`),
  createThread: (bookId: string, body: ThreadIn) =>
    http<ThreadOut>(`/books/${bookId}/threads`, { method: "POST", body: JSON.stringify(body) }),
  addThreadBeat: (threadId: string, body: ThreadBeatIn) =>
    http<ThreadOut>(`/threads/${threadId}/beats`, { method: "POST", body: JSON.stringify(body) }),
  deleteThread: (threadId: string) =>
    http<{ deleted: string }>(`/threads/${threadId}`, { method: "DELETE" }),

  // --- learning: distilled voice/dialogue rules (Tier 3) ------------------------------------------
  // distill runs a review-model pass over recent edits (synchronous; can take a few seconds, may 504).
  ruleProposals: (bookId: string, status?: string) =>
    http<RuleProposalOut[]>(`/books/${bookId}/rule-proposals${qs({ status })}`),
  distill: (bookId: string, pov?: string) =>
    http<RuleProposalOut[]>(`/books/${bookId}/distill${qs({ pov })}`, { method: "POST" }),
  decideRuleProposal: (id: string, body: RuleProposalDecisionIn) =>
    http<RuleProposalOut>(`/rule-proposals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // --- scene markup: annotations + suggestions ----------------------------------------------------
  annotations: (sceneId: string) => http<AnnotationOut[]>(`/scenes/${sceneId}/annotations`),
  createAnnotation: (sceneId: string, body: AnnotationIn) =>
    http<AnnotationOut>(`/scenes/${sceneId}/annotations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteAnnotation: (id: string) =>
    http<{ deleted: string }>(`/annotations/${id}`, { method: "DELETE" }),
  suggestions: (sceneId: string) => http<SuggestionOut[]>(`/scenes/${sceneId}/suggestions`),
  createSuggestion: (sceneId: string, body: SuggestionIn) =>
    http<SuggestionOut>(`/scenes/${sceneId}/suggestions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  decideSuggestion: (id: string, status: SuggestionStatus) =>
    http<SuggestionOut>(`/suggestions/${id}/decision`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  deleteSuggestion: (id: string) =>
    http<{ deleted: string }>(`/suggestions/${id}`, { method: "DELETE" }),
};
