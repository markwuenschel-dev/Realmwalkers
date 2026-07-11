import { describe, expect, it } from "vitest";
import { planReaderProduction, type ReaderFrontItem } from "./readerFrontMatter";
import { resolvePolicy } from "./presets";
import { SPINE_SCHEMA_VERSION, type ManuscriptSpine, type SpineChapterNode } from "./spine";

const scene = (prose: string) => ({
  sceneNo: 1,
  proseRaw: prose,
  proseRender: prose,
  blocks: [{ kind: "p" as const, text: prose, n: 0 }],
  wordCount: prose.split(/\s+/).length,
  hasProse: prose.trim().length > 0,
  issues: [],
});

const chapter = (over: Partial<SpineChapterNode>): SpineChapterNode => ({
  type: "chapter",
  position: 0,
  chapterNo: null,
  kind: "chapter",
  kindRecognized: true,
  sectionType: null,
  title: null,
  pov: "Marcus",
  epigraph: null,
  label: "Chapter",
  partId: null,
  scenes: [scene("body")],
  ...over,
});

// A spine already sorted by position (as the backend returns): front matter → prologue → chapters →
// epilogue → back matter.
const spineOf = (chapters: SpineChapterNode[]): ManuscriptSpine => ({
  schemaVersion: SPINE_SCHEMA_VERSION,
  metadata: { title: "The Book" },
  nodes: chapters,
});

const READER = resolvePolicy("reader_proof");
const kinds = (items: ReaderFrontItem[]) => items.map((i) => i.type);

describe("planReaderProduction", () => {
  it("orders half-title → title page → authored front matter → CONTENTS in canonical sequence", () => {
    const spine = spineOf([
      chapter({ kind: "front_matter", sectionType: "copyright", label: "Copyright" }),
      chapter({ kind: "front_matter", sectionType: "dedication", label: "Dedication" }),
      chapter({ kind: "front_matter", sectionType: "preface", label: "Preface" }),
      chapter({ kind: "prologue", label: "Prologue" }),
      chapter({ kind: "chapter", chapterNo: 1, label: "Chapter 1" }),
      chapter({ kind: "epilogue", label: "Epilogue" }),
    ]);
    const plan = planReaderProduction(spine, READER);
    // Copyright & Dedication precede the Table of Contents; Preface follows it (publishing convention).
    expect(kinds(plan.front)).toEqual([
      "half_title",
      "title_page",
      "section", // copyright
      "section", // dedication
      "toc",
      "section", // preface
    ]);
    // Front-matter chapters are pulled OUT of the body; the body keeps prologue/chapter/epilogue.
    expect(plan.body.map((n) => (n.type === "chapter" ? n.kind : n.type))).toEqual([
      "prologue",
      "chapter",
      "epilogue",
    ]);
  });

  it("builds the Contents from every non-front-matter section that has prose, in reading order", () => {
    const spine = spineOf([
      chapter({ kind: "front_matter", sectionType: "copyright", label: "Copyright" }),
      chapter({ kind: "prologue", label: "Prologue" }),
      chapter({ kind: "chapter", chapterNo: 1, label: "Chapter 1" }),
      chapter({ kind: "epilogue", label: "Epilogue" }),
      chapter({ kind: "back_matter", sectionType: "glossary", label: "Glossary" }),
    ]);
    const toc = planReaderProduction(spine, READER).front.find((i) => i.type === "toc");
    expect(toc?.type === "toc" && toc.entries).toEqual([
      "Prologue",
      "Chapter 1",
      "Epilogue",
      "Glossary",
    ]);
  });

  it("omits an empty section from the Contents (no prose = not listed)", () => {
    const spine = spineOf([
      chapter({ kind: "chapter", chapterNo: 1, label: "Chapter 1" }),
      chapter({
        kind: "back_matter",
        sectionType: "glossary",
        label: "Glossary",
        scenes: [scene("")],
      }),
    ]);
    const toc = planReaderProduction(spine, READER).front.find((i) => i.type === "toc");
    expect(toc?.type === "toc" && toc.entries).toEqual(["Chapter 1"]);
  });

  it("omits half-title and Contents when the policy disables them (Shunn)", () => {
    const spine = spineOf([chapter({ kind: "chapter", chapterNo: 1, label: "Chapter 1" })]);
    const plan = planReaderProduction(spine, resolvePolicy("submission_shunn"));
    expect(kinds(plan.front)).toEqual(["title_page"]); // no half-title, no TOC
  });
});
