// Export presets as real policy objects — NOT a pile of `if (emitter === "shunn")` conditionals sprayed
// through the renderers. A preset resolves to one ExportPolicy that declares, in one place: which emitter
// runs, page setup, typography intent, chapter-opening + scene-break rules, running headers, which
// metadata is included, how LitRPG panels / tables / callouts are treated, submission safety, prose
// source (raw vs beautified — an EXPLICIT choice, never an invisible transform), and how preflight grades
// issues. Emitters read these fields; they do not re-decide policy.
//
// Only the three production presets below are real and UI-exposed. Future preset kinds are declared for
// type-completeness but are INTERNAL ONLY — `resolvePolicy` throws for them rather than pretending they
// work, so the UI can never offer a preset that silently no-ops.

export type EmitterKind = "reader_docx" | "shunn_docx" | "markdown";

/** Which prose form an emitter consumes. `raw` = the verbatim semantic prose (safe, lossless); used by
 *  Markdown so it never ships accidentally-beautified text. `beautified` = the typographically
 *  normalized form, parsed to blocks; used by the DOCX emitters. */
export type ProseSource = "raw" | "beautified";

/** How rich/LitRPG blocks (interface panels, tables, callouts, stat windows, code) are treated.
 *  `styled` = render with full styling (Reader DOCX). `flatten` = degrade to plain submission-safe text
 *  (Shunn). `passthrough` = leave the raw markup untouched (Markdown emits raw prose verbatim). */
export type RichBlockPolicy = "styled" | "flatten" | "passthrough";

export type RunningHeader = "none" | "reader_pageno" | "shunn_surname_title_pageno";

/** How preflight escalates. `block_on_error` = an error-severity issue blocks the export unless the human
 *  explicitly overrides (submission-safe presets). `warn_only` = issues are advisory. */
export type PreflightMode = "block_on_error" | "warn_only";

export interface ExportTypography {
  bodyFont: string;
  bodySizePt: number;
  /** Line spacing intent — the DOCX rewrite maps these to exact `line` values; today's emitters map
   *  `double` and `reader` to their existing spacing. */
  lineSpacing: "single" | "double" | "reader";
  /** Monospace body (Shunn/Courier submission format). */
  monospace: boolean;
}

export interface ExportPageSetup {
  marginInches: number;
  runningHeader: RunningHeader;
  /** Distinct title page (Shunn uses `titlePage: true` so the header is suppressed on page 1). */
  titlePage: boolean;
}

export interface ExportPolicy {
  preset: ExportPreset;
  label: string; // human label for the UI
  description: string; // tooltip / help text for the UI
  emitter: EmitterKind;
  proseSource: ProseSource;
  richBlocks: RichBlockPolicy;

  // --- structure rendering ---
  renderParts: boolean; // render Part dividers (true for all three surfaces — parity is the point)
  /** Scene-break separator glyph rendered between scenes within a chapter. */
  sceneBreakGlyph: string;

  // --- metadata inclusion (drives which ExportMetadata fields reach the page) ---
  includeSeriesLine: boolean; // "BOOK ONE" + series on the title page
  includeSubtitle: boolean;
  includeAuthorByline: boolean;

  // --- typography + page setup (policy owns styling decisions, not the emitter) ---
  typography: ExportTypography;
  pageSetup: ExportPageSetup;

  // --- gates ---
  submissionSafe: boolean; // preflight treats rich-content incompatibility as an export-blocking error
  preflight: PreflightMode;
}

/** Every preset kind the system models. Only the first three are supported today. */
export type ExportPreset =
  | "reader_proof"
  | "submission_shunn"
  | "editorial_review"
  // --- declared but NOT yet implemented (internal only; resolvePolicy throws) ---
  | "print_proof"
  | "ebook_source"
  | "canon_bible";

const READER_PROOF: ExportPolicy = {
  preset: "reader_proof",
  label: "Reader DOCX",
  description:
    "Styled book format with LitRPG interface panels, epigraphs, and part/chapter openings.",
  emitter: "reader_docx",
  proseSource: "beautified",
  richBlocks: "styled",
  renderParts: true,
  sceneBreakGlyph: "⁂",
  includeSeriesLine: true,
  includeSubtitle: true,
  includeAuthorByline: false,
  typography: { bodyFont: "Georgia", bodySizePt: 11, lineSpacing: "reader", monospace: false },
  pageSetup: { marginInches: 1, runningHeader: "reader_pageno", titlePage: false },
  submissionSafe: false,
  preflight: "warn_only",
};

const SUBMISSION_SHUNN: ExportPolicy = {
  preset: "submission_shunn",
  label: "Shunn DOCX",
  description:
    "Plain manuscript format for agents/editors — rich LitRPG blocks flattened to safe text.",
  emitter: "shunn_docx",
  proseSource: "beautified",
  richBlocks: "flatten",
  renderParts: true,
  sceneBreakGlyph: "#",
  includeSeriesLine: false,
  includeSubtitle: false,
  includeAuthorByline: true,
  typography: { bodyFont: "Courier New", bodySizePt: 12, lineSpacing: "double", monospace: true },
  pageSetup: { marginInches: 1, runningHeader: "shunn_surname_title_pageno", titlePage: true },
  submissionSafe: true,
  preflight: "block_on_error",
};

const EDITORIAL_REVIEW: ExportPolicy = {
  preset: "editorial_review",
  label: "Markdown",
  description:
    "Semantic Markdown with YAML front matter — raw prose preserved verbatim for agents.",
  emitter: "markdown",
  proseSource: "raw",
  richBlocks: "passthrough",
  renderParts: true,
  sceneBreakGlyph: "", // markdown uses structural comments/headings, not a glyph
  includeSeriesLine: true,
  includeSubtitle: true,
  includeAuthorByline: false,
  typography: { bodyFont: "", bodySizePt: 0, lineSpacing: "single", monospace: false },
  pageSetup: { marginInches: 0, runningHeader: "none", titlePage: false },
  submissionSafe: false,
  preflight: "warn_only",
};

const POLICIES: Record<"reader_proof" | "submission_shunn" | "editorial_review", ExportPolicy> = {
  reader_proof: READER_PROOF,
  submission_shunn: SUBMISSION_SHUNN,
  editorial_review: EDITORIAL_REVIEW,
};

/** The supported presets, in the order the export UI should offer them. Future presets are deliberately
 *  absent — the UI must not advertise a preset that isn't implemented. */
export const UI_EXPORT_PRESETS: readonly ExportPolicy[] = [
  READER_PROOF,
  SUBMISSION_SHUNN,
  EDITORIAL_REVIEW,
];

export function isSupportedPreset(
  preset: ExportPreset,
): preset is "reader_proof" | "submission_shunn" | "editorial_review" {
  return preset in POLICIES;
}

/** Resolve a preset to its policy. Throws a clear error for declared-but-unimplemented presets rather
 *  than returning a stub — an unsupported preset must fail loudly, never silently produce a wrong export. */
export function resolvePolicy(preset: ExportPreset): ExportPolicy {
  if (!isSupportedPreset(preset)) {
    throw new Error(
      `Export preset "${preset}" is declared but not yet implemented — it is internal-only and must ` +
        `not be offered as a working export.`,
    );
  }
  return POLICIES[preset];
}
