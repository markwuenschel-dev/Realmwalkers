import { describe, expect, it } from "vitest";
import type {
  ManuscriptChapter,
  ManuscriptOut,
  ManuscriptPart,
  ManuscriptScene,
  ManuscriptVolume,
} from "../api/types";
import { resolveExportMetadata } from "./metadata";
import {
  buildSpine,
  spineChapters,
  spineCounts,
  spineHasProse,
  spineParts,
  spineVolumes,
  SPINE_SCHEMA_VERSION,
  type SpineChapterNode,
} from "./spine";

const scn = (scene_no: number, prose: string | null): ManuscriptScene => ({ scene_no, prose });

const ch = (over: Partial<ManuscriptChapter> & { chapter_no: number }): ManuscriptChapter => ({
  title: null,
  pov: "Marcus",
  kind: "chapter",
  epigraph: null,
  part_id: null,
  scenes: [],
  ...over,
});

const part = (
  id: string,
  part_no: number,
  title: string,
  over: Partial<ManuscriptPart> = {},
): ManuscriptPart => ({
  id,
  part_no,
  title,
  subtitle: null,
  kind: "part",
  volume_id: null,
  ...over,
});

const vol = (
  id: string,
  volume_no: number,
  title: string,
  subtitle: string | null = null,
): ManuscriptVolume => ({
  id,
  volume_no,
  title,
  subtitle,
});

const ms = (over: Partial<ManuscriptOut>): ManuscriptOut => ({
  book_id: "b",
  title: "Realmwalkers",
  series: null,
  book_no: null,
  subtitle: null,
  volumes: [],
  parts: [],
  chapters: [],
  ...over,
});

const spineOf = (m: ManuscriptOut) => buildSpine(m, resolveExportMetadata(m));

// A stable structural fingerprint of the top-level reading order, for the tree-shape assertions.
const topLevel = (m: ManuscriptOut): string[] =>
  spineOf(m).nodes.map((n) =>
    n.type === "volume"
      ? `vol:${n.volumeNo}`
      : n.type === "part"
        ? `part:${n.partNo}`
        : `ch:${n.chapterNo}`,
  );

describe("buildSpine tree-ification (parts)", () => {
  it("groups chapters under their part in reading order and leaves ungrouped chapters top-level", () => {
    const m = ms({
      parts: [part("p1", 1, "The Scrim"), part("p2", 2, "The Reserve", { subtitle: "descent" })],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "One.")] }),
        ch({ chapter_no: 2, part_id: "p1", scenes: [scn(1, "Two.")] }),
        ch({ chapter_no: 3, part_id: "p2", scenes: [scn(1, "Three.")] }),
        ch({ chapter_no: 4, part_id: null, scenes: [scn(1, "Four.")] }),
      ],
    });
    const spine = spineOf(m);
    expect(spine.schemaVersion).toBe(SPINE_SCHEMA_VERSION);
    expect(topLevel(m)).toEqual(["part:1", "part:2", "ch:4"]);
    const p1 = spine.nodes[0];
    expect(p1.type === "part" && p1.chapters.map((c) => c.chapterNo)).toEqual([1, 2]);
    expect(p1.type === "part" && p1.label).toBe("Part I — The Scrim");
  });

  it("keeps global chapter numbering across parts (a Part groups, it does not renumber)", () => {
    const m = ms({
      parts: [part("p1", 1, "One"), part("p2", 2, "Two")],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] }),
        ch({ chapter_no: 2, part_id: "p2", scenes: [scn(1, "b")] }),
      ],
    });
    expect(spineChapters(spineOf(m)).map((c) => c.label)).toEqual(["Chapter 1", "Chapter 2"]);
  });

  it("renders a chapter whose part_id dangles (no such part) as an ungrouped top-level node", () => {
    const m = ms({
      parts: [part("p1", 1, "One")],
      chapters: [ch({ chapter_no: 1, part_id: "ghost", scenes: [scn(1, "x")] })],
    });
    const spine = spineOf(m);
    expect(spine.nodes).toHaveLength(1);
    expect(spine.nodes[0].type).toBe("chapter");
  });

  it("sorts chapters by chapter_no regardless of input order (deterministic spine)", () => {
    const m = ms({
      chapters: [
        ch({ chapter_no: 3, scenes: [scn(1, "c")] }),
        ch({ chapter_no: 1, scenes: [scn(1, "a")] }),
        ch({ chapter_no: 2, scenes: [scn(1, "b")] }),
      ],
    });
    expect(spineChapters(spineOf(m)).map((c) => c.chapterNo)).toEqual([1, 2, 3]);
  });
});

describe("buildSpine tree-ification (Volume tier)", () => {
  it("nests parts under volumes (Book → Volume → Part → Chapter) in reading order", () => {
    const m = ms({
      volumes: [vol("v1", 1, "The Long Winter"), vol("v2", 2, "The Thaw")],
      parts: [
        part("p1", 1, "Frost", { volume_id: "v1" }),
        part("p2", 2, "Ice", { volume_id: "v1" }),
        part("p3", 3, "Melt", { volume_id: "v2" }),
      ],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] }),
        ch({ chapter_no: 2, part_id: "p2", scenes: [scn(1, "b")] }),
        ch({ chapter_no: 3, part_id: "p3", scenes: [scn(1, "c")] }),
      ],
    });
    const spine = spineOf(m);
    expect(topLevel(m)).toEqual(["vol:1", "vol:2"]);
    const v1 = spine.nodes[0];
    expect(v1.type === "volume" && v1.label).toBe("Volume I — The Long Winter");
    expect(v1.type === "volume" && v1.parts.map((p) => p.partNo)).toEqual([1, 2]);
    // Derived views recurse through volumes.
    expect(spineVolumes(spine).map((v) => v.volumeNo)).toEqual([1, 2]);
    expect(spineParts(spine).map((p) => p.partNo)).toEqual([1, 2, 3]);
    expect(spineChapters(spine).map((c) => c.chapterNo)).toEqual([1, 2, 3]);
  });

  it("interleaves an ungrouped part and a volume-nested part at their reading positions", () => {
    const m = ms({
      volumes: [vol("v1", 1, "V1")],
      parts: [part("p1", 1, "Top-level"), part("p2", 2, "Nested", { volume_id: "v1" })],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] }),
        ch({ chapter_no: 2, part_id: "p2", scenes: [scn(1, "b")] }),
      ],
    });
    expect(topLevel(m)).toEqual(["part:1", "vol:1"]);
  });

  it("renders a part whose volume_id dangles as a top-level part", () => {
    const m = ms({
      volumes: [vol("v1", 1, "V1")],
      parts: [part("p1", 1, "Orphan", { volume_id: "ghost" })],
      chapters: [ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] })],
    });
    const spine = spineOf(m);
    expect(spine.nodes).toHaveLength(1);
    expect(spine.nodes[0].type).toBe("part");
  });

  it("renders an Act (Part with kind=act) with the Act label word", () => {
    const m = ms({
      parts: [part("p1", 1, "Rising", { kind: "act" })],
      chapters: [ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] })],
    });
    const p = spineParts(spineOf(m))[0];
    expect(p.kind).toBe("act");
    expect(p.label).toBe("Act I — Rising");
  });
});

describe("scene node — prose fidelity", () => {
  it("keeps raw and beautified prose SEPARATE (raw preserves '--', render normalizes to em dash)", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "He paused -- then ran.")] })] });
    const sc = spineChapters(spineOf(m))[0].scenes[0];
    expect(sc.proseRaw).toContain("--");
    expect(sc.proseRaw).not.toContain("—");
    expect(sc.proseRender).toContain("—");
    expect(sc.blocks.length).toBeGreaterThan(0);
    expect(sc.hasProse).toBe(true);
    expect(sc.wordCount).toBeGreaterThan(0);
  });

  it("flags an empty scene and reports no prose", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "   ")] })] });
    const sc = spineChapters(spineOf(m))[0].scenes[0];
    expect(sc.hasProse).toBe(false);
    expect(sc.issues.map((i) => i.code)).toContain("empty_scene");
  });
});

describe("chapter node — kind + label", () => {
  it("resolves the label once and marks an unknown kind unrecognized (coerced to a numbered chapter)", () => {
    const m = ms({
      chapters: [
        ch({ chapter_no: 1, kind: "prologue", scenes: [scn(1, "p")] }),
        ch({ chapter_no: 2, kind: "sidebar", scenes: [scn(1, "s")] }),
      ],
    });
    const [c1, c2] = spineChapters(spineOf(m));
    expect(c1.kind).toBe("prologue");
    expect(c1.kindRecognized).toBe(true);
    expect(c1.label).toBe("Prologue");
    expect(c2.kind).toBe("chapter");
    expect(c2.kindRecognized).toBe(false);
    expect(c2.label).toBe("Chapter 2");
  });
});

describe("derived views", () => {
  it("counts volumes/parts and only prose-bearing scenes", () => {
    const m = ms({
      volumes: [vol("v1", 1, "V1")],
      parts: [part("p1", 1, "One", { volume_id: "v1" })],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "two words"), scn(2, "   ")] }),
        ch({ chapter_no: 2, scenes: [scn(1, "one")] }),
      ],
    });
    const spine = spineOf(m);
    const counts = spineCounts(spine);
    expect(counts.volumes).toBe(1);
    expect(counts.parts).toBe(1);
    expect(counts.chapters).toBe(2);
    expect(counts.scenes).toBe(2);
    expect(counts.words).toBe(3);
    expect(spineHasProse(spine)).toBe(true);
  });
});

// A cross-emitter helper other tests reuse: the canonical ordered label sequence a spine renders.
export function orderedLabels(chapters: SpineChapterNode[]): string[] {
  return chapters.map((c) => c.label);
}
