// DOCX XML smoke tests — a LIGHT check that the two DOCX emitters render the right structural labels,
// NOT a brittle byte-for-byte comparison. We pack the Document, unzip `word/document.xml`, and assert the
// resolved labels (PROLOGUE / EPILOGUE / PART I / CHAPTER N) are present and — the core regression guard —
// that a prologue/epilogue never comes back out as a numbered chapter.

import { Packer } from "docx";
import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import type { ManuscriptChapter, ManuscriptOut, ManuscriptScene } from "../api/types";
import { renderReaderDoc, renderShunnDoc } from "../lib/docx";
import { resolveExportMetadata } from "./metadata";
import { resolvePolicy } from "./presets";
import { buildSpine, type ManuscriptSpine } from "./spine";

const scn = (scene_no: number, prose: string): ManuscriptScene => ({ scene_no, prose });
const ch = (over: Partial<ManuscriptChapter> & { chapter_no: number }): ManuscriptChapter => ({
  title: null,
  pov: "Marcus",
  kind: "chapter",
  epigraph: null,
  part_id: null,
  scenes: [scn(1, "Prose.")],
  ...over,
});

// prologue (ch1) → Volume I [ Part I {2,3} ] → Act II {4} → epilogue (ch5).
const book: ManuscriptOut = {
  book_id: "b",
  title: "Realmwalkers",
  series: null,
  book_no: null,
  subtitle: null,
  volumes: [{ id: "v1", volume_no: 1, title: "The Long Winter", subtitle: null }],
  parts: [
    { id: "p1", volume_id: "v1", part_no: 1, title: "The Scrim", subtitle: null, kind: "part" },
    { id: "p2", volume_id: null, part_no: 2, title: "The Reserve", subtitle: null, kind: "act" },
  ],
  chapters: [
    ch({ chapter_no: 1, kind: "prologue", pov: "Narrator" }),
    ch({ chapter_no: 2, part_id: "p1", title: "The Scrim" }),
    ch({ chapter_no: 3, part_id: "p1" }),
    ch({ chapter_no: 4, part_id: "p2" }),
    ch({ chapter_no: 5, kind: "epilogue" }),
  ],
};

const spine: ManuscriptSpine = buildSpine(book, resolveExportMetadata(book));

async function documentXml(doc: Parameters<typeof Packer.toBuffer>[0]): Promise<string> {
  const buf = await Packer.toBuffer(doc);
  const zip = await JSZip.loadAsync(buf);
  const entry = zip.file("word/document.xml");
  expect(entry, "word/document.xml should exist in the DOCX package").toBeTruthy();
  return entry!.async("string");
}

describe("Reader DOCX document.xml", () => {
  it("renders volume/part/act + kind labels and never numbers a prologue/epilogue", async () => {
    const xml = await documentXml(renderReaderDoc(spine, resolvePolicy("reader_proof")));
    for (const label of [
      "VOLUME I",
      "PART I",
      "ACT II",
      "PROLOGUE",
      "EPILOGUE",
      "CHAPTER 2",
      "CHAPTER 4",
    ]) {
      expect(xml).toContain(label);
    }
    // The regression this whole foundation exists to prevent: ch1 (prologue) and ch5 (epilogue) must
    // NOT surface as numbered chapters.
    expect(xml).not.toContain("CHAPTER 1");
    expect(xml).not.toContain("CHAPTER 5");
    // Named styles (not inline formatting): the structural paragraphs + body reference the stylesheet
    // by id (single-paragraph scenes exercise the first-paragraph body style).
    for (const styleRef of ['w:val="BookTitle"', 'w:val="ChapterLabel"', 'w:val="BodyFirst"']) {
      expect(xml).toContain(styleRef);
    }
  });
});

describe("Shunn DOCX document.xml", () => {
  it("renders volume/part/act + kind labels and never numbers a prologue/epilogue", async () => {
    const xml = await documentXml(renderShunnDoc(spine, resolvePolicy("submission_shunn")));
    for (const label of ["VOLUME I", "PART I", "ACT II", "PROLOGUE", "EPILOGUE", "CHAPTER 2"]) {
      expect(xml).toContain(label);
    }
    expect(xml).not.toContain("CHAPTER 1");
    expect(xml).not.toContain("CHAPTER 5");
  });
});
