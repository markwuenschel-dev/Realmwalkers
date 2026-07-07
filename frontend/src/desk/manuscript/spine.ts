// ManuscriptSpine — the versioned, renderer-neutral intermediate representation every export emitter
// consumes. The whole export architecture funnels through here:
//
//   ManuscriptOut (flat wire) ──buildSpine──▶ ManuscriptSpine ──▶ preset+policy ──▶ preflight ──▶ emitter
//
// Reader DOCX, Shunn DOCX, and Markdown NO LONGER independently loop chapters, resolve labels, parse
// prose, or invent metadata — they read this tree. Prose fidelity is a hard rule: every scene carries
// `proseRaw` (verbatim, the safe source), `proseRender` (beautified) and the parsed `blocks` SEPARATELY,
// so an emitter's choice of source is explicit policy, never an accidental transform.

import { beautify } from "../lib/beautify";
import { wordCount } from "../lib/format";
import { parseBlocks, type ProseBlock } from "../prose";
import type { ManuscriptOut } from "../api/types";
import { isKnownChapterKind, resolveChapterLabel, partLabel, type ChapterKind } from "./labels";
import type { ExportMetadata } from "./metadata";

/** Bump when the spine's SHAPE changes in a way emitters/manifests must notice. Stamped into every
 *  ExportManifest as provenance. */
export const SPINE_SCHEMA_VERSION = "manuscript-spine/1" as const;

/** A low-level, severity-free anomaly found while building the spine (parse time). Preflight reads
 *  these and grades them into policy-aware ExportIssues; keeping them on the node is the provenance the
 *  manifest promises. */
export interface SpineParseIssue {
  code: string;
  message: string;
}

export interface SpineSceneNode {
  sceneNo: number;
  /** Verbatim semantic prose — the safe source. Markdown exports THIS (never the beautified form). */
  proseRaw: string;
  /** `beautify(proseRaw)` — the typographically-normalized form. Explicit, not applied invisibly. */
  proseRender: string;
  /** Parsed AST of `proseRender`. Reader/Shunn DOCX consume THIS (they never re-parse prose). */
  blocks: ProseBlock[];
  /** Counted from `proseRaw` (the source of truth), so the manifest word count can't diverge by
   *  whatever beautify did. */
  wordCount: number;
  /** Whether the scene has any renderable prose (blank scenes are kept in the spine so preflight can
   *  flag them; emitters skip them). */
  hasProse: boolean;
  issues: SpineParseIssue[];
}

export interface SpineChapterNode {
  type: "chapter";
  chapterNo: number;
  /** Normalized kind. An unrecognized source kind is coerced to "chapter" and `kindRecognized` is set
   *  false so preflight can flag the mismatch (and a prologue never silently becomes "Chapter N"). */
  kind: ChapterKind;
  kindRecognized: boolean;
  title: string | null;
  pov: string;
  epigraph: string | null;
  /** Resolved ONCE via the shared label contract (`resolveChapterLabel`). Emitters render this verbatim. */
  label: string;
  partId: string | null;
  scenes: SpineSceneNode[];
}

export interface SpinePartNode {
  type: "part";
  id: string;
  partNo: number;
  title: string;
  subtitle: string | null;
  /** `partLabel()`, resolved once. */
  label: string;
  chapters: SpineChapterNode[];
}

/** A top-level reading unit: a Part (grouping chapters under a divider) or an ungrouped Chapter,
 *  ordered exactly as the manuscript reads. */
export type SpineNode = SpinePartNode | SpineChapterNode;

export interface ManuscriptSpine {
  schemaVersion: typeof SPINE_SCHEMA_VERSION;
  metadata: ExportMetadata;
  nodes: SpineNode[];
}

function buildSceneNode(scene: { scene_no: number; prose?: string | null }): SpineSceneNode {
  const proseRaw = scene.prose ?? "";
  const hasProse = proseRaw.trim().length > 0;
  const proseRender = beautify(proseRaw);
  const blocks = parseBlocks(proseRender);
  const issues: SpineParseIssue[] = [];
  if (!hasProse) {
    issues.push({ code: "empty_scene", message: `Scene ${scene.scene_no} has no prose.` });
  } else if (blocks.length === 0) {
    issues.push({
      code: "no_blocks_parsed",
      message: `Scene ${scene.scene_no} has prose but parsed to zero blocks.`,
    });
  }
  return {
    sceneNo: scene.scene_no,
    proseRaw,
    proseRender,
    blocks,
    wordCount: wordCount(proseRaw),
    hasProse,
    issues,
  };
}

function buildChapterNode(ch: ManuscriptOut["chapters"][number]): SpineChapterNode {
  const rawKind = ch.kind ?? "chapter";
  const kindRecognized = isKnownChapterKind(rawKind);
  const kind: ChapterKind = kindRecognized ? rawKind : "chapter";
  return {
    type: "chapter",
    chapterNo: ch.chapter_no,
    kind,
    kindRecognized,
    title: ch.title ?? null,
    pov: ch.pov,
    epigraph: ch.epigraph ?? null,
    // Label off the NORMALIZED kind (unknown → "Chapter N"); the recognized flag carries the anomaly.
    label: resolveChapterLabel({ kind, title: ch.title, chapter_no: ch.chapter_no }),
    partId: ch.part_id ?? null,
    scenes: [...ch.scenes].sort((a, b) => a.scene_no - b.scene_no).map(buildSceneNode),
  };
}

/**
 * Tree-ify the flat wire manuscript into the ordered reading spine. Chapters are grouped under their
 * Part (by `part_id`) at the position of the part's FIRST chapter in reading order; ungrouped chapters
 * (or chapters whose `part_id` dangles — references a part not in `parts[]`) render as top-level nodes.
 * Deterministic: input order is normalized by `chapter_no` first, so the spine is identical run to run
 * (which is what makes the golden structure tests meaningful). Non-contiguous part membership does NOT
 * break the build (all of a part's chapters still collect under one node) — preflight flags it instead.
 */
export function buildSpine(ms: ManuscriptOut, metadata: ExportMetadata): ManuscriptSpine {
  const partById = new Map(ms.parts.map((p) => [p.id, p]));
  const emittedParts = new Map<string, SpinePartNode>();
  const nodes: SpineNode[] = [];

  const chaptersInOrder = [...ms.chapters].sort((a, b) => a.chapter_no - b.chapter_no);
  for (const ch of chaptersInOrder) {
    const chNode = buildChapterNode(ch);
    const partId = chNode.partId;
    const part = partId ? partById.get(partId) : undefined;
    if (partId && part) {
      let partNode = emittedParts.get(partId);
      if (!partNode) {
        partNode = {
          type: "part",
          id: part.id,
          partNo: part.part_no,
          title: part.title,
          subtitle: part.subtitle ?? null,
          label: partLabel(part),
          chapters: [],
        };
        emittedParts.set(partId, partNode);
        nodes.push(partNode);
      }
      partNode.chapters.push(chNode);
    } else {
      // Ungrouped, or a dangling part_id (part not present in parts[]): render top-level. The dangling
      // case is a data anomaly preflight surfaces; the spine stays renderable regardless.
      nodes.push(chNode);
    }
  }

  return { schemaVersion: SPINE_SCHEMA_VERSION, metadata, nodes };
}

// --- derived reading-order views (single source of truth for counts + preflight walks) --------------

/** Every chapter node in reading order (part members flattened in, ungrouped chapters in place). */
export function spineChapters(spine: ManuscriptSpine): SpineChapterNode[] {
  return spine.nodes.flatMap((n) => (n.type === "part" ? n.chapters : [n]));
}

/** Every part node in order. */
export function spineParts(spine: ManuscriptSpine): SpinePartNode[] {
  return spine.nodes.filter((n): n is SpinePartNode => n.type === "part");
}

/** Every scene node in reading order. */
export function spineScenes(spine: ManuscriptSpine): SpineSceneNode[] {
  return spineChapters(spine).flatMap((c) => c.scenes);
}

export interface SpineCounts {
  parts: number;
  chapters: number;
  scenes: number;
  words: number;
}

/** Counts for the manifest + page estimates. Only scenes with prose count toward `scenes`/`words` — a
 *  blank placeholder scene is a preflight concern, not a rendered unit. */
export function spineCounts(spine: ManuscriptSpine): SpineCounts {
  const scenes = spineScenes(spine).filter((s) => s.hasProse);
  return {
    parts: spineParts(spine).length,
    chapters: spineChapters(spine).length,
    scenes: scenes.length,
    words: scenes.reduce((acc, s) => acc + s.wordCount, 0),
  };
}

/** Whether anything is renderable at all (the gate the export buttons use). */
export function spineHasProse(spine: ManuscriptSpine): boolean {
  return spineScenes(spine).some((s) => s.hasProse);
}
