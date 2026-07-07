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
import { wordCount } from "./format";
import { bookNumberLabel } from "../manuscript/metadata";
import { toRoman } from "../manuscript/labels";
import type { ExportPolicy } from "../manuscript/presets";
import {
  spineCounts,
  type ManuscriptSpine,
  type SpineChapterNode,
  type SpinePartNode,
} from "../manuscript/spine";

// The three export formats every prose-bearing screen offers (Manuscript, Inbox, Scene, Chapters,
// Packets): plain semantic Markdown, the styled Reader DOCX, and the plain-format Shunn DOCX.
export type ExportKind = "md" | "docx" | "shunn";

const TONE_COLOR: Record<Tone, string> = {
  note: "1F3864",
  info: "2E5AAC",
  good: "2F7D57",
  warn: "9A6A1F",
  bad: "A23A52",
};

// Day/date marker divider: the litRPG `time` surface accent for the label, a muted rule glyph flanking it.
const TIME_ACCENT = "B45309";
const TIME_RULE = "9C9C9C";

/** A centered rule with the day/date label sitting on it — `⸻  DAY 3  ⸻`. */
function timeMarkerPara(label: string): Paragraph {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 260, after: 260 },
    children: [
      new TextRun({ text: "⸻  ", color: TIME_RULE, size: 22 }),
      new TextRun({
        text: label,
        bold: true,
        allCaps: true,
        characterSpacing: 30,
        color: TIME_ACCENT,
        font: "Georgia",
        size: 20,
      }),
      new TextRun({ text: "  ⸻", color: TIME_RULE, size: 22 }),
    ],
  });
}

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

function paraFor(text: string, book: boolean, indentFirstLine = false): Paragraph {
  // Book paragraphs use a first-line indent as the paragraph cue (classic print novel), with NO extra
  // space between paragraphs. The FIRST paragraph of a scene / after a scene break is left un-indented
  // (print convention), signalled by indentFirstLine=false from renderBlocks.
  return book
    ? new Paragraph({
        alignment: AlignmentType.JUSTIFIED,
        spacing: { line: 320, lineRule: "auto" },
        ...(indentFirstLine ? { indent: { firstLine: convertInchesToTwip(0.3) } } : {}),
        children: inlineRuns(text, { font: "Georgia", size: 22 }),
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
  let seenBookPara = false; // first book paragraph of the scene stays un-indented (print convention)

  for (const b of blocks) {
    switch (b.kind) {
      case "heading":
        out.push(new Paragraph({ heading: HEADINGS[b.level - 1], children: inlineRuns(b.text) }));
        break;
      case "time":
        out.push(timeMarkerPara(b.label));
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
        out.push(paraFor(b.text, book, book && seenBookPara));
        seenBookPara = true;
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

// --- wrapping arbitrary scenes as a ManuscriptOut -----------------------------------------------
// Every screen that shows prose (Inbox's selected scenes, a single scene, a single chapter, a scene
// packet's drafted scene) wraps its own data as a one-off ManuscriptOut here and feeds it straight into
// the export pipeline (manuscript/pipeline.runExport) — so a Markdown / Reader-DOCX / Shunn-DOCX export
// looks identical (same fonts, structure, front matter, labels) no matter which screen triggered it.
// This is the only place that shape gets assembled; the pipeline + emitters never know whether they're
// rendering the whole book, one chapter, or one scene. A fragment has no Parts and no book metadata, so
// `parts` is empty, each chapter's `part_id` is null, and series/book_no/subtitle stay null (a fragment
// must NOT inherit book identity — the old code hard-coded "BOOK ONE" onto every fragment; it no longer).

export interface ManuscriptChapterInput {
  chapter_no: number;
  title?: string | null;
  pov: string;
  kind?: string | null; // ChapterKind; drives the heading label (Prologue/Interlude/… vs "Chapter N")
  epigraph?: string | null;
  scenes: { scene_no: number; prose?: string | null }[];
}

export function buildManuscriptFrom(
  title: string,
  chapters: ManuscriptChapterInput[],
): ManuscriptOut {
  return {
    book_id: "",
    title,
    series: null,
    book_no: null,
    subtitle: null,
    parts: [],
    chapters: [...chapters]
      .sort((a, b) => a.chapter_no - b.chapter_no)
      .map((c) => ({
        chapter_no: c.chapter_no,
        title: c.title ?? null,
        pov: c.pov,
        kind: c.kind ?? "chapter",
        epigraph: c.epigraph ?? null,
        part_id: null,
        scenes: [...c.scenes].sort((a, b) => a.scene_no - b.scene_no),
      })),
  };
}

/** Whether any scene in a manuscript-shaped document has prose — the gate the export buttons use before
 *  anything has been drafted/approved. (Convenience over the wire shape; the spine exposes the same via
 *  `spineHasProse`.) */
export function manuscriptHasProse(ms: ManuscriptOut): boolean {
  return ms.chapters.some((c) => c.scenes.some((s) => (s.prose ?? "").trim()));
}

/** Total word count across every scene. (Convenience over the wire shape; the manifest's authoritative
 *  count comes from `spineCounts`.) */
export function manuscriptWordCount(ms: ManuscriptOut): number {
  return ms.chapters.flatMap((c) => c.scenes).reduce((acc, s) => acc + wordCount(s.prose), 0);
}

/** Reader DOCX title page — series line + spelled-out book number come from ExportMetadata (never
 *  hard-coded); the render descriptor (approved/draft mode, or a fragment label) is passed by the
 *  pipeline. A fragment/standalone book with no series metadata simply omits those lines. */
function readerTitlePage(
  spine: ManuscriptSpine,
  policy: ExportPolicy,
  renderSubtitle?: string,
): Paragraph[] {
  const { metadata } = spine;
  const out: Paragraph[] = [];
  const bookLine = bookNumberLabel(metadata.bookNumber);
  if (policy.includeSeriesLine && (metadata.series || bookLine)) {
    const head = [metadata.series?.toUpperCase(), bookLine].filter(Boolean).join(" · ");
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 2400, after: 240 },
        children: [new TextRun({ text: head, font: "Georgia", size: 20, color: "808080" })],
      }),
    );
  }
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: out.length ? 0 : 2400, after: 200 },
      children: [new TextRun({ text: metadata.title, font: "Georgia", bold: true, size: 56 })],
    }),
  );
  if (policy.includeSubtitle && metadata.subtitle) {
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 120 },
        children: [
          new TextRun({ text: metadata.subtitle, font: "Georgia", italics: true, size: 26 }),
        ],
      }),
    );
  }
  if (renderSubtitle) {
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({
            text: renderSubtitle,
            font: "Georgia",
            italics: true,
            size: 24,
            color: "808080",
          }),
        ],
      }),
    );
  }
  return out;
}

/** A full-width Part divider page: "PART I" over the part title, optional subtitle. */
function readerPartDivider(part: SpinePartNode): (Paragraph | Table)[] {
  const out: (Paragraph | Table)[] = [new Paragraph({ children: [new PageBreak()] })];
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 2000, after: 200 },
      children: [
        new TextRun({
          text: `PART ${toRoman(part.partNo)}`,
          font: "Georgia",
          size: 24,
          color: "808080",
          characterSpacing: 40,
        }),
      ],
    }),
  );
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: part.subtitle ? 80 : 0 },
      children: [new TextRun({ text: part.title, font: "Georgia", bold: true, size: 40 })],
    }),
  );
  if (part.subtitle) {
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({
            text: part.subtitle,
            font: "Georgia",
            italics: true,
            size: 24,
            color: "808080",
          }),
        ],
      }),
    );
  }
  return out;
}

/** Render one chapter node into the reader doc: page break, resolved label, title, POV, epigraph, and
 *  its scenes (from the pre-parsed spine blocks — no re-parsing, no label re-derivation here). Returns
 *  nothing when the chapter has no prose (an empty chapter is skipped; preflight flags it). */
function readerChapter(ch: SpineChapterNode, policy: ExportPolicy): (Paragraph | Table)[] {
  const scenes = ch.scenes.filter((s) => s.hasProse);
  if (scenes.length === 0) return [];
  const out: (Paragraph | Table)[] = [new Paragraph({ children: [new PageBreak()] })];
  const epigraph = ch.epigraph?.trim();
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 480, after: ch.title ? 60 : 80 },
      children: [
        new TextRun({ text: ch.label.toUpperCase(), font: "Georgia", bold: true, size: 28 }),
      ],
    }),
  );
  if (ch.title) {
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 80 },
        children: [new TextRun({ text: ch.title, font: "Georgia", italics: true, size: 26 })],
      }),
    );
  }
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: epigraph ? 200 : 320 },
      children: [
        new TextRun({ text: `POV · ${ch.pov}`, font: "Georgia", size: 18, color: "808080" }),
      ],
    }),
  );
  if (epigraph) {
    out.push(
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 320 },
        children: [
          new TextRun({
            text: epigraph,
            font: "Georgia",
            italics: true,
            size: 22,
            color: "606060",
          }),
        ],
      }),
    );
  }
  scenes.forEach((sc, si) => {
    if (si > 0) {
      out.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 160, after: 160 },
          children: [
            new TextRun({ text: policy.sceneBreakGlyph || "⁂", size: 24, color: "808080" }),
          ],
        }),
      );
    }
    // Blocks are already parsed (from the beautified prose) in the spine — the emitter never re-parses.
    for (const el of renderBlocks(sc.blocks, true)) out.push(el);
  });
  return out;
}

/** Reader DOCX emitter — consumes the ManuscriptSpine. Renders Part dividers, then each part's chapters
 *  (or ungrouped chapters), each from the spine's resolved labels + pre-parsed blocks. */
export function renderReaderDoc(
  spine: ManuscriptSpine,
  policy: ExportPolicy,
  opts: { renderSubtitle?: string } = {},
): Document {
  const children: (Paragraph | Table)[] = [...readerTitlePage(spine, policy, opts.renderSubtitle)];
  for (const node of spine.nodes) {
    if (node.type === "part") {
      if (policy.renderParts) children.push(...readerPartDivider(node));
      for (const ch of node.chapters) children.push(...readerChapter(ch, policy));
    } else {
      children.push(...readerChapter(node, policy));
    }
  }

  return new Document({
    creator: "Writers' Desk",
    title: spine.metadata.title,
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
      case "time":
        // Shunn stays format-plain: a centered label, no rule glyph or colour.
        out.push(shunnCenter(b.label.toUpperCase(), { indent: undefined }));
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

/** One chapter, plain Shunn format. Uses the spine's resolved label (uppercased) — so a Prologue reads
 *  "PROLOGUE", never "CHAPTER N" (the bug this refactor kills) — and the pre-parsed, flattened blocks. */
function shunnChapter(ch: SpineChapterNode): Paragraph[] {
  const scenes = ch.scenes.filter((s) => s.hasProse);
  if (scenes.length === 0) return [];
  const out: Paragraph[] = [new Paragraph({ children: [new PageBreak()] })];
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 1200, after: 240, ...DOUBLE },
      children: [
        shunnRun(`${ch.label.toUpperCase()}${ch.title ? ` — ${ch.title.toUpperCase()}` : ""}`),
      ],
    }),
  );
  scenes.forEach((sc, si) => {
    if (si > 0) {
      out.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: DOUBLE,
          children: [shunnRun("#")],
        }),
      );
    }
    for (const para of shunnPlainBlocks(sc.blocks)) out.push(para);
  });
  return out;
}

/** A plain Part divider for Shunn — a centered "PART I — TITLE", no rich styling. */
function shunnPartDivider(part: SpinePartNode): Paragraph[] {
  return [
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 1200, after: 240, ...DOUBLE },
      children: [shunnRun(part.label.toUpperCase())],
    }),
  ];
}

/** Shunn DOCX emitter — consumes the ManuscriptSpine. Byline + word count come from ExportMetadata and
 *  the spine counts; rich LitRPG blocks are flattened by `shunnPlainBlocks` (submission-safe). */
export function renderShunnDoc(spine: ManuscriptSpine, policy: ExportPolicy): Document {
  const title = spine.metadata.title;
  const byline =
    (policy.includeAuthorByline ? spine.metadata.author : undefined)?.trim() || "Author";
  const surname = byline.split(/\s+/).pop() || byline;
  const titleUpper = title.toUpperCase();
  const rightTab = convertInchesToTwip(6.5);
  const words = spineCounts(spine).words;

  const children: Paragraph[] = [
    new Paragraph({
      tabStops: [{ type: TabStopType.RIGHT, position: rightTab }],
      children: [shunnRun(byline), shunnRun(`\tabout ${roundWords(words).toLocaleString()} words`)],
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

  for (const node of spine.nodes) {
    if (node.type === "part") {
      if (policy.renderParts) children.push(...shunnPartDivider(node));
      for (const ch of node.chapters) children.push(...shunnChapter(ch));
    } else {
      children.push(...shunnChapter(node));
    }
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

export function manifestFilename(title: string): string {
  return (title.replace(/[^\w]+/g, "_").replace(/^_+|_+$/g, "") || "manuscript") + ".manifest.json";
}

function yamlQuote(s: string): string {
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function chapterComment(ch: SpineChapterNode): string {
  const title = ch.title ? ` title=${yamlQuote(ch.title)}` : "";
  const kind = ch.kind !== "chapter" ? ` kind=${ch.kind}` : "";
  return `<!-- chapter number=${ch.chapterNo}${kind}${title} pov=${yamlQuote(ch.pov)} -->`;
}

/** Emit one chapter to the Markdown line buffer, using the resolved label + RAW prose (never the
 *  beautified form — Markdown preserves the safe, verbatim semantic source). */
function markdownChapter(lines: string[], ch: SpineChapterNode): void {
  const scenes = ch.scenes.filter((s) => s.hasProse);
  if (scenes.length === 0) return;
  // A section chapter's label already IS its title (Glossary, Map…); a normal/prologue label is a bare
  // "Chapter N"/"Prologue" to which the chapter title is appended.
  const isSection = ch.kind === "front_matter" || ch.kind === "back_matter";
  const heading = isSection || !ch.title ? ch.label : `${ch.label} — ${ch.title}`;
  lines.push(`# ${heading}`, chapterComment(ch), "");
  scenes.forEach((sc, si) => {
    lines.push(`<!-- scene index=${si + 1} scene_no=${sc.sceneNo} -->`, "");
    lines.push(sc.proseRaw);
    lines.push("");
  });
}

/** Semantic Markdown emitter — consumes the ManuscriptSpine. Front matter is metadata-driven (no
 *  hard-coded series/book/litrpg flags); Part headings group their chapters; prose is preserved verbatim
 *  (`proseRaw`). `exportedAt` is injected (deterministic for tests) rather than stamped inline. */
export function renderMarkdown(
  spine: ManuscriptSpine,
  _policy: ExportPolicy,
  opts: { draft: boolean; exportedAt: string },
): string {
  const { metadata } = spine;
  const lines: string[] = [
    "---",
    "schema: dominion-manuscript/v1",
    `title: ${yamlQuote(metadata.title)}`,
  ];
  // Metadata-driven — a standalone/new book with no series identity simply omits these lines.
  if (metadata.series) lines.push(`series: ${yamlQuote(metadata.series)}`);
  if (metadata.bookNumber != null) lines.push(`book: ${metadata.bookNumber}`);
  if (metadata.subtitle) lines.push(`subtitle: ${yamlQuote(metadata.subtitle)}`);
  lines.push(
    `exported_at: ${yamlQuote(opts.exportedAt)}`,
    "source: writers-desk",
    "format: semantic-markdown",
    `draft: ${opts.draft}`,
    "---",
    "",
    `# ${metadata.title}`,
    "",
  );

  for (const node of spine.nodes) {
    if (node.type === "part") {
      lines.push(
        `# ${node.label}`,
        `<!-- part number=${node.partNo}${node.subtitle ? ` subtitle=${yamlQuote(node.subtitle)}` : ""} -->`,
        "",
      );
      for (const ch of node.chapters) markdownChapter(lines, ch);
    } else {
      markdownChapter(lines, node);
    }
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

/** Download an ExportManifest as a JSON provenance sidecar. Typed loosely to avoid a runtime dependency
 *  from this emitter file on the manifest module (the shape is `manuscript/manifest.ExportManifest`). */
export function saveManifest(manifest: object, filename: string): void {
  const blob = new Blob([JSON.stringify(manifest, null, 2)], {
    type: "application/json;charset=utf-8",
  });
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
