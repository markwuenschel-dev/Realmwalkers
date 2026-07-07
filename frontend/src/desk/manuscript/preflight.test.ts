import { describe, expect, it } from "vitest";
import type { ProseBlock } from "../prose";
import type {
  ManuscriptChapter,
  ManuscriptOut,
  ManuscriptPart,
  ManuscriptScene,
  ManuscriptVolume,
} from "../api/types";
import { resolveExportMetadata } from "./metadata";
import { preflight } from "./preflight";
import { resolvePolicy } from "./presets";
import {
  buildSpine,
  SPINE_SCHEMA_VERSION,
  type ManuscriptSpine,
  type SpineChapterNode,
  type SpinePartNode,
  type SpineSceneNode,
  type SpineVolumeNode,
} from "./spine";

const READER = resolvePolicy("reader_proof");
const SHUNN = resolvePolicy("submission_shunn");

// --- fixtures via the real spine builder -----------------------------------------------------------

const scn = (scene_no: number, prose: string | null): ManuscriptScene => ({ scene_no, prose });
const ch = (over: Partial<ManuscriptChapter> & { chapter_no: number }): ManuscriptChapter => ({
  title: null,
  pov: "Marcus",
  kind: "chapter",
  epigraph: null,
  part_id: null,
  scenes: [scn(1, "Some prose.")],
  ...over,
});
const part = (id: string, part_no: number, over: Partial<ManuscriptPart> = {}): ManuscriptPart => ({
  id,
  part_no,
  title: `Part ${part_no}`,
  subtitle: null,
  kind: "part",
  volume_id: null,
  ...over,
});
const vol = (id: string, volume_no: number): ManuscriptVolume => ({
  id,
  volume_no,
  title: `Vol ${volume_no}`,
  subtitle: null,
});
const ms = (over: Partial<ManuscriptOut>): ManuscriptOut => ({
  book_id: "b",
  title: "T",
  series: null,
  book_no: null,
  subtitle: null,
  volumes: [],
  parts: [],
  chapters: [],
  ...over,
});
const spineOf = (m: ManuscriptOut) => buildSpine(m, resolveExportMetadata(m));
const codes = (m: ManuscriptOut, policy = READER) =>
  preflight(spineOf(m), policy).issues.map((i) => i.code);

// --- hand-crafted spine nodes for the defensive checks the parser can't produce ---------------------

const sceneNode = (over: Partial<SpineSceneNode> = {}): SpineSceneNode => ({
  sceneNo: 1,
  proseRaw: "x",
  proseRender: "x",
  blocks: [{ kind: "p", text: "x", n: 0 }],
  wordCount: 1,
  hasProse: true,
  issues: [],
  ...over,
});
const chapterNode = (over: Partial<SpineChapterNode> = {}): SpineChapterNode => ({
  type: "chapter",
  chapterNo: 1,
  kind: "chapter",
  kindRecognized: true,
  sectionType: null,
  title: null,
  pov: "Marcus",
  epigraph: null,
  label: "Chapter 1",
  partId: null,
  scenes: [sceneNode()],
  ...over,
});
const partNode = (over: Partial<SpinePartNode> = {}): SpinePartNode => ({
  type: "part",
  id: "p",
  partNo: 1,
  kind: "part",
  title: "Part 1",
  subtitle: null,
  volumeId: null,
  label: "Part I — Part 1",
  chapters: [chapterNode()],
  ...over,
});
const spineWith = (nodes: ManuscriptSpine["nodes"]): ManuscriptSpine => ({
  schemaVersion: SPINE_SCHEMA_VERSION,
  metadata: { title: "T" },
  nodes,
});

describe("structural integrity checks", () => {
  it("flags duplicate chapter numbers", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1 }), ch({ chapter_no: 1 })] });
    expect(codes(m)).toContain("duplicate_chapter_number");
  });

  it("flags duplicate scene numbers within a chapter", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "a"), scn(1, "b")] })] });
    expect(codes(m)).toContain("duplicate_scene_number");
  });

  it("flags a missing POV on a narrative chapter but not on front matter", () => {
    expect(codes(ms({ chapters: [ch({ chapter_no: 1, pov: "" })] }))).toContain("missing_pov");
    expect(
      codes(ms({ chapters: [ch({ chapter_no: 1, pov: "", kind: "front_matter" })] })),
    ).not.toContain("missing_pov");
  });

  it("warns on an empty chapter and an empty scene", () => {
    const c = codes(ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "   ")] })] }));
    expect(c).toContain("empty_chapter");
    expect(c).toContain("empty_scene");
  });
});

describe("prose-hygiene checks", () => {
  it("flags editorial markers and placeholder text", () => {
    expect(
      codes(ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "He ran. TODO fix this.")] })] })),
    ).toContain("editorial_marker");
    expect(
      codes(ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "lorem ipsum dolor sit.")] })] })),
    ).toContain("placeholder_text");
  });
});

describe("label-contract checks", () => {
  it("warns when an unrecognized kind was coerced to a numbered chapter", () => {
    expect(codes(ms({ chapters: [ch({ chapter_no: 2, kind: "sidebar" })] }))).toContain(
      "unrecognized_chapter_kind",
    );
  });

  it("errors if a non-'chapter' kind is ever labeled as a numbered chapter (regression guard)", () => {
    // Hand-crafted: a prologue whose label wrongly reads "Chapter 2" — the invariant the emitters must
    // never violate. buildSpine can't produce this; the guard exists so a future label regression fails.
    const report = preflight(
      spineWith([chapterNode({ kind: "prologue", label: "Chapter 2" })]),
      READER,
    );
    expect(report.issues.map((i) => i.code)).toContain("kind_label_mismatch");
  });
});

describe("block-support checks", () => {
  it("errors on an unknown block kind", () => {
    const bogus = { kind: "bogus" } as unknown as ProseBlock;
    const report = preflight(
      spineWith([chapterNode({ scenes: [sceneNode({ blocks: [bogus] })] })]),
      READER,
    );
    expect(report.issues.map((i) => i.code)).toContain("unsupported_block_kind");
  });

  it("warns (Shunn) that rich LitRPG blocks will be flattened", () => {
    // preflight keys only off `kind`; the spec shape is irrelevant to this check.
    const interfaceBlock = {
      kind: "interface",
      spec: {},
      lines: ["HP: 10"],
    } as unknown as ProseBlock;
    const report = preflight(
      spineWith([chapterNode({ scenes: [sceneNode({ blocks: [interfaceBlock] })] })]),
      SHUNN,
    );
    expect(report.issues.map((i) => i.code)).toContain("shunn_rich_content_flattened");
  });
});

describe("parts checks", () => {
  it("warns on an ungrouped narrative chapter when the book uses Parts", () => {
    const m = ms({
      parts: [part("p1", 1)],
      chapters: [ch({ chapter_no: 1, part_id: "p1" }), ch({ chapter_no: 2, part_id: null })],
    });
    expect(codes(m)).toContain("ungrouped_chapter_with_parts");
  });

  it("warns on a dangling part reference", () => {
    const m = ms({
      parts: [part("p1", 1)],
      chapters: [ch({ chapter_no: 1, part_id: "p1" }), ch({ chapter_no: 2, part_id: "ghost" })],
    });
    expect(codes(m)).toContain("dangling_part_reference");
  });

  it("warns on non-contiguous part membership", () => {
    // p1 owns chapters 1 and 3; chapter 2 (p2) is interleaved between them.
    const m = ms({
      parts: [part("p1", 1), part("p2", 2)],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1" }),
        ch({ chapter_no: 2, part_id: "p2" }),
        ch({ chapter_no: 3, part_id: "p1" }),
      ],
    });
    expect(codes(m)).toContain("non_contiguous_part");
  });

  it("warns on an empty part / a part with no rendered chapter (hand-crafted)", () => {
    const emptyPart = partNode({
      id: "p",
      partNo: 1,
      title: "Empty",
      label: "Part I — Empty",
      chapters: [],
    });
    const noProsePart = partNode({
      id: "p2",
      partNo: 2,
      title: "Blank",
      label: "Part II — Blank",
      chapters: [chapterNode({ chapterNo: 5, scenes: [sceneNode({ hasProse: false })] })],
    });
    const report = preflight(spineWith([emptyPart, noProsePart]), READER);
    const c = report.issues.map((i) => i.code);
    expect(c).toContain("empty_part");
    expect(c).toContain("part_no_rendered_chapter");
  });
});

describe("volumes checks", () => {
  it("warns on an ungrouped part when the book uses Volumes", () => {
    const m = ms({
      volumes: [vol("v1", 1)],
      parts: [part("p1", 1, { volume_id: "v1" }), part("p2", 2, { volume_id: null })],
      chapters: [ch({ chapter_no: 1, part_id: "p1" }), ch({ chapter_no: 2, part_id: "p2" })],
    });
    expect(codes(m)).toContain("ungrouped_part_with_volumes");
  });

  it("warns on a dangling volume reference", () => {
    const m = ms({
      volumes: [vol("v1", 1)],
      parts: [part("p1", 1, { volume_id: "v1" }), part("p2", 2, { volume_id: "ghost" })],
      chapters: [ch({ chapter_no: 1, part_id: "p1" }), ch({ chapter_no: 2, part_id: "p2" })],
    });
    expect(codes(m)).toContain("dangling_volume_reference");
  });

  it("warns on a volume whose parts have no prose (hand-crafted)", () => {
    const volumeNode: SpineVolumeNode = {
      type: "volume",
      id: "v",
      volumeNo: 1,
      title: "Blank",
      subtitle: null,
      label: "Volume I — Blank",
      parts: [partNode({ chapters: [chapterNode({ scenes: [sceneNode({ hasProse: false })] })] })],
    };
    const report = preflight(spineWith([volumeNode]), READER);
    expect(report.issues.map((i) => i.code)).toContain("volume_no_rendered_chapter");
  });
});

describe("submission-safe gating", () => {
  it("blocks a Shunn export on errors, and an override lets it through", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1, pov: "" }), ch({ chapter_no: 1, pov: "" })] });
    const spine = spineOf(m);
    const blocked = preflight(spine, SHUNN);
    expect(blocked.errorCount).toBeGreaterThan(0);
    expect(blocked.blocked).toBe(true);

    const overridden = preflight(spine, SHUNN, { override: true });
    expect(overridden.blocked).toBe(false);

    // A warn-only preset never blocks, even with the same errors.
    expect(preflight(spine, READER).blocked).toBe(false);
  });
});
