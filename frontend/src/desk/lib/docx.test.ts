import { describe, expect, it } from "vitest";
import type { ManuscriptOut } from "../api/types";
import {
  buildManuscriptMarkdown,
  formatInterfaceShunnHeader,
  markdownFilename,
} from "./docx";

const sampleManuscript = (): ManuscriptOut => ({
  book_id: "book-uuid-1",
  title: "Realmwalkers",
  chapters: [
    {
      chapter_no: 1,
      title: "Awakening",
      pov: "Marcus",
      scenes: [
        {
          scene_no: 1,
          prose: "The sky hummed.\n\n```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```",
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
      scenes: [{ scene_no: 1, prose: "   " }],
    },
  ],
});

describe("buildManuscriptMarkdown", () => {
  it("includes YAML front matter", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).toMatch(/^---\n/);
    expect(md).toContain('title: "Realmwalkers"');
    expect(md).toContain('book_id: "book-uuid-1"');
    expect(md).toContain("export: semantic-markdown");
    expect(md).toContain("compile: approved");
  });

  it("adds scene comments and preserves prose verbatim", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).toContain("<!-- Scene 1 -->");
    expect(md).toContain("@interface role=insight creature=archdemon domain=death");
    expect(md).toContain("* * *");
    expect(md).toContain("Second scene prose.");
    expect(md).toContain("— End of Chapter 1 —");
  });

  it("skips empty chapters", () => {
    const md = buildManuscriptMarkdown(sampleManuscript());
    expect(md).not.toContain("Chapter 2");
  });

  it("records draft compile mode", () => {
    const md = buildManuscriptMarkdown(sampleManuscript(), { compile: "draft" });
    expect(md).toContain("compile: draft");
  });
});

describe("formatInterfaceShunnHeader", () => {
  it("formats the Shunn plain header", () => {
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
