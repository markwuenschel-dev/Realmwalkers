// Opt-in font embedding for the LitRPG DOCX. The interface labels use Bahnschrift, which SHIPS with
// Windows 10+/Office, so nothing is embedded by default — Word already has the glyphs (older installs
// fall back to Franklin Gothic). Georgia (body) and Consolas (code) also ship with Word/Windows and
// are never embedded either.
//
// This module exists only for installs that lack the label font, or that want an exact custom label
// look: drop the TTFs in assets/fonts/ (or set LITRPG_FONT_DIR) and list them in LABEL_FONTS below, e.g.
//   MyLabelFont-Regular.ttf, MyLabelFont-Bold.ttf
//
// The docx `fonts` option takes { name, data: Buffer, bold?, italic? }. Loading is best-effort: if a
// file is missing we skip it (the label just falls back to a substitute) rather than fail the export.
// LABEL_FONTS is empty by default.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type FontFile = { name: string; file: string; bold?: boolean; italic?: boolean };

// Bahnschrift is Word-native → nothing to embed. Populate to embed a non-installed label font instead.
const LABEL_FONTS: FontFile[] = [];

function fontDir(): string {
  if (process.env.LITRPG_FONT_DIR) return resolve(process.env.LITRPG_FONT_DIR);
  const here = dirname(fileURLToPath(import.meta.url));
  return resolve(here, "..", "assets", "fonts");
}

export type EmbeddedFont = { name: string; data: Buffer; bold?: boolean; italic?: boolean };

let cache: EmbeddedFont[] | null = null;

/** docx `fonts` descriptors for the fonts that must be embedded; [] if none of the files are present. */
export function embeddedFonts(): EmbeddedFont[] {
  if (cache) return cache;
  const dir = fontDir();
  const out: EmbeddedFont[] = [];
  for (const f of LABEL_FONTS) {
    try {
      out.push({ name: f.name, data: readFileSync(join(dir, f.file)), bold: f.bold, italic: f.italic });
    } catch {
      // missing file — skip; Word will substitute for this weight
    }
  }
  cache = out;
  return out;
}
