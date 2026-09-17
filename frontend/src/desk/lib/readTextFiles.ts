// Reading author-supplied text files in the browser.
//
// Two layers, on purpose:
// - `readFilesAsText` reads every file verbatim ({filename, text}) with no filtering or cleanup. The
//   manuscript uploader uses it: it has always sent whatever was dropped to the parse endpoint, and
//   moving it here must not change that.
// - `readTextFiles` is the stricter reader for read-throughs. It filters by extension (the `accept`
//   attribute only filters the picker; a drop delivers anything), strips a UTF-8 BOM and a leading
//   YAML front-matter block, and derives a chapter label.

export interface RawTextFile {
  filename: string;
  text: string;
}

export interface TextFile extends RawTextFile {
  label: string;
}

export interface ReadTextFilesResult {
  accepted: TextFile[];
  /** Names of files skipped because they are not .md or .txt — shown to the author, never silent. */
  rejected: string[];
}

/** Schema cap on a read-through chapter label (ReadThroughChapterIn.label max_length). */
export const LABEL_MAX = 200;

const TEXT_EXTENSION = /\.(md|txt)$/i;

export const isTextFileName = (name: string): boolean => TEXT_EXTENSION.test(name);

/** Read every file as text, in order, unfiltered and unmodified. */
export function readFilesAsText(files: FileList | File[]): Promise<RawTextFile[]> {
  return Promise.all(
    Array.from(files).map(async (f) => ({ filename: f.name, text: await f.text() })),
  );
}

export const stripBom = (text: string): string =>
  text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;

// `---` on the first line, YAML until a closing `---` or `...` line. The first non-blank line inside
// must look like a `key:` so a chapter that merely opens with a horizontal-rule scene break is not
// eaten up to its next break.
const FRONT_MATTER = /^---[ \t]*\r?\n(?:([\s\S]*?)\r?\n)?(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/;
const YAML_KEY = /^[A-Za-z0-9_-]+[ \t]*:/;

export function stripFrontMatter(text: string): string {
  const m = FRONT_MATTER.exec(text);
  if (!m) return text;
  const firstLine = (m[1] ?? "").split(/\r?\n/).find((l) => l.trim() !== "");
  if (firstLine !== undefined && !YAML_KEY.test(firstLine.trim())) return text;
  return text.slice(m[0].length);
}

const ATX_HEADING = /^[ ]{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/m;

const fileStem = (filename: string): string => {
  const base = filename.split(/[\\/]/).pop() ?? filename;
  const dot = base.lastIndexOf(".");
  const stem = dot > 0 ? base.slice(0, dot) : base;
  return stem.trim() || base;
};

/** First markdown `#` heading (any level), else the filename without its extension. */
export function labelFor(filename: string, text: string): string {
  const heading = ATX_HEADING.exec(text)?.[1]?.trim();
  return (heading || fileStem(filename)).slice(0, LABEL_MAX);
}

/** Clean a chapter's text the way the read-through composer stores it. */
export const cleanChapterText = (text: string): string => stripFrontMatter(stripBom(text));

export async function readTextFiles(files: FileList | File[]): Promise<ReadTextFilesResult> {
  const all = Array.from(files);
  const rejected = all.filter((f) => !isTextFileName(f.name)).map((f) => f.name);
  const raw = await readFilesAsText(all.filter((f) => isTextFileName(f.name)));
  const accepted = raw.map(({ filename, text }) => {
    const cleaned = cleanChapterText(text);
    return { filename, label: labelFor(filename, cleaned), text: cleaned };
  });
  return { accepted, rejected };
}
