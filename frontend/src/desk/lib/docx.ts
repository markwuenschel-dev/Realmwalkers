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
import { partKindWord, toRoman } from "../manuscript/labels";
import { readerStyleDefs, STYLE } from "../manuscript/docxStyles";
import { planReaderProduction } from "../manuscript/readerFrontMatter";
import type { ExportPolicy } from "../manuscript/presets";
import {
  spineCounts,
  type ManuscriptSpine,
  type SpineChapterNode,
  type SpinePartNode,
  type SpineVolumeNode,
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

// When present, book body paragraphs reference the named Body / BodyFirst styles (Reader DOCX) instead
// of carrying inline font/size — the style provides the typography, keeping this file's layout code free
// of one-off run formatting.
type BodyStyleIds = { body: string; first: string } | undefined;

function paraFor(
  text: string,
  book: boolean,
  indentFirstLine = false,
  bodyStyles?: BodyStyleIds,
): Paragraph {
  // Book paragraphs use a first-line indent as the paragraph cue (classic print novel), with NO extra
  // space between paragraphs. The FIRST paragraph of a scene / after a scene break is left un-indented
  // (print convention), signalled by indentFirstLine=false from renderBlocks.
  if (book && bodyStyles) {
    // Named-style path: the Body/BodyFirst style owns font, size, alignment, and the indent cue; runs
    // carry only their own inline emphasis (bold/italic/code), inheriting the rest from the style.
    return new Paragraph({
      style: indentFirstLine ? bodyStyles.body : bodyStyles.first,
      children: inlineRuns(text),
    });
  }
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

function renderBlocks(
  blocks: ProseBlock[],
  book: boolean,
  bodyStyles?: BodyStyleIds,
): (Paragraph | Table)[] {
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
        out.push(paraFor(b.text, book, book && seenBookPara, bodyStyles));
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
  position?: number | null; // reading-order sort key; falls back to chapter_no when absent
  chapter_no?: number | null; // DISPLAY number; null for a numberless kind (prologue/…)
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
  const orderKey = (c: ManuscriptChapterInput) => c.position ?? c.chapter_no ?? 0;
  return {
    book_id: "",
    title,
    series: null,
    book_no: null,
    subtitle: null,
    volumes: [],
    parts: [],
    chapters: [...chapters]
      .sort((a, b) => orderKey(a) - orderKey(b))
      .map((c) => ({
        position: c.position ?? null,
        chapter_no: c.chapter_no ?? null,
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

const READER_BODY_STYLES = { body: STYLE.body, first: STYLE.bodyFirst };

/**
 * The Reader DOCX layout state machine. It walks the ManuscriptSpine and accumulates style-referencing
 * paragraphs — every character/alignment decision lives in the named stylesheet (`readerStyleDefs`), so
 * this class only chooses WHICH style and the contextual spacing. That's the whole point of the slice:
 * no inline one-off run formatting, so the styling can be re-skinned by editing the stylesheet (or the
 * policy that feeds it) without touching this layout code.
 */
class ReaderLayout {
  readonly children: (Paragraph | Table)[] = [];
  constructor(private readonly policy: ExportPolicy) {}

  private styled(style: string, text: string, spacing?: Record<string, number>): void {
    this.children.push(
      new Paragraph({ style, ...(spacing ? { spacing } : {}), children: [new TextRun(text)] }),
    );
  }

  private pageBreak(): void {
    this.children.push(new Paragraph({ children: [new PageBreak()] }));
  }

  /** Half-title page — the book title alone on the leading page, ahead of the full title page (the
   *  standard first leaf of a printed book). Generated from metadata; page-broken from the title page. */
  halfTitle(metadata: ManuscriptSpine["metadata"]): void {
    this.styled(STYLE.bookTitle, metadata.title, { before: 3600, after: 0 });
    this.pageBreak();
  }

  /** Table of Contents — a generated Contents page listing every non-front-matter section that has prose
   *  (Prologue, Chapters, Epilogue, back matter) by its resolved label, in reading order. No page numbers:
   *  those are layout-dependent (Word recomputes them), so a deterministic label list is the honest export. */
  tableOfContents(entries: readonly string[]): void {
    if (entries.length === 0) return;
    this.pageBreak();
    this.styled(STYLE.chapterLabel, "CONTENTS", { before: 480, after: 240 });
    for (const label of entries) this.styled(STYLE.body, label, { after: 80 });
  }

  /** Title page — series line + spelled-out book number from ExportMetadata (never hard-coded); the
   *  render descriptor (approved/draft mode, or a fragment label) is passed by the pipeline. */
  titlePage(metadata: ManuscriptSpine["metadata"], renderSubtitle?: string): void {
    const bookLine = bookNumberLabel(metadata.bookNumber);
    const hasHead = this.policy.includeSeriesLine && !!(metadata.series || bookLine);
    if (hasHead) {
      const head = [metadata.series?.toUpperCase(), bookLine].filter(Boolean).join(" · ");
      this.styled(STYLE.seriesLine, head, { before: 2400, after: 240 });
    }
    this.styled(STYLE.bookTitle, metadata.title, { before: hasHead ? 0 : 2400, after: 200 });
    if (this.policy.includeSubtitle && metadata.subtitle) {
      this.styled(STYLE.bookSubtitle, metadata.subtitle, { after: 120 });
    }
    if (renderSubtitle) this.styled(STYLE.renderDescriptor, renderSubtitle);
  }

  private divider(
    eyebrowStyle: string,
    eyebrow: string,
    titleStyle: string,
    title: string,
    subtitle: string | null,
  ): void {
    this.pageBreak();
    this.styled(eyebrowStyle, eyebrow, { before: 2000, after: 200 });
    this.styled(titleStyle, title, { after: subtitle ? 80 : 0 });
    if (subtitle) this.styled(STYLE.dividerSubtitle, subtitle);
  }

  volumeDivider(v: SpineVolumeNode): void {
    this.divider(
      STYLE.volumeEyebrow,
      `VOLUME ${toRoman(v.volumeNo)}`,
      STYLE.volumeTitle,
      v.title,
      v.subtitle,
    );
  }

  partDivider(p: SpinePartNode): void {
    const eyebrow = `${partKindWord(p.kind).toUpperCase()} ${toRoman(p.partNo)}`;
    this.divider(STYLE.partEyebrow, eyebrow, STYLE.partTitle, p.title, p.subtitle);
  }

  /** One chapter: page break, resolved label, optional title/POV/epigraph, then its scenes (from the
   *  pre-parsed spine blocks — no re-parsing here). Front/back matter renders as a titled section (the
   *  label already IS the section name), so no duplicate title line and no POV line. Skips a
   *  prose-less chapter (preflight flags it). */
  chapter(ch: SpineChapterNode): void {
    const scenes = ch.scenes.filter((s) => s.hasProse);
    if (scenes.length === 0) return;
    this.pageBreak();
    const epigraph = ch.epigraph?.trim();
    const isSection = ch.kind === "front_matter" || ch.kind === "back_matter";
    const showTitle = !!ch.title && !isSection;
    const showPov = !isSection && ch.pov.trim().length > 0;

    this.styled(STYLE.chapterLabel, ch.label.toUpperCase(), {
      before: 480,
      after: showTitle ? 60 : 80,
    });
    if (showTitle) this.styled(STYLE.chapterTitle, ch.title as string, { after: 80 });
    if (showPov) {
      this.styled(STYLE.povLine, `POV · ${ch.pov}`, { after: epigraph ? 200 : 320 });
    } else if (!epigraph) {
      // Preserve the space before prose the POV line would have provided.
      this.children.push(new Paragraph({ spacing: { after: 240 }, children: [] }));
    }
    if (epigraph) this.styled(STYLE.epigraph, epigraph, { after: 320 });

    scenes.forEach((sc, si) => {
      if (si > 0) {
        this.styled(STYLE.sceneBreak, this.policy.sceneBreakGlyph || "⁂", {
          before: 160,
          after: 160,
        });
      }
      for (const el of renderBlocks(sc.blocks, true, READER_BODY_STYLES)) this.children.push(el);
    });
  }
}

/** Reader DOCX emitter — consumes the ManuscriptSpine via the ReaderLayout state machine, rendering
 *  Volume dividers → Part dividers → each part's chapters (or ungrouped parts/chapters) from the spine's
 *  resolved labels + pre-parsed blocks, referencing the named stylesheet built from the policy. */
export function renderReaderDoc(
  spine: ManuscriptSpine,
  policy: ExportPolicy,
  opts: { renderSubtitle?: string } = {},
): Document {
  const layout = new ReaderLayout(policy);

  // Production sequence: the front matter (half-title, title page, authored front-matter sections and the
  // generated Table of Contents) is planned in canonical publishing order by the pure planner, then the
  // body follows. Back matter needs no special handling — it sorts last by `position` and flows through
  // the body walk. The planner is pure/testable; this switch is the only docx-specific rendering.
  const plan = planReaderProduction(spine, policy);
  for (const item of plan.front) {
    if (item.type === "half_title") layout.halfTitle(spine.metadata);
    else if (item.type === "title_page") layout.titlePage(spine.metadata, opts.renderSubtitle);
    else if (item.type === "toc") layout.tableOfContents(item.entries);
    else layout.chapter(item.node);
  }

  const emitPart = (part: SpinePartNode) => {
    if (policy.renderParts) layout.partDivider(part);
    for (const ch of part.chapters) layout.chapter(ch);
  };
  for (const node of plan.body) {
    if (node.type === "volume") {
      if (policy.renderParts) layout.volumeDivider(node);
      for (const part of node.parts) emitPart(part);
    } else if (node.type === "part") {
      emitPart(node);
    } else {
      layout.chapter(node);
    }
  }

  const m = convertInchesToTwip(policy.pageSetup.marginInches || 1);
  return new Document({
    creator: "Writers' Desk",
    title: spine.metadata.title,
    styles: readerStyleDefs(policy),
    sections: [
      {
        properties: { page: { margin: { top: m, bottom: m, left: m, right: m } } },
        footers: { default: pageFooter() },
        children: layout.children,
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

/** A plain grouping divider for Shunn — a centered uppercased label ("PART I — TITLE" / "ACT I …" /
 *  "VOLUME I …"), no rich styling. Consumes the spine's pre-resolved node label. */
function shunnDivider(label: string): Paragraph[] {
  return [
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 1200, after: 240, ...DOUBLE },
      children: [shunnRun(label.toUpperCase())],
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

  const emitPart = (part: SpinePartNode) => {
    if (policy.renderParts) children.push(...shunnDivider(part.label));
    for (const ch of part.chapters) children.push(...shunnChapter(ch));
  };
  for (const node of spine.nodes) {
    if (node.type === "volume") {
      if (policy.renderParts) children.push(...shunnDivider(node.label));
      for (const part of node.parts) emitPart(part);
    } else if (node.type === "part") {
      emitPart(node);
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
  const section = ch.sectionType ? ` section_type=${ch.sectionType}` : "";
  const number = ch.chapterNo != null ? ` number=${ch.chapterNo}` : ""; // numberless kinds omit it
  return `<!-- chapter${number}${kind}${section}${title} pov=${yamlQuote(ch.pov)} -->`;
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

  const emitPart = (part: SpinePartNode) => {
    lines.push(
      `# ${part.label}`,
      `<!-- part number=${part.partNo} kind=${part.kind}${part.subtitle ? ` subtitle=${yamlQuote(part.subtitle)}` : ""} -->`,
      "",
    );
    for (const ch of part.chapters) markdownChapter(lines, ch);
  };
  for (const node of spine.nodes) {
    if (node.type === "volume") {
      lines.push(
        `# ${node.label}`,
        `<!-- volume number=${node.volumeNo}${node.subtitle ? ` subtitle=${yamlQuote(node.subtitle)}` : ""} -->`,
        "",
      );
      for (const part of node.parts) emitPart(part);
    } else if (node.type === "part") {
      emitPart(node);
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
