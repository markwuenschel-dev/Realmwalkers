// ExportManifest — provenance that ships with every export, not optional polish. It records WHAT was
// exported, under WHICH preset, from which schema versions, with what counts, and every preflight
// warning/error. Produced by the pipeline for all three emitters so an exported artifact is always
// traceable back to its inputs.

import type { ExportMetadata } from "./metadata";
import type { ExportPreset } from "./presets";
import type { ExportIssue, PreflightReport } from "./preflight";
import { spineCounts, SPINE_SCHEMA_VERSION, type ManuscriptSpine } from "./spine";

/** Renderer version — bump when an emitter's OUTPUT changes in a way a consumer might need to detect.
 *  Distinct from the spine schema version (the IR shape) and the source schema version (the wire DTO). */
export const RENDERER_VERSION = "writers-desk-export/1" as const;

/** The source (wire) schema version the manuscript payload conforms to. Mirrors the backend
 *  `dominion-manuscript/v1` Markdown front-matter schema id so the two never silently diverge. */
export const SOURCE_SCHEMA_VERSION = "dominion-manuscript/v1" as const;

export interface ExportManifest {
  source: "writers-desk";
  title: string;
  author?: string;
  preset: ExportPreset;
  /** ISO-8601 timestamp; supplied by the caller (the pipeline stamps it) so this module stays pure. */
  exportedAt: string;
  sourceSchemaVersion: typeof SOURCE_SCHEMA_VERSION;
  spineSchemaVersion: typeof SPINE_SCHEMA_VERSION;
  rendererVersion: typeof RENDERER_VERSION;
  counts: {
    volumes: number;
    parts: number;
    chapters: number;
    scenes: number;
    words: number;
  };
  errorCount: number;
  warnCount: number;
  infoCount: number;
  issues: ExportIssue[];
  /** True when preflight blocked the export (submission-safe preset, unresolved errors). */
  blocked: boolean;
  /** Whether this manifest describes a draft compile (all scenes) vs the approved manuscript. */
  draft: boolean;
}

export interface BuildManifestInput {
  spine: ManuscriptSpine;
  metadata: ExportMetadata;
  preset: ExportPreset;
  preflight: PreflightReport;
  exportedAt: string;
  draft: boolean;
}

/**
 * Assemble the manifest from the spine, metadata, and preflight report. Counts are read from the spine
 * (single source of truth) so the manifest word count can never disagree with what was rendered —
 * `assertCountsConsistent` is the guard that proves it if an emitter ever re-derives counts.
 */
export function buildManifest(input: BuildManifestInput): ExportManifest {
  const counts = spineCounts(input.spine);
  return {
    source: "writers-desk",
    title: input.metadata.title,
    author: input.metadata.author,
    preset: input.preset,
    exportedAt: input.exportedAt,
    sourceSchemaVersion: SOURCE_SCHEMA_VERSION,
    spineSchemaVersion: SPINE_SCHEMA_VERSION,
    rendererVersion: RENDERER_VERSION,
    counts,
    errorCount: input.preflight.errorCount,
    warnCount: input.preflight.warnCount,
    infoCount: input.preflight.infoCount,
    issues: input.preflight.issues,
    blocked: input.preflight.blocked,
    draft: input.draft,
  };
}

/**
 * Cross-check that a manifest's word count matches an independently computed export word count (e.g. the
 * total an emitter summed while rendering). Returns an ExportIssue when they diverge (the "word count
 * mismatch between manifest and export input" check), or null when consistent. Kept separate so the
 * pipeline can run it after rendering without the manifest builder depending on emitter internals.
 */
export function assertCountsConsistent(
  manifest: ExportManifest,
  renderedWordCount: number,
): ExportIssue | null {
  if (manifest.counts.words !== renderedWordCount) {
    return {
      severity: "warn",
      code: "word_count_mismatch",
      message: `Manifest word count (${manifest.counts.words}) differs from the rendered word count (${renderedWordCount}).`,
    };
  }
  return null;
}
