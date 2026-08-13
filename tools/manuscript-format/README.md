# manuscript-format

A standalone Python program that formats Markdown or DOCX chapters into a complete manuscript.
It began as a port of `frontend/src/desk/lib/docx.ts` and now adds desktop merge workflows,
Realmwalkers interface recovery, linked contents entries, title-page bylines, working-draft running
headers, and chapter-opening drop caps.

The point is that you can run the export outside the browser: on a file, from a shell, in CI, over a
whole directory — without the app, the database, or a running frontend.

---

## Install

```
cd tools/manuscript-format
pip install -r requirements.txt
```

One dependency: `python-docx`. Python 3.12+.

## Use

```
python -m manuscript_format INPUT [--to TARGETS] [-o DIR] [options]
```

```
python -m manuscript_format examples/sample-book.md --to reader
python -m manuscript_format examples/sample-book.md --to reader,shunn,md -o out/
python -m manuscript_format examples/sample-doc.md  --to doc
python -m manuscript_format old.docx --to reader --title "The Glass Aqueduct"
```

`python -m manuscript_format --help` lists every flag.

### The four targets

| `--to`   | Output              | What it is |
| -------- | ------------------- | ---------- |
| `reader` | `<title>.reader.docx` | Styled book: title page with `Written By:`, clickable Contents with page references, new-page chapter openings, drop caps, working-draft running headers, volume/part dividers, epigraphs, and the full LitRPG interface-panel treatment. |
| `shunn`  | `<title>.shunn.docx`  | Plain submission format — Courier, double-spaced, `Surname / TITLE / page` running header, rich blocks flattened to safe text. Ports `renderShunnDoc`. |
| `md`     | `<title>.md`          | Semantic Markdown: YAML front matter, `<!-- chapter … -->` structural comments, prose preserved verbatim. Ports `renderMarkdown`. |
| `doc`    | `<title>.docx`        | Flat canon-doc render — no book format, markdown straight through the block parser. Ports `buildDocDoc`. |

`--to` accepts a comma-separated list; each target is written once.

### Double-click merge workflow

Run `FORMAT MANUSCRIPT.bat` and choose multiple chapter files or a manuscript folder. The picker can
merge recognized front matter, chapters, interludes, epilogues, and back matter in natural filename
order before exporting one Reader manuscript. The book workflow prompts for the title and author;
for Realmwalkers the defaults are `Realmwalkers I: Threadbound` and `Nalakram`.

Generated `.reader.docx`, `.shunn.docx`, and `.reference.docx` files are excluded from folder scans.
When editable Markdown and derived formatted DOCX files are both present, Markdown is preferred to
avoid duplicate chapters. Explicitly selected DOCX chapters are still supported.

### Input

Three shapes, auto-detected:

1. **Semantic manuscript Markdown** — what the `md` target emits (YAML front matter with
   `schema: dominion-manuscript/v1`, plus `<!-- chapter -->` / `<!-- part -->` / `<!-- volume -->` /
   `<!-- scene -->` markers). Round-trips losslessly: emit → re-ingest → re-emit is byte-identical.
2. **Plain Markdown** — no markers. Structure is inferred:
   * Each `# ` heading starts a chapter — `# Prologue`, `# Chapter 2 — Title`, `# Chapter Two`
     (spelled-out numbers up to twenty), or a bare name.
   * A `## ` line immediately under the chapter heading is lifted as the chapter *title*, so
     `# Chapter Two` / `## The Facility` renders as **CHAPTER 2** / *The Facility*.
   * A whole line of `---`, `***`, `* * *`, `___`, `⁂`, or a bare `#` starts a new scene. This
     matches the vocabulary the repo's own importer already accepts (`_SCENE_BREAK_RE`,
     `src/dominion/workers/memory/manuscript_split.py:34-37`), so a file that imports cleanly into
     the app splits into the same scenes here.
   * Leading YAML front matter is treated as **metadata, not prose** — it is stripped from the body,
     and `title` / `chapter` / `scene` / `pov` are harvested from it. This is the shape the scene
     files in `book1/manuscript/scenes/` are written in.
   * `--split none` forces the whole document into one chapter.
3. **`.docx`** — text recovery. See the limitation below.

Metadata flags (`--title`, `--series`, `--book-no`, `--subtitle`, `--author`) always override
whatever the file declares.

### Overrides

`--body-font`, `--body-size`, `--line-spacing`, `--margin`, `--scene-break`, `--no-parts`,
`--no-half-title`, `--no-toc` layer on top of a preset without mutating it — the same
`ExportPolicy` seam the app uses, so a re-skin never touches layout code.

---

## Known limitations

These are inherited from the TypeScript, not introduced by the port. They are listed here because
each one will otherwise look like a bug in this tool.

**1. DOCX input is a recovery path, not a fully lossless source format.**
The formatter recognizes and reconstructs its own Realmwalkers Current Status sheet, pale-blue
interface notice, compact Insight scan, resource readout tables, and working-draft running-header
marker when formatted chapter DOCX files are merged. Arbitrary third-party Word tables and custom
interface designs still recover as plain table grids because their original directives no longer
exist. Markdown remains the lossless source format.

**2. `@style` directives break on the book path — an upstream defect this port reproduces.**
`beautify()` (`frontend/src/desk/lib/beautify.ts:50-64`) classifies a blank-line-delimited run as
"structural" only when its *first* line is a box-drawing char, heading, rule, list, blockquote, or a
table header. A standalone `@style role=sheet name="Wren Calloway"` line is none of those, so the run
is re-flowed and typeset. Two consequences, both verified against the TypeScript:

* `@style` with **no blank line** before the table → the whole run collapses onto one line and the
  table is destroyed (`parse_blocks` returns zero blocks).
* `@style` with **a blank line** → the table survives, but `typeset()` has curled the quotes, so
  `name="Wren Calloway"` no longer matches the `"([^"]*)"` attribute pattern and `spec.name` becomes
  `”Wren`.

This affects every production path that goes through the spine (`spine.py`, porting
`spine.ts:118`) — both DOCX emitters and the on-screen reader. It does **not** affect `--to doc`,
which calls `parse_blocks` directly with no `beautify()` pre-pass, exactly as `buildDocDoc` does
(`docx.ts:1113-1122`). That is why `examples/sample-doc.md` is where the character sheet lives.

The app's own suite cannot see this: its `@style` fixtures enter at `buildDocDoc` or call
`parseBlocks` directly. `@interface` is immune for an unrelated reason — it lives inside a
```` ``` ```` fence, which `beautify` passes through verbatim. Worth fixing upstream (an `@style`
case in `isStructuralRun`, plus quote-protection for directive lines); this port deliberately does
not diverge.

**3. The Markdown format drops epigraphs.**
`chapterComment` (`docx.ts:1584-1590`) emits `number`, `kind`, `section_type`, `title`, `pov` — not
`epigraph`. A chapter epigraph renders in the Reader DOCX but cannot survive a Markdown round-trip,
because it is never written to the file.

**4. Fonts are not embedded.**
`embeddedFonts()` returns `[]` upstream (`frontend/src/desk/lib/fonts.ts:15-17`) because the export
runs in a browser with no filesystem. Ported as the same empty seam. Every font used (Bahnschrift,
Georgia, Consolas) ships with Windows/Office by design. This port runs server-side and *could*
embed fonts — that is a capability it unlocks, not a parity gap.

**5. Contents page references are Word fields.**
Each Contents entry is an internal hyperlink to a chapter bookmark and ends in a `PAGEREF` field.
The document requests field updates when opened. After substantial manual editing in an application
that does not refresh fields automatically, select the document and update fields (`Ctrl+A`, `F9` in
Microsoft Word) to refresh the displayed page numbers.

---

## Module map

Every module names the TypeScript file it ports.

| Python | Ports |
| ------ | ----- |
| `prose.py` | `desk/prose.ts` — `parseBlocks`, `parseInline`, `timeMarker`, `parseInterfaceSpec` |
| `beautify.py` | `desk/lib/beautify.ts` — re-flow + typeset pre-pass |
| `surfaces.py` | `desk/lib/litrpgSurfaces.ts` — palette, `resolveSurface`, WCAG contrast |
| `labels.py` | `desk/manuscript/labels.ts` + `metadata.ts` — the label contract |
| `presets.py` | `desk/manuscript/presets.ts` — `ExportPolicy` and the three presets |
| `spine.py` | `desk/manuscript/spine.ts` + `readerFrontMatter.ts` — the IR and the production plan |
| `styles.py` | `desk/manuscript/docxStyles.ts` — the named Reader stylesheet |
| `render_reader.py` | `desk/lib/docx.ts` — Reader emitter, all panels, `buildDocDoc` |
| `render_shunn.py` | `desk/lib/docx.ts` — `renderShunnDoc` |
| `render_markdown.py` | `desk/lib/docx.ts` — `renderMarkdown` + filename helpers |
| `ooxml.py` | the `docx` npm library's builder API (no TS counterpart) |
| `ingest.py` | *net-new* — no importer exists in the repo |

`ooxml.py` is the one piece with no upstream counterpart. `docx.ts` is written against docx-js,
which is declarative; python-docx is imperative with a fixed table grid and no public API for
character spacing, per-cell borders, column spans, paragraph borders, or percentage widths. So
`ooxml.py` supplies the same declarative vocabulary (`Run`, `Par`, `Cell`, `Row`, `Tbl`) and
materializes it straight to OOXML. python-docx is used only as the package container — it owns
`[Content_Types].xml`, relationships, `styles.xml`, `numbering.xml`, headers/footers, and `sectPr`.
That keeps the emitter port nearly line-for-line with the original.

### Unit conventions (matching docx-js exactly)

| Field | Unit |
| ----- | ---- |
| `Run.size` | half-points (`22` → 11pt) |
| `Run.character_spacing` | twentieths of a point |
| `Border.size` | eighths of a point (`4` → 0.5pt) |
| spacing / indent / tab positions | twips (1440 per inch) |
| `Tbl.width_pct` | percent (emitted as OOXML fiftieths-of-a-percent) |

---

## Tests

```
python -m pytest
python -m ruff check .
```

36 tests. The DOCX assertions deliberately mirror the repo's own suites — structural labels and
named-style ids as in `frontend/src/desk/manuscript/docxXml.test.ts`, panel text and hex colours as
in `frontend/src/desk/lib/docxInterface.test.ts` — so a failure means the port has drifted from the
TypeScript, not merely that a byte moved.

Two invariants worth knowing about:

* `test_every_surface_label_meets_wcag_aa` sweeps every role × domain × creature × intensity
  combination (18 × 22 × 18 × 4) and asserts label text clears 4.5:1 on its own fill — the
  invariant `interface-markup.md` advertises.
* `test_semantic_markdown_round_trips_byte_identical` asserts emit → ingest → emit is byte-equal.

---

## Assemble a manuscript from multiple files

The formatter accepts multiple source paths or an entire folder. Files are sorted naturally
(`chapter_2` before `chapter_10`), ingested with the same parser as single-file exports, and rendered
as one manuscript.

```powershell
python -m manuscript_format `
  frontmatter chapters backmatter `
  --to reader `
  --title "Realmwalkers I" `
  --book-front-matter `
  -o out
```

The included `FORMAT MANUSCRIPT.bat` provides the same workflow through a picker:

1. Select multiple files or a manuscript folder.
2. Choose **Merge into one manuscript**.
3. Enter the book title.
4. Choose Book, Submission, Markdown, or a combined export.

The drag-and-drop `.bat` files also accept multiple files or a folder. Multiple inputs are merged;
a single input is formatted normally.

### Authored front matter and back matter

Recognized plain-Markdown headings are classified automatically. Examples:

```markdown
# Dedication

For...
```

```markdown
# Acknowledgments

Thank you...
```

Supported front-matter names include Copyright, Dedication, Epigraph, Foreword, Preface,
Introduction, Dramatis Personae, Map, Timeline, and Pronunciation Guide. Supported back-matter
names include Afterword, Acknowledgments/Acknowledgements, Appendix, Glossary, Author's Note,
About the Author, Author Bio, and Preview.

For explicit control, a standalone source file may declare its role in YAML:

```yaml
---
kind: front_matter
section_type: dedication
title: Dedication
---
```

or:

```yaml
---
kind: back_matter
section_type: glossary
title: Glossary
---
```

Authored front matter is placed in canonical publishing order around the generated half-title,
title page, and Contents. Body chapters retain source order. Authored back matter is emitted after
the body in canonical section order. Semantic manuscript Markdown keeps its existing volume, part,
chapter, scene, front-matter, and back-matter markers.

## Realmwalkers manuscript-merge fixes in 1.2

The double-click **Book manuscript** workflow now produces the `.reader.docx` as the primary output
and opens/selects that file in Explorer. A flat reference export, when requested, is named
`.reference.docx` so it cannot be mistaken for the manuscript.

Merged books now:

- include one full title page by default (the picker suppresses the redundant half-title page),
- start every chapter on a new page,
- spell chapter numbers from One through Twenty,
- render `WORKING DRAFT — PROVISIONAL` centered in red,
- render opening date/location lines as centered chapter context rather than blue Word headings,
- preserve the amber Current Status sheet and pale-blue interface/readout cards even when the
  selected sources are previously formatted chapter DOCX files,
- prefer editable Markdown over derived working/formatted DOCX exports during folder scans, and
- place authored front matter before chapters and authored back matter after them.

When **Everything** is selected, open the `.reader.docx`; the `.reference.docx` is intentionally a
flat canon/reference document and does not use book front matter or chapter-opening layout.
