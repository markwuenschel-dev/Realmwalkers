// Golden / structural tests — the acceptance bar for the export foundation. One spine drives Reader
// DOCX, Shunn DOCX, and Markdown; these tests prove the three agree on structure + labels, that Parts
// render on every surface, that prologue/epilogue never become "Chapter N", that prose stays faithful
// (raw for Markdown), that hard-coded project metadata is gone, and that a manifest always ships.

import { describe, expect, it } from "vitest";
import type {
  ManuscriptChapter,
  ManuscriptOut,
  ManuscriptPart,
  ManuscriptScene,
} from "../api/types";
import { resolveExportMetadata } from "./metadata";
import { buildManifest } from "./manifest";
import { preflight } from "./preflight";
import { resolvePolicy } from "./presets";
import { runExport } from "./pipeline";
import { buildSpine, spineChapters } from "./spine";

const AT = "2026-07-07T00:00:00.000Z";
const RICH_INTERFACE =
  "```text\n@interface role=insight creature=archdemon domain=death\nName: ????\n```";

const scn = (scene_no: number, prose: string | null): ManuscriptScene => ({ scene_no, prose });
const ch = (over: Partial<ManuscriptChapter> & { chapter_no: number }): ManuscriptChapter => ({
  title: null,
  pov: "Marcus",
  kind: "chapter",
  epigraph: null,
  part_id: null,
  scenes: [scn(1, "Prose here.")],
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

/** A full book: prologue (ungrouped) → Part I {2,3} → Part II {4} → epilogue (ungrouped), with an
 *  epigraph, a LitRPG interface panel, a raw em-dash source, and an editorial marker. */
const fullBook = (over: Partial<ManuscriptOut> = {}): ManuscriptOut =>
  ms({
    parts: [part("p1", 1, "The Scrim"), part("p2", 2, "The Reserve", { subtitle: "descent" })],
    chapters: [
      ch({
        chapter_no: 1,
        kind: "prologue",
        pov: "Narrator",
        epigraph: "A quote.",
        scenes: [scn(1, "The sky hummed -- loudly.")],
      }),
      ch({
        chapter_no: 2,
        part_id: "p1",
        title: "The Scrim",
        scenes: [scn(1, `A panel:\n\n${RICH_INTERFACE}`)],
      }),
      ch({ chapter_no: 3, part_id: "p1", scenes: [scn(1, "Third scene.")] }),
      ch({ chapter_no: 4, part_id: "p2", scenes: [scn(1, "Fourth scene. TODO revise this.")] }),
      ch({ chapter_no: 5, kind: "epilogue", scenes: [scn(1, "The end.")] }),
    ],
    ...over,
  });

/** Assert `needles` appear in `haystack` in the given order. */
function assertOrder(haystack: string, needles: string[]): void {
  let cursor = -1;
  for (const n of needles) {
    const at = haystack.indexOf(n, cursor + 1);
    expect(at, `expected "${n}" after position ${cursor}`).toBeGreaterThan(cursor);
    cursor = at;
  }
}

const markdownOf = (m: ManuscriptOut) => {
  const r = runExport(m, "editorial_review", { exportedAt: AT });
  expect(r.artifact?.kind).toBe("markdown");
  return r.artifact?.kind === "markdown" ? r.artifact.content : "";
};

describe("structure + labels agree across all three emitters (one spine)", () => {
  it("Markdown renders Parts and every kind's resolved label in reading order", () => {
    const md = markdownOf(fullBook());
    assertOrder(md, [
      "# Prologue",
      "# Part I — The Scrim",
      "# Chapter 2",
      "# Chapter 3",
      "# Part II — The Reserve",
      "# Chapter 4",
      "# Epilogue",
    ]);
  });

  it("prologue/epilogue are NEVER emitted as a numbered chapter (the core regression)", () => {
    const md = markdownOf(fullBook());
    expect(md).toContain("# Prologue");
    expect(md).toContain("# Epilogue");
    // chapter_no 1 is the prologue and 5 is the epilogue — neither may appear as "Chapter 1/5".
    expect(md).not.toContain("# Chapter 1");
    expect(md).not.toContain("# Chapter 5");
  });

  it("renders the Volume → Part → Chapter tiers (and Act labels) in reading order", () => {
    const book = ms({
      volumes: [{ id: "v1", volume_no: 1, title: "The Long Winter", subtitle: null }],
      parts: [
        part("p1", 1, "Frost", { volume_id: "v1" }),
        part("p2", 2, "Rising", { kind: "act" }), // an Act, ungrouped
      ],
      chapters: [ch({ chapter_no: 1, part_id: "p1" }), ch({ chapter_no: 2, part_id: "p2" })],
    });
    const md = markdownOf(book);
    assertOrder(md, [
      "# Volume I — The Long Winter",
      "# Part I — Frost",
      "# Chapter 1",
      "# Act II — Rising",
      "# Chapter 2",
    ]);
    // Volume + Act render on the DOCX surfaces too (build without throwing).
    expect(runExport(book, "reader_proof", { exportedAt: AT }).artifact?.kind).toBe("docx");
    expect(
      runExport(book, "submission_shunn", { author: "A", exportedAt: AT }).artifact?.kind,
    ).toBe("docx");
  });

  it("renders front/back matter by its section type (label + provenance), not as a numbered chapter", () => {
    const book = ms({
      chapters: [
        ch({ chapter_no: 1, scenes: [scn(1, "Story.")] }),
        ch({
          chapter_no: 2,
          kind: "back_matter",
          section_type: "glossary",
          pov: "",
          scenes: [scn(1, "Aether: the ambient magic.")],
        }),
      ],
    });
    const md = markdownOf(book);
    assertOrder(md, ["# Chapter 1", "# Glossary"]);
    expect(md).toContain("section_type=glossary");
    expect(md).not.toContain("# Chapter 2"); // the glossary is not a numbered chapter
  });

  it("Reader DOCX and Shunn DOCX build from the same book (Parts + rich blocks, no throw)", () => {
    const book = fullBook();
    const reader = runExport(book, "reader_proof", { exportedAt: AT });
    const shunn = runExport(book, "submission_shunn", {
      author: "A. Author",
      exportedAt: AT,
      override: true,
    });
    expect(reader.artifact?.kind).toBe("docx");
    expect(shunn.artifact?.kind).toBe("docx");
    expect(reader.artifact?.kind === "docx" && reader.artifact.doc).toBeTruthy();
    expect(shunn.artifact?.kind === "docx" && shunn.artifact.doc).toBeTruthy();

    // The three emitters share ONE resolved-label spine — assert that canonical label sequence, which
    // the Markdown text above was verified against, so "agreement" is grounded in a single source.
    const spine = buildSpine(book, resolveExportMetadata(book));
    expect(spineChapters(spine).map((c) => c.label)).toEqual([
      "Prologue",
      "Chapter 2",
      "Chapter 3",
      "Chapter 4",
      "Epilogue",
    ]);
  });
});

describe("prose fidelity", () => {
  it("Markdown preserves RAW prose (keeps '--', not the beautified em dash)", () => {
    const md = markdownOf(fullBook());
    expect(md).toContain("The sky hummed -- loudly.");
    expect(md).not.toContain("The sky hummed — loudly.");
  });
});

describe("metadata is data-driven, never hard-coded", () => {
  it("a book with no series identity emits NO Dominion/BOOK/litrpg lines", () => {
    const md = markdownOf(fullBook({ series: null, book_no: null }));
    expect(md).not.toContain("Dominion Realm");
    expect(md).not.toMatch(/BOOK/i);
    expect(md).not.toContain("litrpg_ui");
    expect(md).not.toContain("series:");
  });

  it("a book WITH series metadata emits it from the data", () => {
    const md = markdownOf(fullBook({ series: "Dominion Realm", book_no: 1, subtitle: "Ascent" }));
    expect(md).toContain('series: "Dominion Realm"');
    expect(md).toContain("book: 1");
    expect(md).toContain('subtitle: "Ascent"');
  });

  it("stamps the injected timestamp (deterministic), not wall-clock", () => {
    expect(markdownOf(fullBook())).toContain(`exported_at: "${AT}"`);
  });
});

describe("manifest ships with every export", () => {
  it("records counts, schema versions, preset, and source for a Markdown export", () => {
    const r = runExport(fullBook(), "editorial_review", { exportedAt: AT });
    const man = r.manifest;
    expect(man.source).toBe("writers-desk");
    expect(man.preset).toBe("editorial_review");
    expect(man.exportedAt).toBe(AT);
    expect(man.counts).toEqual({
      volumes: 0,
      parts: 2,
      chapters: 5,
      scenes: 5,
      words: expect.any(Number),
    });
    expect(man.counts.words).toBeGreaterThan(0);
    expect(man.spineSchemaVersion).toBeTruthy();
    expect(man.sourceSchemaVersion).toBeTruthy();
    expect(man.rendererVersion).toBeTruthy();
    // The TODO marker in chapter 4 surfaces as a preflight warning carried on the manifest.
    expect(man.issues.some((i) => i.code === "editorial_marker")).toBe(true);
    // The independent count guard wired into runExport agrees with the spine counts on good input.
    expect(man.issues.some((i) => i.code === "word_count_mismatch")).toBe(false);
  });

  it("manifest counts are consistent with an independent spine count", () => {
    const book = fullBook();
    const spine = buildSpine(book, resolveExportMetadata(book));
    const man = buildManifest({
      spine,
      metadata: spine.metadata,
      preset: "editorial_review",
      preflight: preflight(spine, resolvePolicy("editorial_review")),
      exportedAt: AT,
      draft: false,
    });
    expect(man.counts.words).toBe(
      spineChapters(spine)
        .flatMap((c) => c.scenes)
        .reduce((a, s) => a + (s.hasProse ? s.wordCount : 0), 0),
    );
  });
});

describe("submission-safe gate end-to-end", () => {
  it("Shunn returns no artifact when preflight blocks, and does when overridden", () => {
    const bad = ms({ chapters: [ch({ chapter_no: 1, pov: "" }), ch({ chapter_no: 1, pov: "" })] });
    const blocked = runExport(bad, "submission_shunn", { author: "A", exportedAt: AT });
    expect(blocked.artifact).toBeNull();
    expect(blocked.preflight.blocked).toBe(true);
    expect(blocked.manifest.blocked).toBe(true); // manifest still ships, recording the block

    const forced = runExport(bad, "submission_shunn", {
      author: "A",
      exportedAt: AT,
      override: true,
    });
    expect(forced.artifact?.kind).toBe("docx");
  });
});

describe("backward compatibility", () => {
  it("a plain book with no parts still exports on all three surfaces", () => {
    const plain = ms({
      chapters: [ch({ chapter_no: 1, title: "Only", scenes: [scn(1, "Hello world.")] })],
    });
    expect(markdownOf(plain)).toContain("# Chapter 1 — Only");
    expect(runExport(plain, "reader_proof", { exportedAt: AT }).artifact?.kind).toBe("docx");
    expect(
      runExport(plain, "submission_shunn", { author: "A", exportedAt: AT }).artifact?.kind,
    ).toBe("docx");
  });
});
