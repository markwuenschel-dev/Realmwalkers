/** Filter state for GET /llm-calls — shared by the filter bar, URL sync, and drawer. */

export type LlmCallFilters = {
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
};

export const EXPENSIVE_INPUT_TOKENS = 10_000;
export const SLOW_LATENCY_MS = 30_000;

export const DRAFT_STAGES = "drafter,enrichment,length,reviewers";
export const CACHE_PRIME_STAGES = "scene_packet_author_prefix_prime,scene_packet_qa_prefix_prime";

export type FilterPresetId =
  | "problems"
  | "scene_packet"
  | "draft"
  | "cache_primes"
  | "latest_run"
  | "this_chapter";

export function presetFilters(
  id: FilterPresetId,
  ctx: { latestRunId?: string | null; chapterId?: string | null },
): LlmCallFilters {
  switch (id) {
    case "problems":
      return { problems_only: true };
    case "scene_packet":
      return { stage_prefix: "scene_packet" };
    case "draft":
      return { stages: DRAFT_STAGES };
    case "cache_primes":
      return { stages: CACHE_PRIME_STAGES };
    case "latest_run":
      return ctx.latestRunId ? { run_id: ctx.latestRunId } : {};
    case "this_chapter":
      return ctx.chapterId ? { chapter_id: ctx.chapterId } : {};
    default:
      return {};
  }
}

export function filtersLabel(f: LlmCallFilters): string {
  const parts: string[] = [];
  if (f.problems_only) parts.push("problems");
  if (f.truncated) parts.push("truncated");
  if (f.errors_only) parts.push("errors");
  if (f.fallbacks_only) parts.push("fallbacks");
  if (f.cache_miss_only) parts.push("cache miss");
  if (f.min_input_tokens) parts.push("expensive");
  if (f.stage_prefix) parts.push(f.stage_prefix);
  if (f.stages) parts.push(f.stages.replace(/,/g, " · "));
  if (f.stage) parts.push(f.stage);
  if (f.model) parts.push(f.model);
  if (f.run_id) parts.push("run");
  if (f.chapter_id) parts.push("chapter");
  if (f.scene_no != null) parts.push(`Sc${f.scene_no}`);
  return parts.length ? parts.join(" · ") : "All calls";
}

export function filtersToSearchParams(bookId: string, f: LlmCallFilters): URLSearchParams {
  const p = new URLSearchParams();
  p.set("book", bookId);
  if (f.chapter_id) p.set("chapter_id", f.chapter_id);
  if (f.run_id) p.set("run_id", f.run_id);
  if (f.scene_no != null) p.set("scene_no", String(f.scene_no));
  if (f.stage) p.set("stage", f.stage);
  if (f.stage_prefix) p.set("stage_prefix", f.stage_prefix);
  if (f.stages) p.set("stages", f.stages);
  if (f.model) p.set("model", f.model);
  if (f.truncated) p.set("truncated", "1");
  if (f.errors_only) p.set("errors_only", "1");
  if (f.problems_only) p.set("problems_only", "1");
  if (f.fallbacks_only) p.set("fallbacks_only", "1");
  if (f.cache_miss_only) p.set("cache_miss_only", "1");
  if (f.min_latency_ms != null) p.set("min_latency_ms", String(f.min_latency_ms));
  if (f.min_input_tokens != null) p.set("min_input_tokens", String(f.min_input_tokens));
  return p;
}

export function filtersFromSearchParams(sp: URLSearchParams): LlmCallFilters {
  const f: LlmCallFilters = {};
  const chapter = sp.get("chapter_id");
  if (chapter) f.chapter_id = chapter;
  const run = sp.get("run_id");
  if (run) f.run_id = run;
  const scene = sp.get("scene_no");
  if (scene) f.scene_no = Number(scene);
  const stage = sp.get("stage");
  if (stage) f.stage = stage;
  const prefix = sp.get("stage_prefix");
  if (prefix) f.stage_prefix = prefix;
  const stages = sp.get("stages");
  if (stages) f.stages = stages;
  const model = sp.get("model");
  if (model) f.model = model;
  if (sp.get("truncated") === "1") f.truncated = true;
  if (sp.get("errors_only") === "1") f.errors_only = true;
  if (sp.get("problems_only") === "1") f.problems_only = true;
  if (sp.get("fallbacks_only") === "1") f.fallbacks_only = true;
  if (sp.get("cache_miss_only") === "1") f.cache_miss_only = true;
  const lat = sp.get("min_latency_ms");
  if (lat) f.min_latency_ms = Number(lat);
  const inp = sp.get("min_input_tokens");
  if (inp) f.min_input_tokens = Number(inp);
  return f;
}

export function filtersToApiOpts(
  bookId: string,
  f: LlmCallFilters,
  paging?: { limit?: number; offset?: number },
) {
  return {
    book_id: bookId,
    chapter_id: f.chapter_id,
    run_id: f.run_id,
    scene_no: f.scene_no,
    stage: f.stage,
    stage_prefix: f.stage_prefix,
    stages: f.stages,
    model: f.model,
    truncated: f.truncated,
    errors_only: f.errors_only,
    problems_only: f.problems_only,
    fallbacks_only: f.fallbacks_only,
    min_latency_ms: f.min_latency_ms,
    min_input_tokens: f.min_input_tokens,
    cache_miss_only: f.cache_miss_only,
    limit: paging?.limit ?? 50,
    offset: paging?.offset ?? 0,
  };
}

/** Merge UI toggles into a filter object (clears mutually exclusive stage keys when picking exact stage). */
export function buildFiltersFromForm(input: {
  chapter_id: string;
  run_id: string;
  scene_no: string;
  stage: string;
  model: string;
  truncated: boolean;
  errors_only: boolean;
  fallbacks_only: boolean;
  expensive: boolean;
  cache_miss_only: boolean;
}): LlmCallFilters {
  const f: LlmCallFilters = {};
  if (input.chapter_id) f.chapter_id = input.chapter_id;
  if (input.run_id) f.run_id = input.run_id;
  if (input.scene_no.trim()) {
    const n = Number(input.scene_no);
    if (!Number.isNaN(n)) f.scene_no = n;
  }
  if (input.stage) f.stage = input.stage;
  if (input.model) f.model = input.model;
  if (input.truncated) f.truncated = true;
  if (input.errors_only) f.errors_only = true;
  if (input.fallbacks_only) f.fallbacks_only = true;
  if (input.expensive) f.min_input_tokens = EXPENSIVE_INPUT_TOKENS;
  if (input.cache_miss_only) f.cache_miss_only = true;
  return f;
}

export function formFromFilters(f: LlmCallFilters): {
  chapter_id: string;
  run_id: string;
  scene_no: string;
  stage: string;
  model: string;
  truncated: boolean;
  errors_only: boolean;
  fallbacks_only: boolean;
  expensive: boolean;
  cache_miss_only: boolean;
} {
  return {
    chapter_id: f.chapter_id ?? "",
    run_id: f.run_id ?? "",
    scene_no: f.scene_no != null ? String(f.scene_no) : "",
    stage: f.stage ?? f.stage_prefix ?? "",
    model: f.model ?? "",
    truncated: f.truncated ?? f.problems_only ?? false,
    errors_only: f.errors_only ?? false,
    fallbacks_only: f.fallbacks_only ?? false,
    expensive: (f.min_input_tokens ?? 0) >= EXPENSIVE_INPUT_TOKENS,
    cache_miss_only: f.cache_miss_only ?? false,
  };
}

export function hasActiveFilters(f: LlmCallFilters): boolean {
  return Object.keys(f).length > 0;
}
