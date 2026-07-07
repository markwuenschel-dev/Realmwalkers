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
import {
  isKnownChapterKind,
  partLabel,
  resolveChapterLabel,
  volumeLabel,
  type ChapterKind,
} from "./labels";
import type { ExportMetadata } from "./metadata";

/** Bump when the spine's SHAPE changes in a way emitters/manifests must notice. Stamped into every
 *  ExportManifest as provenance. (`/2` added the Volume tier + Part.kind/volumeId.) */
export const SPINE_SCHEMA_VERSION = "manuscript-spine/2" as const;

export type PartKind = "part" | "act";

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
  /** Label word only — an Act is a Part rendered as "Act I". */
  kind: PartKind;
  title: string;
  subtitle: string | null;
  /** The Volume this Part is nested under, or null (top-level part). */
  volumeId: string | null;
  /** `partLabel()`, resolved once. */
  label: string;
  chapters: SpineChapterNode[];
}

export interface SpineVolumeNode {
  type: "volume";
  id: string;
  volumeNo: number;
  title: string;
  subtitle: string | null;
  /** `volumeLabel()`, resolved once. */
  label: string;
  parts: SpinePartNode[];
}

/** A top-level reading unit: a Volume (grouping Parts), a Part (grouping chapters), or an ungrouped
 *  Chapter, ordered exactly as the manuscript reads. */
export type SpineNode = SpineVolumeNode | SpinePartNode | SpineChapterNode;

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
 * Tree-ify the flat wire manuscript into the ordered reading spine (Book → Volume → Part → Chapter).
 * A grouping node is emitted at the position of its FIRST member in reading order, and members collect
 * under it thereafter — so a Part appears where its first chapter reads, and a Volume where its first
 * part's first chapter reads. Ungrouped chapters, chapters with a dangling `part_id`, and parts with a
 * dangling `volume_id` render at their natural (higher) level. Deterministic: chapters are normalized by
 * `chapter_no` first, so the spine is identical run to run (what makes the golden structure tests
 * meaningful). Non-contiguous membership does NOT break the build — preflight flags it instead.
 */
export function buildSpine(ms: ManuscriptOut, metadata: ExportMetadata): ManuscriptSpine {
  const partById = new Map(ms.parts.map((p) => [p.id, p]));
  const volumeById = new Map(ms.volumes.map((v) => [v.id, v]));
  const emittedParts = new Map<string, SpinePartNode>();
  const emittedVolumes = new Map<string, SpineVolumeNode>();
  const nodes: SpineNode[] = [];

  // Get-or-create the Volume node, pushing it to the top level at first sight.
  const volumeNodeFor = (v: ManuscriptOut["volumes"][number]): SpineVolumeNode => {
    let node = emittedVolumes.get(v.id);
    if (!node) {
      node = {
        type: "volume",
        id: v.id,
        volumeNo: v.volume_no,
        title: v.title,
        subtitle: v.subtitle ?? null,
        label: volumeLabel(v),
        parts: [],
      };
      emittedVolumes.set(v.id, node);
      nodes.push(node);
    }
    return node;
  };

  // Get-or-create the Part node, attaching it to its Volume (or the top level) at first sight.
  const partNodeFor = (p: ManuscriptOut["parts"][number]): SpinePartNode => {
    let node = emittedParts.get(p.id);
    if (!node) {
      node = {
        type: "part",
        id: p.id,
        partNo: p.part_no,
        kind: p.kind === "act" ? "act" : "part",
        title: p.title,
        subtitle: p.subtitle ?? null,
        volumeId: p.volume_id ?? null,
        label: partLabel(p),
        chapters: [],
      };
      emittedParts.set(p.id, node);
      const volume = p.volume_id ? volumeById.get(p.volume_id) : undefined;
      if (volume) volumeNodeFor(volume).parts.push(node);
      else nodes.push(node); // ungrouped part, or dangling volume_id → top-level
    }
    return node;
  };

  const chaptersInOrder = [...ms.chapters].sort((a, b) => a.chapter_no - b.chapter_no);
  for (const ch of chaptersInOrder) {
    const chNode = buildChapterNode(ch);
    const part = chNode.partId ? partById.get(chNode.partId) : undefined;
    if (part) partNodeFor(part).chapters.push(chNode);
    else nodes.push(chNode); // ungrouped, or dangling part_id → top-level
  }

  return { schemaVersion: SPINE_SCHEMA_VERSION, metadata, nodes };
}

// --- derived reading-order views (single source of truth for counts + preflight walks) --------------

/** Every volume node in order. */
export function spineVolumes(spine: ManuscriptSpine): SpineVolumeNode[] {
  return spine.nodes.filter((n): n is SpineVolumeNode => n.type === "volume");
}

/** Every part node in reading order (volume members flattened in, top-level parts in place). */
export function spineParts(spine: ManuscriptSpine): SpinePartNode[] {
  return spine.nodes.flatMap((n) => (n.type === "volume" ? n.parts : n.type === "part" ? [n] : []));
}

/** Every chapter node in reading order (recurses volumes → parts → chapters; ungrouped chapters in place). */
export function spineChapters(spine: ManuscriptSpine): SpineChapterNode[] {
  return spine.nodes.flatMap((n) =>
    n.type === "volume" ? n.parts.flatMap((p) => p.chapters) : n.type === "part" ? n.chapters : [n],
  );
}

/** Every scene node in reading order. */
export function spineScenes(spine: ManuscriptSpine): SpineSceneNode[] {
  return spineChapters(spine).flatMap((c) => c.scenes);
}

export interface SpineCounts {
  volumes: number;
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
    volumes: spineVolumes(spine).length,
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
