// The single label contract for the manuscript export spine, the three emitters, the reader UI, and
// preflight. NO consumer may resolve a structural label independently — they all call these functions.
// This is what stops Reader DOCX, Shunn DOCX, and Markdown from drifting on how a Prologue or a Part is
// titled (the bug this whole foundation exists to kill: Shunn/Markdown used to hard-code "CHAPTER N").

/** Reader-facing structural role of a chapter — mirrors the backend `ChapterKind` enum. Display-only:
 *  ordering always keys off `chapter_no`, only the label changes. */
export type ChapterKind =
  | "chapter"
  | "prologue"
  | "interlude"
  | "epilogue"
  | "front_matter"
  | "back_matter";

export const KNOWN_CHAPTER_KINDS: readonly ChapterKind[] = [
  "chapter",
  "prologue",
  "interlude",
  "epilogue",
  "front_matter",
  "back_matter",
];

/** The kinds that render as a titled section rather than a numbered chapter (front/back matter). */
export const SECTION_KINDS: readonly ChapterKind[] = ["front_matter", "back_matter"];

export function isKnownChapterKind(kind: string | null | undefined): kind is ChapterKind {
  return KNOWN_CHAPTER_KINDS.includes((kind ?? "") as ChapterKind);
}

export function isSectionKind(kind: string | null | undefined): boolean {
  return SECTION_KINDS.includes((kind ?? "") as ChapterKind);
}

// Reader display names for non-'chapter' kinds.
const KIND_LABEL: Record<Exclude<ChapterKind, "chapter">, string> = {
  prologue: "Prologue",
  interlude: "Interlude",
  epilogue: "Epilogue",
  front_matter: "Front Matter",
  back_matter: "Back Matter",
};

const ROMAN: readonly [number, string][] = [
  [1000, "M"],
  [900, "CM"],
  [500, "D"],
  [400, "CD"],
  [100, "C"],
  [90, "XC"],
  [50, "L"],
  [40, "XL"],
  [10, "X"],
  [9, "IX"],
  [5, "V"],
  [4, "IV"],
  [1, "I"],
];

/** Roman numeral for Part labels ("Part I", "Part II"). Falls back to the arabic number for anything
 *  out of range (n <= 0 or non-integer) — defensive, never throws. */
export function toRoman(n: number): string {
  if (!Number.isInteger(n) || n <= 0) return String(n);
  let out = "";
  let rem = n;
  for (const [v, s] of ROMAN) {
    while (rem >= v) {
      out += s;
      rem -= v;
    }
  }
  return out;
}

/** The label WORD for a Part-level grouping: "act" → "Act", anything else → "Part". */
export function partKindWord(kind: string | null | undefined): "Part" | "Act" {
  return kind === "act" ? "Act" : "Part";
}

/** "Part I — The Gathering Storm" / "Act I — …" (title optional → just "Part I"). Roman numbering is the
 *  print convention; the subtitle is intentionally NOT folded in here (emitters render it on its own line
 *  under the part title). `kind` selects the word only — an Act is structurally a Part. */
export function partLabel(part: {
  part_no: number;
  title?: string | null;
  kind?: string | null;
}): string {
  const head = `${partKindWord(part.kind)} ${toRoman(part.part_no)}`;
  const title = part.title?.trim();
  return title ? `${head} — ${title}` : head;
}

/** "Volume I — The Long Winter" (title optional → just "Volume I"). The top grouping tier. */
export function volumeLabel(volume: { volume_no: number; title?: string | null }): string {
  const head = `Volume ${toRoman(volume.volume_no)}`;
  const title = volume.title?.trim();
  return title ? `${head} — ${title}` : head;
}

/** "Chapter 3" for a plain chapter; the kind's own name (no number) for prologue/interlude/epilogue and
 *  front/back matter. An UNKNOWN kind falls back to "Chapter N" (so nothing renders a raw enum string);
 *  callers that need to know a kind was unrecognized should check `isKnownChapterKind` separately — the
 *  spine records that as a preflight issue. */
export function chapterLabel(ch: { kind?: string | null; chapter_no: number }): string {
  const kind = ch.kind ?? "chapter";
  if (kind === "chapter" || !isKnownChapterKind(kind)) return `Chapter ${ch.chapter_no}`;
  // Runtime-guaranteed non-'chapter' known kind (the guard above excludes "chapter" and unknown kinds).
  return KIND_LABEL[kind as Exclude<ChapterKind, "chapter">];
}

/** Display names for the known front/back-matter section types. Free-slug on the wire, so an unknown
 *  slug is title-cased (`dramatis_personae` → "Dramatis Personae") rather than rejected — the catalog
 *  can grow without a migration. Ordered roughly front-matter → back-matter for the editor dropdown. */
export const SECTION_TYPES: Record<string, string> = {
  preface: "Preface",
  foreword: "Foreword",
  introduction: "Introduction",
  dramatis_personae: "Dramatis Personae",
  map: "Map",
  timeline: "Timeline",
  pronunciation: "Pronunciation Guide",
  epigraph: "Epigraph",
  afterword: "Afterword",
  acknowledgments: "Acknowledgments",
  glossary: "Glossary",
  appendix: "Appendix",
  author_note: "Author's Note",
  about_author: "About the Author",
  preview: "Preview",
};

function titleCaseSlug(slug: string): string {
  return slug
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/** Display name for a section-type slug (known → catalog name, unknown → title-cased), or undefined for
 *  a blank/absent value. */
export function sectionTypeLabel(sectionType: string | null | undefined): string | undefined {
  const t = (sectionType ?? "").trim();
  if (!t) return undefined;
  return SECTION_TYPES[t] ?? titleCaseSlug(t);
}

/** Label for front/back-matter section chapters. Priority: the author's explicit title (a custom heading
 *  like "Map of the Northern Reach") → the section-type display name ("Glossary", "Dramatis Personae") →
 *  the generic kind label. For non-section kinds it delegates to `chapterLabel`. */
export function sectionLabel(ch: {
  kind?: string | null;
  title?: string | null;
  section_type?: string | null;
  chapter_no: number;
}): string {
  const kind = ch.kind ?? "chapter";
  if (kind === "front_matter" || kind === "back_matter") {
    return ch.title?.trim() || sectionTypeLabel(ch.section_type) || KIND_LABEL[kind];
  }
  return chapterLabel(ch);
}

/** The one dispatcher the spine uses to resolve a chapter node's primary label: section kinds prefer
 *  their title / section type (`sectionLabel`), everything else is `chapterLabel`. */
export function resolveChapterLabel(ch: {
  kind?: string | null;
  title?: string | null;
  section_type?: string | null;
  chapter_no: number;
}): string {
  return isSectionKind(ch.kind) ? sectionLabel(ch) : chapterLabel(ch);
}
