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
      title: "The Scrim",
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
