import { describe, expect, it } from "vitest";
import { parseBlocks } from "../prose";
import { resolveSurface } from "./litrpgSurfaces";
import {
  buildManuscriptFrom,
  manuscriptHasProse,
  manuscriptWordCount,
  renderMarkdown,
  renderReaderDoc,
  renderShunnDoc,
  type ManuscriptChapterInput,
} from "./docx";
import { resolveExportMetadata } from "../manuscript/metadata";
import { resolvePolicy } from "../manuscript/presets";
import { buildSpine } from "../manuscript/spine";

const spineFrom = (title: string, chapters: ManuscriptChapterInput[]) => {
  const ms = buildManuscriptFrom(title, chapters);
  return buildSpine(ms, resolveExportMetadata(ms));
};

describe("litrpg interface styling still resolves through the render path", () => {
  it("parses an @interface block and the Reader DOCX builds from it", () => {
    const blocks = parseBlocks(
      "```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
    );
    const block = blocks[0];
    expect(block.kind).toBe("interface");
    if (block.kind !== "interface") return;
    expect(resolveSurface(block.spec).accent).toBeTruthy();

    const spine = spineFrom("Test", [
      {
        chapter_no: 1,
        pov: "X",
        scenes: [
          {
            scene_no: 1,
            prose:
              "```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
          },
        ],
      },
    ]);
    expect(renderReaderDoc(spine, resolvePolicy("reader_proof"))).toBeTruthy();
    expect(renderShunnDoc(spine, resolvePolicy("submission_shunn"))).toBeTruthy();
  });
});

// buildManuscriptFrom is the seam every fragment exporter (Inbox's selected scenes, a single Scene, a
// single chapter, a packet's drafted scene) uses to wrap its data as a ManuscriptOut before the pipeline.
describe("buildManuscriptFrom", () => {
  it("produces the flat wire shape with no parts and null part_id (fragments inherit no book identity)", () => {
    const ms = buildManuscriptFrom("Chapter 3 · Scene 2", [
      { chapter_no: 3, pov: "Serra", scenes: [{ scene_no: 2, prose: "Hello." }] },
    ]);
    expect(ms.parts).toEqual([]);
    expect(ms.series).toBeNull();
    expect(ms.book_no).toBeNull();
    expect(ms.chapters[0].part_id).toBeNull();
  });

  it("sorts chapters and scenes into reading order", () => {
    const ms = buildManuscriptFrom("selected scenes", [
      {
        chapter_no: 2,
        pov: "B",
        scenes: [
          { scene_no: 2, prose: "b2" },
          { scene_no: 1, prose: "b1" },
        ],
      },
      { chapter_no: 1, pov: "A", scenes: [{ scene_no: 1, prose: "a1" }] },
    ]);
    expect(ms.chapters.map((c) => c.chapter_no)).toEqual([1, 2]);
    expect(ms.chapters[1].scenes.map((s) => s.scene_no)).toEqual([1, 2]);
  });
});

describe("manuscriptHasProse / manuscriptWordCount", () => {
  it("detects prose presence and counts words across scenes", () => {
    const empty = buildManuscriptFrom("t", [
      { chapter_no: 1, pov: "X", scenes: [{ scene_no: 1, prose: "   " }] },
    ]);
    expect(manuscriptHasProse(empty)).toBe(false);
    expect(manuscriptHasProse(buildManuscriptFrom("t", []))).toBe(false);

    const full = buildManuscriptFrom("t", [
      {
        chapter_no: 1,
        pov: "X",
        scenes: [
          { scene_no: 1, prose: "one two three" },
          { scene_no: 2, prose: "four five six" },
        ],
      },
    ]);
    expect(manuscriptHasProse(full)).toBe(true);
    expect(manuscriptWordCount(full)).toBe(6);
  });
});

describe("renderMarkdown", () => {
  it("keeps an in-prose day marker verbatim (raw prose is preserved)", () => {
    const spine = spineFrom("t", [
      { chapter_no: 1, pov: "X", scenes: [{ scene_no: 1, prose: "Day 3\n\nHe woke." }] },
    ]);
    const md = renderMarkdown(spine, resolvePolicy("editorial_review"), {
      draft: false,
      exportedAt: "2026-07-07T00:00:00.000Z",
    });
    expect(md).toContain("Day 3");
    expect(md).toContain("# Chapter 1");
  });
});
