/** Stage groupings aligned with backend PIPELINE_STAGE_ORDER / PRIME_STAGES. */
export const PRIME_STAGES = new Set([
  "scene_packet_author_prefix_prime",
  "scene_packet_qa_prefix_prime",
]);

export const FANOUT_STAGES = new Set(["scene_packet_author", "scene_packet_qa"]);

export const PIPELINE_ORDER = [
  "scene_packet_author_prefix_prime",
  "scene_packet_qa_prefix_prime",
  "scene_packet_author",
  "scene_packet_qa",
  "drafter",
  "reviewers",
  "enrichment",
  "length",
] as const;

export function stageSortKey(stage: string): number {
  const i = PIPELINE_ORDER.indexOf(stage as (typeof PIPELINE_ORDER)[number]);
  return i >= 0 ? i : 100 + stage.charCodeAt(0);
}
