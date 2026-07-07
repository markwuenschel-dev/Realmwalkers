// The export pipeline — the ONE entry point every export surface calls. It runs the full architecture in
// order and hands back a rendered artifact plus its manifest:
//
//   ManuscriptOut ─▶ resolve metadata ─▶ buildSpine ─▶ resolve preset/policy ─▶ preflight ─▶ render ─▶
//                    { artifact, manifest, preflight }
//
// Emitters (Reader DOCX / Shunn DOCX / Markdown) are invoked here from the spine — no screen wires an
// emitter directly, so metadata resolution, label resolution, prose parsing, preflight, and manifest
// generation happen exactly once, the same way, for all three surfaces. A submission-safe preset whose
// preflight blocked returns `artifact: null` (nothing is rendered) unless the caller passes `override`.

import type { Document } from "docx";
import type { ManuscriptOut } from "../api/types";
import { manuscriptWordCount, renderMarkdown, renderReaderDoc, renderShunnDoc } from "../lib/docx";
import { assertCountsConsistent, buildManifest, type ExportManifest } from "./manifest";
import { resolveExportMetadata } from "./metadata";
import { preflight, type PreflightReport } from "./preflight";
import { resolvePolicy, type ExportPreset } from "./presets";
import { buildSpine } from "./spine";

export type ExportArtifact =
  | { kind: "docx"; doc: Document }
  | { kind: "markdown"; content: string };

export interface ExportResult {
  manifest: ExportManifest;
  preflight: PreflightReport;
  /** The rendered artifact, or null when a submission-safe preflight blocked the export. */
  artifact: ExportArtifact | null;
}

export interface ExportOptions {
  /** Author name (Shunn byline / manifest attribution). */
  author?: string | null;
  /** Whether this is a working-draft compile (all scenes) vs the approved manuscript. */
  draft?: boolean;
  /** ISO-8601 timestamp stamped into the manifest + Markdown front matter. Injected by the caller so
   *  this module stays pure/deterministic for tests. */
  exportedAt: string;
  /** A per-export descriptor line for the Reader DOCX title page (e.g. "selected scenes",
   *  "Chapter 3 · Scene 2", or the approved/draft mode line). NOT book metadata. */
  renderSubtitle?: string;
  /** Human override for a submission-safe preflight block ("export anyway"). */
  override?: boolean;
}

/**
 * Run the export pipeline for one manuscript payload under one preset. Pure aside from the caller-
 * supplied timestamp; safe to call for a full book or a one-off fragment (a single scene/packet).
 */
export function runExport(
  ms: ManuscriptOut,
  preset: ExportPreset,
  opts: ExportOptions,
): ExportResult {
  const policy = resolvePolicy(preset);
  const metadata = resolveExportMetadata(ms, opts.author);
  const spine = buildSpine(ms, metadata);
  const report = preflight(spine, policy, { override: opts.override });
  const manifest = buildManifest({
    spine,
    metadata,
    preset,
    preflight: report,
    exportedAt: opts.exportedAt,
    draft: opts.draft ?? false,
  });

  // Independent count guard (runs BEFORE artifact creation): recompute the word total straight from the
  // wire — a different path than the spine the manifest counts came through — and record a warning if
  // they diverge. Catches a spine-build bug (a dropped/duplicated scene) before anything is rendered.
  // `manifest.issues` is the same array reference as `report.issues`, so one push updates both.
  const mismatch = assertCountsConsistent(manifest, manuscriptWordCount(ms));
  if (mismatch) {
    report.issues.push(mismatch);
    report.warnCount += 1;
    manifest.warnCount += 1;
  }

  if (report.blocked) {
    return { manifest, preflight: report, artifact: null };
  }

  let artifact: ExportArtifact;
  switch (policy.emitter) {
    case "reader_docx":
      artifact = {
        kind: "docx",
        doc: renderReaderDoc(spine, policy, { renderSubtitle: opts.renderSubtitle }),
      };
      break;
    case "shunn_docx":
      artifact = { kind: "docx", doc: renderShunnDoc(spine, policy) };
      break;
    case "markdown":
      artifact = {
        kind: "markdown",
        content: renderMarkdown(spine, policy, {
          draft: opts.draft ?? false,
          exportedAt: opts.exportedAt,
        }),
      };
      break;
  }

  return { manifest, preflight: report, artifact };
}
