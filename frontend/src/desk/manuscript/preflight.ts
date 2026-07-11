// Preflight — a first-class export GATE, not decorative validation. It walks the ManuscriptSpine under a
// resolved ExportPolicy and returns structured issues (severity + stable code + message + location). For
// submission-safe presets an error-severity issue BLOCKS the export unless the human explicitly overrides.
//
// Every check the export foundation promises lives here (see the section headers). Checks read the spine
// (including the parse-time issues buildSpine attached) plus the policy — never the raw wire or emitter
// internals — so the same report is valid across all three emitters.

import type { ProseBlock } from "../prose";
import type { ExportPolicy, ExportPreset } from "./presets";
import {
  spineChapters,
  spineParts,
  spineVolumes,
  type ManuscriptSpine,
  type SpineChapterNode,
  type SpinePartNode,
  type SpineSceneNode,
} from "./spine";

export type ExportIssueSeverity = "error" | "warn" | "info";

export interface ExportIssueLocation {
  partNo?: number;
  chapterNo?: number;
  sceneNo?: number;
  /** Human-readable anchor, e.g. "Chapter 3 · Scene 2" or "Part II". */
  label?: string;
}

export interface ExportIssue {
  severity: ExportIssueSeverity;
  /** Stable machine code (snake_case) for tests + UI grouping. */
  code: string;
  message: string;
  location?: ExportIssueLocation;
}

export interface PreflightReport {
  preset: ExportPreset;
  issues: ExportIssue[];
  errorCount: number;
  warnCount: number;
  infoCount: number;
  /** True when the export must not proceed: a submission-safe preset saw an error and no override was
   *  given. Non-submission presets never block (issues are advisory). */
  blocked: boolean;
}

export interface PreflightOptions {
  /** Human override for a submission-safe block ("export anyway"). */
  override?: boolean;
}

// Kinds that carry a narrating POV (front/back matter do not require one).
const POV_REQUIRED_KINDS = new Set(["chapter", "prologue", "interlude", "epilogue"]);

// Blocks that lose fidelity when flattened for a plain submission format (Shunn). Not "broken" — they
// are safely degraded — but the human should know rich content was flattened.
const RICH_BLOCK_KINDS = new Set<ProseBlock["kind"]>([
  "interface",
  "table",
  "stat",
  "callout",
  "code",
]);

const KNOWN_BLOCK_KINDS = new Set<ProseBlock["kind"]>([
  "p",
  "heading",
  "time",
  "ul",
  "ol",
  "callout",
  "hr",
  "stat",
  "code",
  "interface",
  "table",
]);

const TODO_MARKER = /\b(TODO|FIXME|XXX|TKTK)\b|\[\s*TK\s*\]/;
const PLACEHOLDER = /\b(lorem ipsum|placeholder|insert\s+\w+\s+here)\b|<[A-Z_]{3,}>|\?\?\?\?+/i;
const TRIPLE_BLANK = /\n[ \t]*\n[ \t]*\n[ \t]*\n/; // 3+ blank lines (beyond one blank between paragraphs)
const ZERO_WIDTH = /[\u200B\u200C\u200D\uFEFF]/; // zero-width space/non-joiner/joiner + BOM

function chapterAnchor(ch: SpineChapterNode): ExportIssueLocation {
  return { chapterNo: ch.chapterNo ?? undefined, label: ch.label };
}

function sceneAnchor(ch: SpineChapterNode, sc: SpineSceneNode): ExportIssueLocation {
  return {
    chapterNo: ch.chapterNo ?? undefined,
    sceneNo: sc.sceneNo,
    label: `${ch.label} · Scene ${sc.sceneNo}`,
  };
}

// --- structural integrity ---------------------------------------------------------------------------

function checkDuplicateChapterNumbers(chapters: SpineChapterNode[], out: ExportIssue[]): void {
  const seen = new Map<number, number>();
  // Only numbered chapters can clash on a number; a numberless kind (prologue/…) has none to duplicate.
  for (const ch of chapters) {
    if (ch.chapterNo == null) continue;
    seen.set(ch.chapterNo, (seen.get(ch.chapterNo) ?? 0) + 1);
  }
  for (const [no, count] of seen) {
    if (count > 1) {
      out.push({
        severity: "error",
        code: "duplicate_chapter_number",
        message: `Chapter number ${no} is used by ${count} chapters — reading order is ambiguous.`,
        location: { chapterNo: no },
      });
    }
  }
}

function checkDuplicateSceneNumbers(ch: SpineChapterNode, out: ExportIssue[]): void {
  const seen = new Map<number, number>();
  for (const sc of ch.scenes) seen.set(sc.sceneNo, (seen.get(sc.sceneNo) ?? 0) + 1);
  for (const [no, count] of seen) {
    if (count > 1) {
      out.push({
        severity: "error",
        code: "duplicate_scene_number",
        message: `Scene number ${no} appears ${count} times in ${ch.label}.`,
        location: { chapterNo: ch.chapterNo ?? undefined, sceneNo: no, label: ch.label },
      });
    }
  }
}

function checkMissingPov(ch: SpineChapterNode, out: ExportIssue[]): void {
  if (POV_REQUIRED_KINDS.has(ch.kind) && ch.pov.trim() === "") {
    out.push({
      severity: "error",
      code: "missing_pov",
      message: `${ch.label} has no POV character set.`,
      location: chapterAnchor(ch),
    });
  }
}

function checkEmptyChapter(ch: SpineChapterNode, out: ExportIssue[]): void {
  const rendered = ch.scenes.filter((s) => s.hasProse);
  if (rendered.length === 0) {
    out.push({
      severity: "warn",
      code: "empty_chapter",
      message: `${ch.label} has no scene with prose — it will not render.`,
      location: chapterAnchor(ch),
    });
  }
}

// --- prose fidelity (per scene) ---------------------------------------------------------------------

function checkScene(
  ch: SpineChapterNode,
  sc: SpineSceneNode,
  policy: ExportPolicy,
  out: ExportIssue[],
): void {
  const loc = sceneAnchor(ch, sc);

  // Parse-time anomalies bubbled up from buildSpine.
  for (const issue of sc.issues) {
    if (issue.code === "empty_scene") {
      out.push({ severity: "warn", code: "empty_scene", message: issue.message, location: loc });
    } else if (issue.code === "no_blocks_parsed") {
      out.push({
        severity: "warn",
        code: "parse_no_blocks",
        message: issue.message,
        location: loc,
      });
    } else {
      out.push({ severity: "info", code: issue.code, message: issue.message, location: loc });
    }
  }

  if (!sc.hasProse) return; // nothing further to inspect

  // Unsupported / unknown block kinds (defensive — the parser shouldn't emit these).
  for (const b of sc.blocks) {
    if (!KNOWN_BLOCK_KINDS.has(b.kind)) {
      out.push({
        severity: "error",
        code: "unsupported_block_kind",
        message: `${loc.label} contains an unsupported block kind "${b.kind}".`,
        location: loc,
      });
    }
  }

  // Editorial markers left in the prose.
  if (TODO_MARKER.test(sc.proseRaw)) {
    out.push({
      severity: "warn",
      code: "editorial_marker",
      message: `${loc.label} contains an editorial marker (TODO/FIXME/XXX/TK).`,
      location: loc,
    });
  }
  // Placeholder / filler text.
  if (PLACEHOLDER.test(sc.proseRaw)) {
    out.push({
      severity: "warn",
      code: "placeholder_text",
      message: `${loc.label} contains suspicious placeholder text.`,
      location: loc,
    });
  }
  // Hard tabs.
  if (sc.proseRaw.includes("\t")) {
    out.push({
      severity: "info",
      code: "hard_tab",
      message: `${loc.label} contains a hard tab character.`,
      location: loc,
    });
  }
  // Zero-width / BOM characters.
  if (ZERO_WIDTH.test(sc.proseRaw)) {
    out.push({
      severity: "info",
      code: "zero_width_char",
      message: `${loc.label} contains a zero-width or BOM character.`,
      location: loc,
    });
  }
  // Excessive blank runs.
  if (TRIPLE_BLANK.test(sc.proseRaw)) {
    out.push({
      severity: "info",
      code: "repeated_blank_lines",
      message: `${loc.label} has 3+ consecutive blank lines.`,
      location: loc,
    });
  }

  // Submission safety: rich LitRPG blocks are flattened by a "flatten" policy (Shunn). Inform the human
  // which content was degraded so they can verify submission acceptability.
  if (policy.richBlocks === "flatten") {
    const rich = new Set(sc.blocks.filter((b) => RICH_BLOCK_KINDS.has(b.kind)).map((b) => b.kind));
    if (rich.size > 0) {
      out.push({
        severity: "warn",
        code: "shunn_rich_content_flattened",
        message: `${loc.label} contains rich blocks (${[...rich].join(", ")}) that will be flattened to plain text for submission.`,
        location: loc,
      });
    }
  }
}

// --- label contract regression guard ----------------------------------------------------------------

function checkLabelContract(ch: SpineChapterNode, out: ExportIssue[]): void {
  // A chapter whose source kind wasn't a known ChapterKind was coerced to "Chapter N".
  if (!ch.kindRecognized) {
    out.push({
      severity: "warn",
      code: "unrecognized_chapter_kind",
      message: `${ch.label} had an unrecognized kind and was labeled as a numbered chapter.`,
      location: chapterAnchor(ch),
    });
  }
  // Hard invariant (acceptance bar): a non-'chapter' kind must NEVER render as "Chapter N". If the label
  // contract regressed, this fires as an error — the guarantee prologue/interlude/epilogue never get
  // silently numbered, enforced at runtime rather than only in a test.
  if (ch.kind !== "chapter" && /^Chapter\s+\d/.test(ch.label)) {
    out.push({
      severity: "error",
      code: "kind_label_mismatch",
      message: `${ch.label} has kind "${ch.kind}" but is labeled as a numbered chapter — label contract regression.`,
      location: chapterAnchor(ch),
    });
  }
}

// --- parts integrity --------------------------------------------------------------------------------

function checkParts(spine: ManuscriptSpine, out: ExportIssue[]): void {
  const parts = spineParts(spine);
  if (parts.length === 0) return;

  // Empty part / part with no rendered chapter.
  for (const p of parts) {
    const renderable = p.chapters.some((c) => c.scenes.some((s) => s.hasProse));
    if (p.chapters.length === 0) {
      out.push({
        severity: "warn",
        code: "empty_part",
        message: `${p.label} has no chapters.`,
        location: { partNo: p.partNo, label: p.label },
      });
    } else if (!renderable) {
      out.push({
        severity: "warn",
        code: "part_no_rendered_chapter",
        message: `${p.label} has chapters but none with prose — it will render an empty divider.`,
        location: { partNo: p.partNo, label: p.label },
      });
    }
  }

  // Ungrouped narrative chapters while Parts are in use. Front/back matter and prologue/epilogue may sit
  // outside parts by design; a plain "chapter" left ungrouped is usually an oversight.
  const topLevelChapters = spine.nodes.filter((n): n is SpineChapterNode => n.type === "chapter");
  for (const ch of topLevelChapters) {
    if (ch.kind !== "chapter") continue;
    // A dangling part_id (set but pointing at no known part) is a distinct, stronger anomaly.
    if (ch.partId) {
      out.push({
        severity: "warn",
        code: "dangling_part_reference",
        message: `${ch.label} references a Part that is not in the manuscript; it renders ungrouped.`,
        location: chapterAnchor(ch),
      });
    } else {
      out.push({
        severity: "warn",
        code: "ungrouped_chapter_with_parts",
        message: `${ch.label} is not assigned to a Part, but this book uses Parts.`,
        location: chapterAnchor(ch),
      });
    }
  }

  // Non-contiguous membership: a part's chapters should form a consecutive run among all chapters in
  // reading order. (The spine's reading order already groups a part's chapters together, so it can't
  // reveal interleaving — compare against the global `position` ordering instead.)
  const orderKey = (c: SpineChapterNode) => c.position ?? c.chapterNo ?? 0;
  const byOrder = [...spineChapters(spine)].sort((a, b) => orderKey(a) - orderKey(b));
  const indexByChapter = new Map(byOrder.map((c, i) => [c, i]));
  for (const p of parts) {
    const positions = p.chapters
      .map((c) => indexByChapter.get(c))
      .filter((i): i is number => i != null)
      .sort((a, b) => a - b);
    const contiguous = positions.every((pos, i) => i === 0 || pos === positions[i - 1] + 1);
    if (!contiguous) {
      out.push({
        severity: "warn",
        code: "non_contiguous_part",
        message: `${p.label}'s chapters are interleaved with other chapters in reading order.`,
        location: { partNo: p.partNo, label: p.label },
      });
    }
  }
}

// --- volumes integrity (the top grouping tier) ------------------------------------------------------

function checkVolumes(spine: ManuscriptSpine, out: ExportIssue[]): void {
  const volumes = spineVolumes(spine);
  if (volumes.length === 0) return;

  // A Volume that renders no chapter (all its parts empty) would emit a bare divider.
  for (const v of volumes) {
    const renderable = v.parts.some((p) =>
      p.chapters.some((c) => c.scenes.some((s) => s.hasProse)),
    );
    if (!renderable) {
      out.push({
        severity: "warn",
        code: v.parts.length === 0 ? "empty_volume" : "volume_no_rendered_chapter",
        message:
          v.parts.length === 0
            ? `${v.label} has no parts.`
            : `${v.label}'s parts have no prose — it will render an empty divider.`,
        location: { label: v.label },
      });
    }
  }

  // A top-level Part while Volumes are in use: either a dangling volume_id (stronger anomaly) or an
  // unassigned part (usually an oversight — mirrors the ungrouped-chapter check one tier up).
  const topLevelParts = spine.nodes.filter((n): n is SpinePartNode => n.type === "part");
  for (const p of topLevelParts) {
    if (p.volumeId) {
      out.push({
        severity: "warn",
        code: "dangling_volume_reference",
        message: `${p.label} references a Volume that is not in the manuscript; it renders ungrouped.`,
        location: { partNo: p.partNo, label: p.label },
      });
    } else {
      out.push({
        severity: "warn",
        code: "ungrouped_part_with_volumes",
        message: `${p.label} is not assigned to a Volume, but this book uses Volumes.`,
        location: { partNo: p.partNo, label: p.label },
      });
    }
  }
}

/**
 * Run the full preflight over a spine under a policy. Pure (no I/O); deterministic given the spine.
 * `blocked` is true only for a submission-safe preset with at least one error and no override.
 */
export function preflight(
  spine: ManuscriptSpine,
  policy: ExportPolicy,
  opts: PreflightOptions = {},
): PreflightReport {
  const issues: ExportIssue[] = [];
  const chapters = spineChapters(spine);

  checkDuplicateChapterNumbers(chapters, issues);
  for (const ch of chapters) {
    checkDuplicateSceneNumbers(ch, issues);
    checkMissingPov(ch, issues);
    checkEmptyChapter(ch, issues);
    checkLabelContract(ch, issues);
    for (const sc of ch.scenes) checkScene(ch, sc, policy, issues);
  }
  checkParts(spine, issues);
  checkVolumes(spine, issues);

  const errorCount = issues.filter((i) => i.severity === "error").length;
  const warnCount = issues.filter((i) => i.severity === "warn").length;
  const infoCount = issues.filter((i) => i.severity === "info").length;
  const blocked =
    policy.submissionSafe &&
    policy.preflight === "block_on_error" &&
    errorCount > 0 &&
    !opts.override;

  return { preset: policy.preset, issues, errorCount, warnCount, infoCount, blocked };
}
