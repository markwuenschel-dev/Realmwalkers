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
