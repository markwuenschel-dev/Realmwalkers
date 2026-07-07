import { describe, expect, it } from "vitest";
import type { ManuscriptChapter, ManuscriptOut, ManuscriptScene } from "../api/types";
import { resolveExportMetadata } from "./metadata";
import {
  buildSpine,
  spineChapters,
  spineCounts,
  spineHasProse,
  spineParts,
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

const ms = (over: Partial<ManuscriptOut>): ManuscriptOut => ({
  book_id: "b",
  title: "Realmwalkers",
  series: null,
  book_no: null,
  subtitle: null,
  parts: [],
  chapters: [],
  ...over,
});

const spineOf = (m: ManuscriptOut) => buildSpine(m, resolveExportMetadata(m));

describe("buildSpine tree-ification", () => {
  it("groups chapters under their part in reading order and leaves ungrouped chapters top-level", () => {
    const m = ms({
      parts: [
        { id: "p1", part_no: 1, title: "The Scrim", subtitle: null },
        { id: "p2", part_no: 2, title: "The Reserve", subtitle: "descent" },
      ],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "One.")] }),
        ch({ chapter_no: 2, part_id: "p1", scenes: [scn(1, "Two.")] }),
        ch({ chapter_no: 3, part_id: "p2", scenes: [scn(1, "Three.")] }),
        ch({ chapter_no: 4, part_id: null, scenes: [scn(1, "Four.")] }),
      ],
    });
    const spine = spineOf(m);
    expect(spine.schemaVersion).toBe(SPINE_SCHEMA_VERSION);
    expect(
      spine.nodes.map((n) => (n.type === "part" ? `part:${n.partNo}` : `ch:${n.chapterNo}`)),
    ).toEqual(["part:1", "part:2", "ch:4"]);
    const p1 = spine.nodes[0];
    expect(p1.type === "part" && p1.chapters.map((c) => c.chapterNo)).toEqual([1, 2]);
    expect(p1.type === "part" && p1.label).toBe("Part I — The Scrim");
  });

  it("keeps global chapter numbering across parts (a Part groups, it does not renumber)", () => {
    const m = ms({
      parts: [
        { id: "p1", part_no: 1, title: "One", subtitle: null },
        { id: "p2", part_no: 2, title: "Two", subtitle: null },
      ],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "a")] }),
        ch({ chapter_no: 2, part_id: "p2", scenes: [scn(1, "b")] }),
      ],
    });
    expect(spineChapters(spineOf(m)).map((c) => c.label)).toEqual(["Chapter 1", "Chapter 2"]);
  });

  it("renders a chapter whose part_id dangles (no such part) as an ungrouped top-level node", () => {
    const m = ms({
      parts: [{ id: "p1", part_no: 1, title: "One", subtitle: null }],
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

describe("scene node — prose fidelity", () => {
  it("keeps raw and beautified prose SEPARATE (raw preserves '--', render normalizes to em dash)", () => {
    const m = ms({ chapters: [ch({ chapter_no: 1, scenes: [scn(1, "He paused -- then ran.")] })] });
    const sc = spineChapters(spineOf(m))[0].scenes[0];
    expect(sc.proseRaw).toContain("--");
    expect(sc.proseRaw).not.toContain("—");
    expect(sc.proseRender).toContain("—"); // beautify() typeset the dash
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
    expect(c2.kind).toBe("chapter"); // coerced
    expect(c2.kindRecognized).toBe(false);
    expect(c2.label).toBe("Chapter 2");
  });
});

describe("derived views", () => {
  it("counts only prose-bearing scenes and reports parts/chapters totals", () => {
    const m = ms({
      parts: [{ id: "p1", part_no: 1, title: "One", subtitle: null }],
      chapters: [
        ch({ chapter_no: 1, part_id: "p1", scenes: [scn(1, "two words"), scn(2, "   ")] }),
        ch({ chapter_no: 2, scenes: [scn(1, "one")] }),
      ],
    });
    const spine = spineOf(m);
    const counts = spineCounts(spine);
    expect(counts.parts).toBe(1);
    expect(counts.chapters).toBe(2);
    expect(counts.scenes).toBe(2); // the blank scene is excluded
    expect(counts.words).toBe(3);
    expect(spineParts(spine)).toHaveLength(1);
    expect(spineHasProse(spine)).toBe(true);
  });
});

// A cross-emitter helper other tests reuse: the canonical ordered label sequence a spine renders.
export function orderedLabels(chapters: SpineChapterNode[]): string[] {
  return chapters.map((c) => c.label);
}
