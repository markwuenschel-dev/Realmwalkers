// Font descriptors for the LitRPG DOCX `fonts` option. The interface labels use Bahnschrift, which
// SHIPS with Windows 10+/Office (older installs fall back to Franklin Gothic); Georgia (body) and
// Consolas (code) are Word-native too — so nothing needs embedding by default.
//
// The Reader export runs in the BROWSER (see manuscript/exportActions.ts), which has no filesystem, so
// custom label fonts cannot be read from disk and embedded here — embeddedFonts() returns []. This
// stays as the seam docx.ts hands to docx's `fonts` option; wiring real embedding would need a
// server-side export path that can read TTFs. A client bundle must NOT import node:fs — doing so breaks
// the Next/Turbopack build, since docx.ts is reached from client components (e.g. PacketsScreen).

export type EmbeddedFont = { name: string; data: Buffer; bold?: boolean; italic?: boolean };

/** docx `fonts` descriptors for fonts that must be embedded. Empty: Bahnschrift is Word-native and the
 *  browser export cannot read font files from disk. */
export function embeddedFonts(): EmbeddedFont[] {
  return [];
}
