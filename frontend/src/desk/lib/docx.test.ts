import { describe, expect, it } from "vitest";
import type { ManuscriptOut } from "../api/types";
import { parseBlocks } from "../prose";
import { resolveSurface } from "./litrpgSurfaces";
import {
  buildManuscriptDoc,
  buildManuscriptFrom,
  buildManuscriptMarkdown,
  buildShunnDoc,
  formatInterfaceShunnHeader,
  manuscriptHasProse,
  manuscriptWordCount,
  markdownFilename,
} from "./docx";

describe("export integration", () => {
  it("docx path resolves interface styling via litrpgSurfaces", () => {
    const blocks = parseBlocks(
      "```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
    );
    const block = blocks[0];
    expect(block.kind).toBe("interface");
    if (block.kind !== "interface") return;
    expect(resolveSurface(block.spec).accent).toBeTruthy();
    expect(
      buildManuscriptDoc({
        book_id: "b1",
        title: "Test",
        chapters: [
          {
            chapter_no: 1,
            title: null,
            pov: "X",
            kind: "chapter",
            scenes: [
              {
                scene_no: 1,
                prose:
                  "```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
              },
            ],
          },
        ],
      }),
    ).toBeTruthy();
  });
});

const sampleManuscript = (): ManuscriptOut => ({
  book_id: "book-uuid-1",
  title: "Realmwalkers",
  chapters: [
    {
      chapter_no: 1,
      title: "The Scrim",
      pov: "Marcus",
      kind: "chapter",
      scenes: [
        {
          scene_no: 1,
          prose:
            "The sky hummed.\n\n```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
        },
        {
          scene_no: 2,
          prose: "Second scene prose.",
        },
      ],
    },
    {
      chapter_no: 2,
      title: null,
      pov: "Serra",
      kind: "chapter",
      scenes: [{ scene_no: 1, prose: "   " }],
    },
  ],
});

describe("buildManuscriptMarkdown", () => {
  it("includes dominion-manuscript/v1 front matter", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).toMatch(/^---\n/);
    expect(md).toContain("schema: dominion-manuscript/v1");
    expect(md).toContain('title: "Realmwalkers"');
    expect(md).toContain('series: "Dominion Realm"');
    expect(md).toContain("book: 1");
    expect(md).toContain("source: writers-desk");
    expect(md).toContain("format: semantic-markdown");
    expect(md).toContain("interface_style: professional");
    expect(md).toContain("litrpg_ui: true");
    expect(md).toContain("draft: false");
    expect(md).toContain("exported_at:");
  });

  it("includes title, chapter comments, and preserves prose verbatim", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).toContain("# Realmwalkers");
    expect(md).toContain("# Chapter 1 — The Scrim");
    expect(md).toContain('<!-- chapter number=1 title="The Scrim" pov="Marcus" -->');
    expect(md).toContain("<!-- scene index=1 scene_no=1 -->");
    expect(md).toContain("<!-- scene index=2 scene_no=2 -->");
    expect(md).toContain("@interface role=insight creature=archdemon domain=death");
    expect(md).toContain("Second scene prose.");
    expect(md).not.toContain("*POV —");
    expect(md).not.toContain("— End of Chapter");
  });

  it("skips empty chapters", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).not.toContain("Chapter 2");
  });

  it("records draft flag", () => {
    const md = buildManuscriptMarkdown(sampleManuscript(), { draft: true });
    expect(md).toContain("draft: true");
  });
});

describe("day/date markers in the export path", () => {
  const dayMs = (): ManuscriptOut => ({
    book_id: "b1",
    title: "T",
    chapters: [
      {
        chapter_no: 1,
        title: null,
        pov: "X",
        kind: "chapter",
        scenes: [{ scene_no: 1, prose: "Day 3\n\nI woke on the cold stone floor." }],
      },
    ],
  });

  it("both DOCX builders render a manuscript containing a day marker", () => {
    expect(buildManuscriptDoc(dayMs())).toBeTruthy();
    expect(buildShunnDoc(dayMs(), "A. Author", 8)).toBeTruthy();
  });

  it("keeps the day marker verbatim in the raw-source Markdown export", () => {
    expect(buildManuscriptMarkdown(dayMs())).toContain("Day 3");
  });
});

describe("formatInterfaceShunnHeader", () => {
  it("formats the Shunn plain header from InterfaceSpec", () => {
    expect(
      formatInterfaceShunnHeader({
        role: "insight",
        creature: "archdemon",
        domain: "death",
      }),
    ).toBe("[ INSIGHT ] CREATURE SCAN · ARCHDEMON · DEATH");
  });
});

describe("markdownFilename", () => {
  it("sanitizes title and adds .md extension", () => {
    expect(markdownFilename("Realm Walkers!")).toBe("Realm_Walkers.md");
  });
});

// buildManuscriptFrom is what lets every screen (Inbox's selected scenes, a single Scene, a single
// Chapter, a drafted scene-packet scene) wrap its own data as a one-off ManuscriptOut and reuse
// buildManuscriptMarkdown / buildManuscriptDoc / buildShunnDoc verbatim — so this is the seam that
// keeps every tab's export byte-for-byte identical to the Manuscript tab's.
describe("buildManuscriptFrom", () => {
  it("wraps a single chapter with a single scene (the Scene-screen shape)", () => {
    const ms = buildManuscriptFrom("Chapter 3 · Scene 2", [
      {
        chapter_no: 3,
        title: "The Return",
        pov: "Mara",
        scenes: [{ scene_no: 2, prose: "Text." }],
      },
    ]);
    expect(ms.title).toBe("Chapter 3 · Scene 2");
    expect(ms.book_id).toBe("");
    expect(ms.chapters).toHaveLength(1);
    expect(ms.chapters[0]).toEqual({
      chapter_no: 3,
      title: "The Return",
      pov: "Mara",
      kind: "chapter",
      epigraph: null,
      scenes: [{ scene_no: 2, prose: "Text." }],
    });
  });

  it("defaults a missing chapter title to null", () => {
    const ms = buildManuscriptFrom("Chapter 3", [
      { chapter_no: 3, pov: "Mara", scenes: [{ scene_no: 1, prose: "Text." }] },
    ]);
    expect(ms.chapters[0].title).toBeNull();
  });

  it("sorts chapters by chapter_no and scenes by scene_no (Inbox's multi-chapter selection shape)", () => {
    const ms = buildManuscriptFrom("selected scenes", [
      { chapter_no: 2, title: null, pov: "Serra", scenes: [{ scene_no: 1, prose: "b" }] },
      {
        chapter_no: 1,
        title: "The Scrim",
        pov: "Marcus",
        scenes: [
          { scene_no: 2, prose: "second" },
          { scene_no: 1, prose: "first" },
        ],
      },
    ]);
    expect(ms.chapters.map((c) => c.chapter_no)).toEqual([1, 2]);
    expect(ms.chapters[0].scenes.map((s) => s.scene_no)).toEqual([1, 2]);
  });

  it("feeds straight into buildManuscriptMarkdown/buildManuscriptDoc unchanged", () => {
    const ms = buildManuscriptFrom("Chapter 1", [
      {
        chapter_no: 1,
        title: "The Scrim",
        pov: "Marcus",
        scenes: [{ scene_no: 1, prose: "Hello." }],
      },
    ]);
    const md = buildManuscriptMarkdown(ms);
    expect(md).toContain("schema: dominion-manuscript/v1");
    expect(md).toContain("# Chapter 1 — The Scrim");
    expect(md).toContain("Hello.");
    expect(buildManuscriptDoc(ms)).toBeTruthy();
  });
});

describe("manuscriptHasProse", () => {
  it("is false when every scene is empty/whitespace", () => {
    const ms = buildManuscriptFrom("t", [
      { chapter_no: 1, pov: "X", scenes: [{ scene_no: 1, prose: "   " }] },
    ]);
    expect(manuscriptHasProse(ms)).toBe(false);
  });

  it("is false with no chapters at all", () => {
    expect(manuscriptHasProse(buildManuscriptFrom("t", []))).toBe(false);
  });

  it("is true once any scene has prose", () => {
    const ms = buildManuscriptFrom("t", [
      { chapter_no: 1, pov: "X", scenes: [{ scene_no: 1, prose: "Something." }] },
    ]);
    expect(manuscriptHasProse(ms)).toBe(true);
  });
});

describe("manuscriptWordCount", () => {
  it("sums words across every scene and chapter", () => {
    const ms = buildManuscriptFrom("t", [
      {
        chapter_no: 1,
        pov: "X",
        scenes: [
          { scene_no: 1, prose: "one two three" },
          { scene_no: 2, prose: "four five" },
        ],
      },
      { chapter_no: 2, pov: "Y", scenes: [{ scene_no: 1, prose: "six" }] },
    ]);
    expect(manuscriptWordCount(ms)).toBe(6);
  });

  it("is zero for a manuscript with no prose", () => {
    const ms = buildManuscriptFrom("t", [{ chapter_no: 1, pov: "X", scenes: [] }]);
    expect(manuscriptWordCount(ms)).toBe(0);
  });
});
