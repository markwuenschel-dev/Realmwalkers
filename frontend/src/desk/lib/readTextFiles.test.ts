import { describe, expect, it } from "vitest";
import {
  LABEL_MAX,
  labelFor,
  readFilesAsText,
  readTextFiles,
  stripFrontMatter,
} from "./readTextFiles";

// jsdom's File does not reliably implement Blob.text(); pin the content the way the uploader test does.
const fileWith = (name: string, text: string): File => {
  const f = new File(["ignored"], name);
  Object.defineProperty(f, "text", { value: () => Promise.resolve(text) });
  return f;
};

describe("readTextFiles", () => {
  it("accepts .md and .txt by extension and names every rejected file", async () => {
    const out = await readTextFiles([
      fileWith("one.md", "Alpha."),
      fileWith("notes.docx", "binary"),
      fileWith("TWO.TXT", "Beta."),
      fileWith("cover.png", "binary"),
    ]);
    expect(out.accepted.map((f) => f.filename)).toEqual(["one.md", "TWO.TXT"]);
    expect(out.rejected).toEqual(["notes.docx", "cover.png"]);
  });

  it("strips a UTF-8 BOM and a leading YAML front-matter block", async () => {
    const text = "﻿---\ntitle: Draft\nstatus: wip\n---\n# The Lantern Room\n\nThe door stuck.";
    const [f] = (await readTextFiles([fileWith("ch1.md", text)])).accepted;
    expect(f.text).toBe("# The Lantern Room\n\nThe door stuck.");
    expect(f.label).toBe("The Lantern Room");
  });

  it("labels from the first heading at any level, else the filename stem", async () => {
    const out = await readTextFiles([
      fileWith("a.md", "Some opening line.\n\n### Second Bell ###\n\n# Later Heading"),
      fileWith("chapter-04.draft.txt", "No heading here, just prose."),
    ]);
    expect(out.accepted[0].label).toBe("Second Bell");
    expect(out.accepted[1].label).toBe("chapter-04.draft");
  });

  it("caps a long heading at the label limit", () => {
    expect(labelFor("x.md", `# ${"w".repeat(500)}`)).toHaveLength(LABEL_MAX);
  });
});

describe("stripFrontMatter", () => {
  it("keeps a chapter that merely opens with a horizontal-rule scene break", () => {
    const text = "---\nThe tide came in.\n\n---\nMorning.";
    expect(stripFrontMatter(text)).toBe(text);
  });

  it("strips an empty front-matter block", () => {
    expect(stripFrontMatter("---\n---\nBody.")).toBe("Body.");
  });

  it("leaves text without front matter untouched, CRLF included", () => {
    expect(stripFrontMatter("Line one.\r\nLine two.")).toBe("Line one.\r\nLine two.");
  });
});

describe("readFilesAsText (the manuscript uploader's reader)", () => {
  it("reads every file, unfiltered and unmodified", async () => {
    const out = await readFilesAsText([
      fileWith("draft.md", "﻿---\ntitle: x\n---\nBody"),
      fileWith("notes.docx", "anything"),
    ]);
    expect(out).toEqual([
      { filename: "draft.md", text: "﻿---\ntitle: x\n---\nBody" },
      { filename: "notes.docx", text: "anything" },
    ]);
  });
});
