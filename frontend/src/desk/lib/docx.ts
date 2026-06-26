// DOCX emitter (Phase 4) — the "many emitters, one parse" DOCX side. Consumes the same
// parseBlocks/parseInline AST the on-screen renderer uses, so a Word export matches what you read.
// Two domains: the manuscript (Domain A — book typography) and canon docs (Domain B — MarketMind
// styling: navy-header tables, accent callout boxes, code blocks). Runs client-side via docx-js;
// lazy-imported from the export buttons so it stays out of the main bundle.
import {
  AlignmentType,
  BorderStyle,
  Document,
  ExternalHyperlink,
  Footer,
  Header,
  HeadingLevel,
  Packer,
  PageBreak,
  PageNumber,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableRow,
  TabStopType,
  TextRun,
  WidthType,
  convertInchesToTwip,
  type IBorderOptions,
} from "docx";
import type { ManuscriptOut } from "../api/types";
import { parseBlocks, parseInline, type ProseBlock, type Tone } from "../prose";

// Print colours (theme-independent — a Word doc isn't themed). Navy header is the MarketMind table
// look; callout tones map to fixed ink colours.
const NAVY = "1F3864";
const TONE_COLOR: Record<Tone, string> = {
  note: "1F3864",
  info: "2E5AAC",
  good: "2F7D57",
  warn: "9A6A1F",
  bad: "A23A52",
};

const HEADINGS = [
  HeadingLevel.HEADING_1,
  HeadingLevel.HEADING_2,
  HeadingLevel.HEADING_3,
  HeadingLevel.HEADING_4,
  HeadingLevel.HEADING_5,
  HeadingLevel.HEADING_6,
];

const NO_BORDER: IBorderOptions = { style: BorderStyle.NONE, size: 0, color: "auto" };
const cellMargins = { top: 60, bottom: 60, left: 110, right: 110 };

function line(color: string, size = 4): IBorderOptions {
  return { style: BorderStyle.SINGLE, size, color };
}

type Run = TextRun | ExternalHyperlink;

// One line of inline markdown -> docx runs. `base` carries prose font/size; code/link override it.
function inlineRuns(text: string, base: { font?: string; size?: number } = {}): Run[] {
  return parseInline(text).map((tok): Run => {
    switch (tok.t) {
      case "code":
        return new TextRun({ ...base, text: tok.s, font: "Consolas" });
      case "strong":
        return new TextRun({ ...base, text: tok.s, bold: true });
      case "em":
        return new TextRun({ ...base, text: tok.s, italics: true });
      case "link":
        return new ExternalHyperlink({
          link: tok.href,
          children: [new TextRun({ ...base, text: tok.s, color: "0563C1", underline: {} })],
        });
      default:
        return new TextRun({ ...base, text: tok.s });
    }
  });
}

// A single-cell table — the MarketMind shape for callouts and code/stat blocks (fill + optional
// accent edge). docx renders box-art and code best in a shaded cell with a monospace font.
function panel(children: Paragraph[], fill: string, leftAccent?: string): Table {
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: NO_BORDER,
      bottom: NO_BORDER,
      right: NO_BORDER,
      insideHorizontal: NO_BORDER,
      insideVertical: NO_BORDER,
      left: leftAccent ? line(leftAccent, 24) : NO_BORDER,
    },
    rows: [
      new TableRow({
        children: [
          new TableCell({
            shading: { type: ShadingType.CLEAR, fill, color: "auto" },
            margins: cellMargins,
            children,
          }),
        ],
      }),
    ],
  });
}

function calloutPanel(b: Extract<ProseBlock, { kind: "callout" }>): Table {
  const color = TONE_COLOR[b.tone];
  const children: Paragraph[] = [];
  if (b.title) {
    children.push(
      new Paragraph({
        spacing: { after: 60 },
        children: [new TextRun({ text: b.title.toUpperCase(), bold: true, color, size: 18 })],
      }),
    );
  }
  for (const ln of b.lines) {
    if (ln.trim())
      children.push(new Paragraph({ spacing: { after: 40 }, children: inlineRuns(ln) }));
  }
  if (children.length === 0) children.push(new Paragraph(""));
  return panel(children, "F3F4F6", color);
}

function monoPanel(lines: string[], fill: string): Table {
  const rows = lines.length ? lines : [""];
  return panel(
    rows.map(
      (l) =>
        new Paragraph({
          spacing: { after: 0, line: 240, lineRule: "auto" },
          children: [new TextRun({ text: l || " ", font: "Consolas", size: 18 })],
        }),
    ),
    fill,
  );
}

function dataTable(b: Extract<ProseBlock, { kind: "table" }>): Table {
  const align = (i: number) =>
    b.align[i] === "center"
      ? AlignmentType.CENTER
      : b.align[i] === "right"
        ? AlignmentType.RIGHT
        : AlignmentType.LEFT;
  const header = new TableRow({
    tableHeader: true,
    children: b.head.map(
      (h, i) =>
        new TableCell({
          shading: { type: ShadingType.CLEAR, fill: NAVY, color: "auto" },
          margins: cellMargins,
          children: [
            new Paragraph({
              alignment: align(i),
              children: [new TextRun({ text: h, bold: true, color: "FFFFFF" })],
            }),
          ],
        }),
    ),
  });
  const body = b.rows.map(
    (r) =>
      new TableRow({
        children: r.map(
          (c, i) =>
            new TableCell({
              margins: cellMargins,
              children: [new Paragraph({ alignment: align(i), children: inlineRuns(c) })],
            }),
        ),
      }),
  );
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: line("CCCCCC"),
      bottom: line("CCCCCC"),
      left: line("CCCCCC"),
      right: line("CCCCCC"),
      insideHorizontal: line("CCCCCC"),
      insideVertical: line("CCCCCC"),
    },
    rows: [header, ...body],
  });
}

function paraFor(text: string, book: boolean): Paragraph {
  return book
    ? new Paragraph({
        alignment: AlignmentType.JUSTIFIED,
        spacing: { line: 320, lineRule: "auto" },
        children: inlineRuns(text, { font: "Georgia", size: 24 }),
      })
    : new Paragraph({
        spacing: { after: 120, line: 276, lineRule: "auto" },
        children: inlineRuns(text),
      });
}

// AST -> docx body. `book` switches paragraphs to justified serif book prose (the manuscript) vs.
// left-aligned doc prose (canon). Tables are followed by an empty paragraph so adjacent tables don't
// merge and Word is happy ending a section after one.
function renderBlocks(blocks: ProseBlock[], book: boolean): (Paragraph | Table)[] {
  const out: (Paragraph | Table)[] = [];
  const pushTable = (t: Table) => {
    out.push(t);
    out.push(new Paragraph(""));
  };
  for (const b of blocks) {
    switch (b.kind) {
      case "heading":
        out.push(new Paragraph({ heading: HEADINGS[b.level - 1], children: inlineRuns(b.text) }));
        break;
      case "ul":
        for (const it of b.items)
          out.push(new Paragraph({ bullet: { level: 0 }, children: inlineRuns(it) }));
        break;
      case "ol":
        b.items.forEach((it, i) =>
          out.push(new Paragraph({ children: [new TextRun(`${i + 1}. `), ...inlineRuns(it)] })),
        );
        break;
      case "callout":
        pushTable(calloutPanel(b));
        break;
      case "table":
        pushTable(dataTable(b));
        break;
      case "code":
        pushTable(monoPanel(b.lines, "F5F5F5"));
        break;
      case "stat":
        pushTable(monoPanel(b.lines, "FAFAFA"));
        break;
      case "hr":
        out.push(
          new Paragraph({
            spacing: { before: 120, after: 120 },
            border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "CCCCCC", space: 1 } },
          }),
        );
        break;
      default:
        out.push(paraFor(b.text, book));
        break;
    }
  }
  return out;
}

function pageFooter(): Footer {
  return new Footer({
    children: [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ children: [PageNumber.CURRENT], color: "808080", size: 18 })],
      }),
    ],
  });
}

const inch = convertInchesToTwip(1);
const pageMargin = { top: inch, bottom: inch, left: inch, right: inch };

/** Domain B — a canon/planning/style doc as a Word file (MarketMind styling, page-numbered). */
export function buildDocDoc(title: string, content: string): Document {
  return new Document({
    creator: "Writers' Desk",
    title,
    sections: [
      {
        properties: { page: { margin: pageMargin } },
        footers: { default: pageFooter() },
        children: renderBlocks(parseBlocks(content), false),
      },
    ],
  });
}

/** Domain A — the manuscript as a Word file: title page, chapters on fresh pages, book prose.
 *  `subtitle` defaults to the approved-manuscript line; pass a draft-compile line for an unapproved export. */
export function buildManuscriptDoc(
  manuscript: ManuscriptOut,
  subtitle = "the approved manuscript, in reading order",
): Document {
  const title = manuscript.title || "Untitled";
  const children: (Paragraph | Table)[] = [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 2400, after: 240 },
      children: [new TextRun({ text: "BOOK ONE", font: "Georgia", size: 20, color: "808080" })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
      children: [new TextRun({ text: title, font: "Georgia", bold: true, size: 56 })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({
          text: subtitle,
          font: "Georgia",
          italics: true,
          size: 24,
          color: "808080",
        }),
      ],
    }),
  ];

  for (const ch of manuscript.chapters) {
    const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
    if (scenes.length === 0) continue;
    children.push(new Paragraph({ children: [new PageBreak()] }));
    children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 480, after: ch.title ? 60 : 80 },
        children: [
          new TextRun({ text: `CHAPTER ${ch.chapter_no}`, font: "Georgia", bold: true, size: 28 }),
        ],
      }),
    );
    if (ch.title) {
      children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 80 },
          children: [new TextRun({ text: ch.title, font: "Georgia", italics: true, size: 26 })],
        }),
      );
    }
    children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 320 },
        children: [
          new TextRun({ text: `POV · ${ch.pov}`, font: "Georgia", size: 18, color: "808080" }),
        ],
      }),
    );
    scenes.forEach((sc, si) => {
      if (si > 0) {
        children.push(
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: { before: 160, after: 160 },
            children: [new TextRun({ text: "⁂", size: 24, color: "808080" })],
          }),
        );
      }
      for (const el of renderBlocks(parseBlocks(sc.prose ?? ""), true)) children.push(el);
    });
  }

  return new Document({
    creator: "Writers' Desk",
    title,
    sections: [
      {
        properties: { page: { margin: pageMargin } },
        footers: { default: pageFooter() },
        children,
      },
    ],
  });
}

// --- Shunn standard manuscript format (submission) -----------------------------------------------
// Monospaced (Courier New 12pt), double-spaced, 1" margins, 0.5" paragraph indent; a running header
// `Surname / TITLE / page#` on every page but the first; a title page with contact + word count and
// the title a third of the way down; scenes separated by a centered "#". This is the format agents
// expect — deliberately plain (no markdown styling), unlike the book-typography export above.
const SHUNN_FONT = "Courier New";
const SHUNN_SIZE = 24; // 12pt in half-points
const DOUBLE = { line: 480 }; // double line spacing (240 = single)

// Shunn rounds the cover word count: nearest 100 under 25k, nearest 1000 above.
function roundWords(n: number): number {
  return n < 25000 ? Math.round(n / 100) * 100 : Math.round(n / 1000) * 1000;
}

function shunnRun(text: string): TextRun {
  return new TextRun({ text, font: SHUNN_FONT, size: SHUNN_SIZE });
}

function shunnHeader(surname: string, titleUpper: string): Header {
  return new Header({
    children: [
      new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [
          shunnRun(`${surname} / ${titleUpper} / `),
          new TextRun({ children: [PageNumber.CURRENT], font: SHUNN_FONT, size: SHUNN_SIZE }),
        ],
      }),
    ],
  });
}

/** Domain A, submission variant — the manuscript in standard (Shunn) format for agents/editors. */
export function buildShunnDoc(
  manuscript: ManuscriptOut,
  author: string,
  wordCount: number,
): Document {
  const title = manuscript.title || "Untitled";
  const byline = author.trim() || "Author";
  const surname = byline.split(/\s+/).pop() || byline;
  const titleUpper = title.toUpperCase();
  const rightTab = convertInchesToTwip(6.5); // 8.5" page − 2×1" margins

  const body = (text: string) =>
    new Paragraph({
      spacing: DOUBLE,
      indent: { firstLine: convertInchesToTwip(0.5) },
      children: [shunnRun(text)],
    });

  // Title page: contact (left) + word count (right), then the title ~1/3 down, then the byline.
  const children: Paragraph[] = [
    new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: rightTab }],
      children: [
        shunnRun(byline),
        shunnRun(`\tabout ${roundWords(wordCount).toLocaleString()} words`),
      ],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 2800, ...DOUBLE },
      children: [shunnRun(titleUpper)],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: DOUBLE,
      children: [shunnRun(`by ${byline}`)],
    }),
  ];

  for (const ch of manuscript.chapters) {
    const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
    if (scenes.length === 0) continue;
    children.push(new Paragraph({ children: [new PageBreak()] }));
    children.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 1200, after: 240, ...DOUBLE },
        children: [
          shunnRun(`CHAPTER ${ch.chapter_no}${ch.title ? ` — ${ch.title.toUpperCase()}` : ""}`),
        ],
      }),
    );
    scenes.forEach((sc, si) => {
      if (si > 0) {
        children.push(
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: DOUBLE,
            children: [shunnRun("#")],
          }),
        );
      }
      // Plain prose: split on blank lines into paragraphs; collapse internal whitespace (reflow).
      for (const block of (sc.prose ?? "").split(/\n{2,}/)) {
        const text = block.replace(/\s+/g, " ").trim();
        if (text) children.push(body(text));
      }
    });
  }

  return new Document({
    creator: "Writers' Desk",
    title,
    sections: [
      {
        properties: { titlePage: true, page: { margin: pageMargin } },
        headers: { default: shunnHeader(surname, titleUpper), first: new Header({ children: [] }) },
        children,
      },
    ],
  });
}

/** Safe-ish filename stem from a title. */
export function docxFilename(title: string): string {
  return (title.replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "document") + ".docx";
}

/** Pack a Document to a .docx blob and download it client-side. */
export async function saveDocx(doc: Document, filename: string): Promise<void> {
  const blob = await Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
