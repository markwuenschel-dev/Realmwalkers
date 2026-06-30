import type { LlmCallOut } from "../../api/types";

export type CacheStageRow = {
  stage: string;
  calls: number;
  hitPct: number;
  cacheRead: number;
  cacheCreate: number;
  input: number;
};

/** Per-stage cache hit rate for a run's calls. */
export function cacheHealthByStage(calls: LlmCallOut[]): CacheStageRow[] {
  const byStage = new Map<string, LlmCallOut[]>();
  for (const c of calls) {
    const list = byStage.get(c.stage) ?? [];
    list.push(c);
    byStage.set(c.stage, list);
  }
  const rows: CacheStageRow[] = [];
  for (const [stage, group] of byStage) {
    const cacheRead = group.reduce((s, c) => s + c.cache_read_tokens, 0);
    const cacheCreate = group.reduce((s, c) => s + c.cache_creation_tokens, 0);
    const input = group.reduce((s, c) => s + c.input_tokens, 0);
    const prompt = input + cacheCreate + cacheRead;
    rows.push({
      stage,
      calls: group.length,
      hitPct: prompt ? Math.round((cacheRead / prompt) * 100) : 0,
      cacheRead,
      cacheCreate,
      input,
    });
  }
  rows.sort((a, b) => a.stage.localeCompare(b.stage));
  return rows;
}

export function formatSettingsKey(key: string): string {
  return key.replace(/_/g, " ");
}

export function truncationAdvice(call: LlmCallOut): string {
  const meta = (call.metadata ?? {}) as Record<string, unknown>;
  const stop = meta.stop_reason;
  if (call.stage.startsWith("scene_packet")) {
    return "Increase scene_packet max_tokens or shorten the chapter prefix / scene context.";
  }
  if (call.stage === "drafter") {
    return "Increase draft max_tokens or reduce beats/scene packet size in context.";
  }
  if (stop === "max_tokens") {
    return "Output hit max_tokens — raise the stage limit or shorten the prompt.";
  }
  return "Inspect context sections; shorten prompt or raise max_tokens for this stage.";
}
