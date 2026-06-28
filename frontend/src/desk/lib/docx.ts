// DOCX emitter — "many emitters, one parse". Consumes parseBlocks/parseInline AST.
// Domain A: manuscript book typography + LitRPG interface panels.
// Domain B: canon docs with professional tables/callouts.
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
import {
  formatInterfaceHeader,
  formatInterfaceShunnHeader,
  neutralSurface,
  PALETTE,
  resolveSurface,
  tableSurface,
  type Surface,
} from "./litrpgSurfaces";
import { parseBlocks, parseInline, type ProseBlock, type Tone } from "../prose";

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

function inlineRuns(
  text: string,
  base: { font?: string; size?: number; color?: string } = {},
): Run[] {
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
        return new TextRun({ ...base, text: tok.s, color: base.color });
    }
  });
}

/** Layout-only panel — callers supply pre-coloured Paragraph rows. */
function panel(rows: TableRow[], surface: Surface): Table {
  const accent = line(surface.accent, surface.leftBorderSize);
  const outer = line(surface.border, 4);
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: outer,
      bottom: outer,
      right: outer,
      left: accent,
      insideHorizontal: NO_BORDER,
      insideVertical: NO_BORDER,
    },
    rows,
  });
}

function singleCellPanel(children: Paragraph[], surface: Surface): Table {
  return panel(
    [
      new TableRow({
        children: [
          new TableCell({
            shading: { type: ShadingType.CLEAR, fill: surface.fill, color: "auto" },
            margins: cellMargins,
            children,
          }),
        ],
      }),
    ],
    surface,
  );
}

function calloutPanel(b: Extract<ProseBlock, { kind: "callout" }>): Table {
  const accent = TONE_COLOR[b.tone];
  const surface = neutralSurface();
  const children: Paragraph[] = [];
  if (b.title) {
    children.push(
      new Paragraph({
        spacing: { after: 60 },
        children: [
          new TextRun({ text: b.title.toUpperCase(), bold: true, color: accent, size: 18 }),
        ],
      }),
    );
  }
  for (const ln of b.lines) {
    if (ln.trim()) {
      children.push(
        new Paragraph({
          spacing: { after: 40 },
          children: inlineRuns(ln, { color: surface.text }),
        }),
      );
    }
  }
  if (children.length === 0) children.push(new Paragraph(""));
  return panel(
    [
      new TableRow({
        children: [
          new TableCell({
            shading: { type: ShadingType.CLEAR, fill: surface.fill, color: "auto" },
            margins: cellMargins,
            children,
          }),
        ],
      }),
    ],
    { ...surface, accent },
  );
}

function monoPanel(lines: string[], surface: Surface): Table {
  const rows = lines.length ? lines : [""];
  return singleCellPanel(
    rows.map(
      (l) =>
        new Paragraph({
          spacing: { after: 0, line: 240, lineRule: "auto" },
          children: [
            new TextRun({ text: l || " ", font: "Consolas", size: 18, color: surface.text }),
          ],
        }),
    ),
    surface,
  );
}

function interfacePanel(b: Extract<ProseBlock, { kind: "interface" }>): Table {
  const surface = resolveSurface(b.spec);
  const headerRow = new TableRow({
    children: [
      new TableCell({
        shading: { type: ShadingType.CLEAR, fill: surface.headerFill, color: "auto" },
        margins: cellMargins,
        children: [
          new Paragraph({
            spacing: { after: 0 },
            children: [
              new TextRun({
                text: formatInterfaceHeader(b.spec),
                bold: true,
                color: surface.headerText,
                size: 18,
              }),
            ],
          }),
        ],
      }),
    ],
  });

  const bodyParagraphs: Paragraph[] = b.lines.map((ln) =>
    ln.trim()
      ? new Paragraph({
          spacing: { after: 40, line: 240, lineRule: "auto" },
          children: [new TextRun({ text: ln, font: "Consolas", size: 18, color: surface.text })],
        })
      : new Paragraph({ spacing: { after: 40 }, children: [new TextRun("")] }),
  );
  if (bodyParagraphs.length === 0) bodyParagraphs.push(new Paragraph(""));

  const bodyRow = new TableRow({
    children: [
      new TableCell({
        shading: { type: ShadingType.CLEAR, fill: surface.fill, color: "auto" },
        margins: cellMargins,
        children: bodyParagraphs,
      }),
    ],
  });

  return panel([headerRow, bodyRow], surface);
}

export { formatInterfaceShunnHeader };

function dataTable(b: Extract<ProseBlock, { kind: "table" }>): Table {
  const surface = tableSurface();
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
          shading: { type: ShadingType.CLEAR, fill: surface.headerFill, color: "auto" },
          margins: cellMargins,
          children: [
            new Paragraph({
              alignment: align(i),
              children: [new TextRun({ text: h, bold: true, color: surface.headerText })],
            }),
          ],
        }),
    ),
  });

  const body = b.rows.map(
    (r, ri) =>
      new TableRow({
        children: r.map((c, i) => {
          const fill = ri % 2 === 0 ? PALETTE.paper : PALETTE.pale;
          const borders =
            i === 0
              ? {
                  top: line(surface.border),
                  bottom: line(surface.border),
                  left: line(surface.accent, 12),
                  right: line(surface.border),
                }
              : {
                  top: line(surface.border),
                  bottom: line(surface.border),
                  left: line(surface.border),
                  right: line(surface.border),
                };
          return new TableCell({
            shading: { type: ShadingType.CLEAR, fill, color: "auto" },
            margins: cellMargins,
            borders,
            children: [
              new Paragraph({
                alignment: align(i),
                children: inlineRuns(c, { color: surface.text }),
              }),
            ],
          });
        }),
      }),
  );

  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: line(surface.border),
      bottom: line(surface.border),
      left: line(surface.border),
      right: line(surface.border),
      insideHorizontal: line(surface.border),
      insideVertical: line(surface.border),
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

function renderBlocks(blocks: ProseBlock[], book: boolean): (Paragraph | Table)[] {
  const out: (Paragraph | Table)[] = [];
  const pushTable = (t: Table) => {
    out.push(t);
    out.push(new Paragraph(""));
  };
  const neutral = neutralSurface();

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
        pushTable(monoPanel(b.lines, { ...neutral, fill: "F5F5F5" }));
        break;
      case "stat":
        pushTable(monoPanel(b.lines, { ...neutral, fill: "FAFAFA" }));
        break;
      case "interface":
        pushTable(interfacePanel(b));
        break;
      case "hr":
        out.push(
          new Paragraph({
            spacing: { before: 120, after: 120 },
            border: {
              bottom: { style: BorderStyle.SINGLE, size: 6, color: PALETTE.border, space: 1 },
            },
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

const SHUNN_FONT = "Courier New";
const SHUNN_SIZE = 24;
const DOUBLE = { line: 480 };

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

function shunnBody(text: string): Paragraph {
  return new Paragraph({
    spacing: DOUBLE,
    indent: { firstLine: convertInchesToTwip(0.5) },
    children: [shunnRun(text)],
  });
}

function shunnCenter(text: string, extra: object = {}): Paragraph {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: DOUBLE,
    ...extra,
    children: [shunnRun(text)],
  });
}

function shunnPlainBlocks(blocks: ProseBlock[]): Paragraph[] {
  const out: Paragraph[] = [];
  for (const b of blocks) {
    switch (b.kind) {
      case "p":
        out.push(shunnBody(b.text.replace(/\s+/g, " ").trim()));
        break;
      case "interface": {
        out.push(shunnCenter(formatInterfaceShunnHeader(b.spec), { indent: undefined }));
        out.push(new Paragraph({ spacing: DOUBLE, children: [shunnRun("")] }));
        for (const ln of b.lines) {
          if (ln.trim()) out.push(shunnBody(ln));
          else out.push(new Paragraph({ spacing: DOUBLE, children: [shunnRun("")] }));
        }
        break;
      }
      case "table": {
        for (const row of [b.head, ...b.rows]) {
          out.push(shunnCenter(`| ${row.join(" | ")} |`, { indent: undefined }));
        }
        break;
      }
      case "code":
      case "stat":
        for (const ln of b.lines) {
          out.push(shunnCenter(ln || " ", { indent: undefined }));
        }
        break;
      case "callout":
        if (b.title) out.push(shunnBody(`[${b.title}]`));
        for (const ln of b.lines) {
          if (ln.trim()) out.push(shunnBody(ln.replace(/\s+/g, " ").trim()));
        }
        break;
      case "heading":
        out.push(
          shunnCenter(b.text.toUpperCase(), {
            indent: undefined,
            spacing: { before: 120, ...DOUBLE },
          }),
        );
        break;
      case "ul":
        for (const it of b.items) out.push(shunnBody(`- ${it.replace(/\s+/g, " ").trim()}`));
        break;
      case "ol":
        b.items.forEach((it, i) =>
          out.push(shunnBody(`${i + 1}. ${it.replace(/\s+/g, " ").trim()}`)),
        );
        break;
      case "hr":
        out.push(shunnCenter("#", { indent: undefined }));
        break;
    }
  }
  return out;
}

export function buildShunnDoc(
  manuscript: ManuscriptOut,
  author: string,
  wordCount: number,
): Document {
  const title = manuscript.title || "Untitled";
  const byline = author.trim() || "Author";
  const surname = byline.split(/\s+/).pop() || byline;
  const titleUpper = title.toUpperCase();
  const rightTab = convertInchesToTwip(6.5);

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
      for (const para of shunnPlainBlocks(parseBlocks(sc.prose ?? ""))) children.push(para);
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

export function docxFilename(title: string): string {
  return (title.replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "document") + ".docx";
}

export function markdownFilename(title: string): string {
  return (title.replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "manuscript") + ".md";
}

function yamlQuote(s: string): string {
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function chapterComment(ch: ManuscriptOut["chapters"][number]): string {
  const title = ch.title ? ` title=${yamlQuote(ch.title)}` : "";
  return `<!-- chapter number=${ch.chapter_no}${title} pov=${yamlQuote(ch.pov)} -->`;
}

/** Semantic Markdown export — dominion-manuscript/v1 front matter, prose preserved verbatim. */
export function buildManuscriptMarkdown(
  manuscript: ManuscriptOut,
  opts: { draft?: boolean } = {},
): string {
  const draft = opts.draft ?? false;
  const title = manuscript.title || "Untitled";
  const lines: string[] = [
    "---",
    "schema: dominion-manuscript/v1",
    `title: ${yamlQuote(title)}`,
    'series: "Dominion Realm"',
    "book: 1",
    `exported_at: ${yamlQuote(new Date().toISOString())}`,
    "source: writers-desk",
    "format: semantic-markdown",
    "interface_style: professional",
    "litrpg_ui: true",
    `draft: ${draft}`,
    "---",
    "",
    `# ${title}`,
    "",
  ];

  for (const ch of manuscript.chapters) {
    const scenes = ch.scenes.filter((s) => (s.prose ?? "").trim());
    if (scenes.length === 0) continue;
    lines.push(
      `# Chapter ${ch.chapter_no}${ch.title ? ` — ${ch.title}` : ""}`,
      chapterComment(ch),
      "",
    );
    scenes.forEach((sc, si) => {
      lines.push(`<!-- scene index=${si + 1} scene_no=${sc.scene_no} -->`, "");
      lines.push(sc.prose ?? "");
      lines.push("");
    });
  }

  return lines.join("\n");
}

export function saveMarkdown(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function saveDocx(doc: Document, filename: string): Promise<void> {
  const blob = await Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
