// Named Word paragraph styles for the Reader DOCX, derived from the ExportPolicy's typography intent.
// This replaces the emitter's inline one-off run formatting (font/size/color repeated on every TextRun)
// with a stylesheet the emitter references by id — so the forthcoming production DOCX overhaul can
// re-skin the reader by editing THESE definitions (or the policy that feeds them) without touching the
// layout code, and a Word user sees real named styles instead of hard-formatted paragraphs.

import { AlignmentType, convertInchesToTwip, type IStylesOptions } from "docx";
import type { ExportPolicy } from "./presets";

/** Stable style ids the emitter references. */
export const STYLE = {
  bookTitle: "BookTitle",
  seriesLine: "SeriesLine",
  bookSubtitle: "BookSubtitle",
  renderDescriptor: "RenderDescriptor",
  volumeEyebrow: "VolumeEyebrow",
  volumeTitle: "VolumeTitle",
  partEyebrow: "PartEyebrow",
  partTitle: "PartTitle",
  dividerSubtitle: "DividerSubtitle",
  chapterLabel: "ChapterLabel",
  chapterTitle: "ChapterTitle",
  povLine: "PovLine",
  epigraph: "Epigraph",
  sceneBreak: "SceneBreak",
  body: "Body",
  bodyFirst: "BodyFirst",
} as const;

export type ReaderStyleId = (typeof STYLE)[keyof typeof STYLE];

const GRAY = "808080";
const DIM = "606060";

function bodyLine(spacing: ExportPolicy["typography"]["lineSpacing"]): number {
  return spacing === "double" ? 480 : spacing === "single" ? 240 : 320; // "reader" default
}

/**
 * Build the Reader DOCX stylesheet from a policy. Body font/size/line-spacing come from
 * `policy.typography`; the display sizes (titles, dividers) are the reader's typographic scale, expressed
 * once here rather than sprinkled through the emitter. Sizes are half-points (docx convention).
 */
export function readerStyleDefs(policy: ExportPolicy): IStylesOptions {
  const font = policy.typography.bodyFont || "Georgia";
  const bodySize = (policy.typography.bodySizePt || 11) * 2;
  const line = bodyLine(policy.typography.lineSpacing);
  const center = { alignment: AlignmentType.CENTER } as const;
  const eyebrow = { font, size: 24, color: GRAY, characterSpacing: 40 };

  return {
    paragraphStyles: [
      // Prose body: first-line indent is the paragraph cue (classic print novel). BodyFirst (the first
      // paragraph of a scene / after a break) drops the indent per print convention.
      {
        id: STYLE.body,
        name: "Body",
        basedOn: "Normal",
        quickFormat: true,
        run: { font, size: bodySize },
        paragraph: {
          alignment: AlignmentType.JUSTIFIED,
          spacing: { line, lineRule: "auto" },
          indent: { firstLine: convertInchesToTwip(0.3) },
        },
      },
      {
        id: STYLE.bodyFirst,
        name: "Body First",
        basedOn: STYLE.body,
        paragraph: { indent: { firstLine: 0 } },
      },
      {
        id: STYLE.bookTitle,
        name: "Book Title",
        basedOn: "Normal",
        run: { font, bold: true, size: 56 },
        paragraph: center,
      },
      {
        id: STYLE.seriesLine,
        name: "Series Line",
        basedOn: "Normal",
        run: { font, size: 20, color: GRAY },
        paragraph: center,
      },
      {
        id: STYLE.bookSubtitle,
        name: "Book Subtitle",
        basedOn: "Normal",
        run: { font, italics: true, size: 26 },
        paragraph: center,
      },
      {
        id: STYLE.renderDescriptor,
        name: "Render Descriptor",
        basedOn: "Normal",
        run: { font, italics: true, size: 24, color: GRAY },
        paragraph: center,
      },
      {
        id: STYLE.volumeEyebrow,
        name: "Volume Eyebrow",
        basedOn: "Normal",
        run: eyebrow,
        paragraph: center,
      },
      {
        id: STYLE.volumeTitle,
        name: "Volume Title",
        basedOn: "Normal",
        run: { font, bold: true, size: 48 },
        paragraph: center,
      },
      {
        id: STYLE.partEyebrow,
        name: "Part Eyebrow",
        basedOn: "Normal",
        run: eyebrow,
        paragraph: center,
      },
      {
        id: STYLE.partTitle,
        name: "Part Title",
        basedOn: "Normal",
        run: { font, bold: true, size: 40 },
        paragraph: center,
      },
      {
        id: STYLE.dividerSubtitle,
        name: "Divider Subtitle",
        basedOn: "Normal",
        run: { font, italics: true, size: 24, color: GRAY },
        paragraph: center,
      },
      {
        id: STYLE.chapterLabel,
        name: "Chapter Label",
        basedOn: "Normal",
        run: { font, bold: true, size: 28 },
        paragraph: center,
      },
      {
        id: STYLE.chapterTitle,
        name: "Chapter Title",
        basedOn: "Normal",
        run: { font, italics: true, size: 26 },
        paragraph: center,
      },
      {
        id: STYLE.povLine,
        name: "POV Line",
        basedOn: "Normal",
        run: { font, size: 18, color: GRAY },
        paragraph: center,
      },
      {
        id: STYLE.epigraph,
        name: "Epigraph",
        basedOn: "Normal",
        run: { font, italics: true, size: 22, color: DIM },
        paragraph: center,
      },
      {
        id: STYLE.sceneBreak,
        name: "Scene Break",
        basedOn: "Normal",
        run: { font, size: 24, color: GRAY },
        paragraph: center,
      },
    ],
  };
}
