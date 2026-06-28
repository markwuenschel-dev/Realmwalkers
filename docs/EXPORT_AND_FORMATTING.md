# Export & Formatting — Design Note

**Status:** Phases 1–4 done — **Markdown, PDF, and DOCX export all ship.** Page numbers, table/box
rendering, and the MarketMind styling standard are in. Remaining/optional: a server-side WeasyPrint
PDF route (headless page numbers) and a Shunn submission profile (deferred — needs an author field).
Export runs entirely client-side (no new backend export route); the canon-doc viewer (Domain B) ships
too — `GET /library` serves the on-disk Markdown, rendered through the same `ProseBlocks` renderer.

## Decisions (resolved 2026-06-22)
- **Domain:** book typography for the manuscript **and** reuse the docx-js/MarketMind spec to
  color/format/render the ASCII tables (so both domains, table styling via the MarketMind converter).
- **DOCX engine:** **docx-js** — lets us port the MarketMind spec verbatim and unifies book + table/
  callout styling. Runs client-side in the React app (or a small Node step) and downloads a `.docx`.
- **"Manuscript page":** **Shunn submission format** (~250 wpp) **+ in-app page estimate**.
- **Phase 1:** ✅ in-app Shunn page estimate + Markdown export (no new deps) — done in
  `ManuscriptScreen.tsx`.
- **Phase 2:** ✅ in-app Markdown rendering (no new deps) — `prose.parseBlocks`/`parseInline` (pure
  parsers: paragraphs, headings, lists, blockquote callouts, GFM pipe tables, fenced code,
  stat-window box-art, rules; inline `code`/**bold**/*em*/links) + the reusable `ProseBlocks`
  renderer. Stat windows now keep their monospace alignment instead of being flattened into justified
  prose; tables render with the accent header / hairline borders (the in-app side of the MarketMind
  table style).
- **Phase 3 (PDF):** ✅ manuscript PDF via the browser's **Save as PDF** (no deps) — a print
  stylesheet in `index.css` (`@page` margins, `.ms-chapter { break-before: page }`, title page,
  black-on-white, widow/orphan + `break-inside` control; app chrome carries `.no-print`). Use the
  browser's print dialog (Ctrl/Cmd+P) from the manuscript view. Page numbers are real in CSS Paged
  engines (the `@bottom-center` margin box, honoured by WeasyPrint/Prince); in Chrome use the print
  dialog's "Headers and footers" toggle. A server-side WeasyPrint route can later render the *same
  HTML* for headless, page-numbered PDFs.
- **Canon viewer (Domain B):** ✅ `GET /library` + `/library/{path}` (`api/routers/docs.py`,
  read-only, sandboxed to `series/{canon,style}` + `book1/planning`, `.md` only, no traversal) → the **Canon**
  screen (`DocsScreen.tsx`) renders any doc through `ProseBlocks`. Blockquotes become tone-coloured
  **callouts** (GitHub admonitions + the `[LOCK]/[WORKING]/[OPEN]/[OVERRIDE]` status tags).
- **Phase 4 (DOCX):** ✅ docx-js DOCX export (`desk/lib/docx.ts`), client-side and **lazy-loaded** so
  it stays out of the main bundle. The emitter consumes the same `parseBlocks`/`parseInline` AST as the
  on-screen renderer ("many emitters, one parse"): **Domain A** — the manuscript as book typography
  (title page, chapters on fresh pages, justified serif prose, scene-break `⁂`, monospace stat windows,
  page-numbered footer, LitRPG `@interface` panels) via **Export Reader DOCX** in the manuscript
  toolbar; **Domain B** — any canon
  doc with MarketMind styling (navy-header tables, accent callout boxes from blockquotes, code/stat
  panels, lists, inline formatting, page numbers) via the **⬇ Word** button in the Canon viewer.
- **Shunn submission profile:** ✅ ships via **Export Shunn DOCX** — monospace, double-spaced,
  `Surname / TITLE / page#` running head; author name field in the manuscript toolbar. Server-side
  **WeasyPrint** PDF (headless, real page numbers from the same HTML) remains a natural follow-on.

---

## 1. What this site can do today (honest assessment)

| Capability | Today |
|---|---|
| Render manuscript in-browser | ✅ Title page, chapter headers, scene-break `⁂`, chapter-end `✦`, layout toggle (Page/Wide/Two-column) — all React + inline CSS |
| Render "boxes" (stat windows, canon cards) | ✅ but **browser-only** — they're React/CSS `<div>`s, nothing exists outside the DOM |
| Render Markdown tables | ✅ (Phase 2) `prose.parseBlocks`/`parseInline` + `ProseBlocks` render GFM tables, lists, callouts, headings, fenced code, stat-window box-art, and inline formatting — in both the Manuscript view and the new **Canon** viewer |
| Surface canon / planning / style docs in-app | ✅ `GET /library` (read-only, sandboxed) → the Canon screen renders them through `ProseBlocks` |
| Export to Markdown / DOCX / PDF | **Markdown** ✅ (Phase 1) · **PDF** ✅ (Phase 3, browser Save-as-PDF) · **DOCX** ✅ (Phase 4, docx-js — manuscript book format + canon MarketMind styling, lazy-loaded) |
| Page numbers | ✅ in print: real in CSS Paged engines (`@bottom-center`), or via Chrome's print headers/footers. (No in-*app* pagination — it's still a scrolling page; the Shunn estimate covers that.) |
| Stack for adding this | FastAPI/Python backend (server-side gen), React/Vite frontend (client-side gen), `httpx` available |

**Bottom line:** the app *displays* formatted content well but cannot *produce a document*. Export is
greenfield — which means we can design it cleanly rather than retrofit.

---

## 2. Two distinct export domains (do not conflate)

The MarketMind spec is a **technical-document** standard (callout blocks, badges, decision boxes). It
is the right model for **one** of our two domains, not both.

### Domain A — The manuscript (the novel)
Prose: chapters, scenes, the occasional ```stat window. Wants **book typography**: title page,
chapter pages, scene-break glyphs, running heads, page numbers, justified text. **Callout boxes /
badges are wrong here** — fiction doesn't have DECISION blocks. Tables ≈ none (stat windows instead).
Optional: **standard manuscript format (Shunn)** for submissions (12pt mono, double-spaced, ~250
words/page, `Surname / TITLE / page#` header).

### Domain B — Canon / planning / design docs
`master_timeline.md`, `setup_payoff_tracker.md`, this note, etc. Heavy **tables**, status tags
(`[WORKING]` / `[LOCK]` / `[OPEN]` / `[OVERRIDE]`), structured decisions. **This is exactly the
MarketMind domain** — its callout taxonomy and badges map cleanly:

| Realmwalkers tag | MarketMind block |
|---|---|
| `[LOCK]` | DECISION (navy) / Accepted badge |
| `[WORKING]` | ❖ Note (blue) / Proposed badge |
| `[OPEN]` | ⚠ Warning or Open badge |
| `[OVERRIDE]` | ⚠ Warning (needs sign-off) |
| status tables | navy-header tables |

So the MarketMind converter is **reusable almost as-is for Domain B** — same `Markdown → DOCX`
conversion-rules table, same title page / TOC / typography.

---

## 3. Format-by-format plan

### Markdown
- **Domain A:** assemble the manuscript endpoint → Markdown: `# Chapter N`, scene breaks (`* * *` or
  `⁂`), chapter-end rule, stat windows kept as ```stat fences. Trivial (server-side, a few KB).
- **Domain B:** canon docs are *already* Markdown on disk — "export" = bundle/serve them (optionally
  resolve includes, strip internal-only `[anchor TBD]` notes).

### DOCX — engine chosen: docx-js ✅
Shipped client-side via **`docx`** (docx-js, v9). The emitter is `desk/lib/docx.ts`; it reuses the
`parseBlocks`/`parseInline` AST. Lazy-loaded (`await import("../lib/docx")`) so the ~100 KB-gzip docx
chunk only downloads on click. (Original options kept below for the record.)

| Option | Where | Pros | Cons |
|---|---|---|---|
| **`docx-js`** ✅ chosen | Frontend (lazy chunk) | Reuses the AST; `PageNumber`/tables/shading first-class; no backend route, no new server dep | Adds a client JS chunk (lazy-loaded, so off the critical path) |
| **`python-docx`** | Backend (Python) | Lives with the data; one stack | Page-number/TOC fields need raw OXML; would reimplement the AST emitter server-side |

Either supports everything the MarketMind spec needs: **tables** (cell shading `w:shd`, borders
`w:tcBorders`, navy header), **callout boxes** (single-cell table + thick left accent border + fill),
**badges** (tiny shaded tables), **code blocks** (Consolas, gray fill), **page numbers** (footer
`PAGE` field), **TOC** (`TOC` field, Word auto-populates), **section breaks** for the title→TOC→body
zone structure.

### PDF — pick by fidelity need
| Approach | Best for | Page numbers | Cost |
|---|---|---|---|
| **HTML + CSS Paged** (WeasyPrint, or browser print-to-PDF) | Manuscript & docs | `@page { @bottom-center { content: counter(page) } }` — real, native | Light; reuses web rendering |
| **DOCX → PDF** (LibreOffice headless `soffice --convert-to pdf`) | Exact DOCX↔PDF parity | Inherited from DOCX | Heavy system dep (LibreOffice on server) |
| **ReportLab** (direct) | Total control | Manual | Most code |

**Recommendation:** Manuscript PDF via **HTML+CSS Paged** (real page numbers, book typography, reuses
the view we already render). Use **LibreOffice conversion** only if Domain-B PDFs must byte-match the
DOCX.

> ✅ **Done (Phase 3):** the browser-print half of the HTML+CSS-Paged path ships now — print
> stylesheet in `index.css` (use Ctrl/Cmd+P from the manuscript view). The server-side **WeasyPrint**
> half (headless, real `@bottom-center` page numbers, no print-dialog step) is the natural follow-on
> and reuses the *same* `ProseBlocks` HTML.

---

## 4. Page numbers / "manuscript page"

"Manuscript page" is ambiguous — three things it could mean, and we can offer all three:
1. **Real rendered page numbers** — DOCX footer `PAGE` field; PDF `counter(page)`. (What MarketMind does.)
2. **Standard manuscript format (Shunn)** — for submissions: monospaced, double-spaced, ~250 words/page,
   `Surname / TITLE / page#` running head. A separate output profile, not just a footer.
3. **In-app estimated page count** — `words / 250` (or `/300`) shown live on the Manuscript page, so you
   see "≈ 142 manuscript pages" without exporting. Cheap, immediately useful.

---

## 5. Table & box rendering plan

One renderer per surface, driven by a single parser (see §6):
- **Web (in-app):** ✅ (Phase 2 + canon viewer) `prose.parseBlocks` + `ProseBlocks` render Markdown
  tables as styled HTML `<table>` (accent header, hairline borders), stat windows as monospace "system
  windows" (the backend's box-drawing art, kept aligned), and Domain-B **callout boxes** (tone-coloured
  left-accent blocks from blockquotes — GitHub admonitions + `[LOCK]/[WORKING]/[OPEN]/[OVERRIDE]`).
- **DOCX:** tables → native tables with header shading + `#CCCCCC` borders (MarketMind table spec);
  callouts/stat-windows → single-cell tables with left accent border + fill.
- **PDF:** same as web via the print stylesheet (HTML+CSS path), so tables/boxes are identical to
  what's on screen.

---

## 6. Recommended architecture

**One parse, many emitters.** Avoid N divergent converters:

```
source (manuscript JSON  |  canon .md)
        │
        ▼
   normalized AST  ──►  Markdown emitter
                   ──►  HTML emitter (in-app + PDF via print CSS)
                   ──►  DOCX emitter (MarketMind styling for Domain B; book styling for Domain A)
                   ──►  PDF (HTML+CSS Paged; or DOCX→LibreOffice for parity)
```

The MarketMind **"Markdown → DOCX Conversion Rules"** table is, verbatim, the spec for the DOCX
emitter's Domain-B mode. The block-type detection (leading `❖ ⚠ ⛔ ✅` + keyword `DECISION/HALT/FAIL/
PASS/Gate:`) is reusable.

### Suggested sequencing
1. ✅ **Markdown export** + **in-app page-count estimate** — trivial, immediate value.
2. ✅ **In-app Markdown rendering** (tables, lists, callouts, stat-window boxes, inline) —
   `prose.parseBlocks`/`parseInline` + `ProseBlocks`.
3. ✅ **Manuscript PDF** via print-CSS (`@page`, page breaks, book typography) — browser Save-as-PDF.
   (Follow-on: server-side WeasyPrint for headless, auto-page-numbered PDFs from the same HTML.)
3b. ✅ **Canon-doc viewer** — `GET /library` + the Canon screen (callouts from blockquotes).
4. ✅ **DOCX** — docx-js, both domains (manuscript book format + canon MarketMind styling).
5. **DOCX→PDF parity** (LibreOffice) only if required. ← optional / not pursued

---

## 7. Decisions (resolved)
- **Which domain first** — built **both**: manuscript (book typography) and canon docs (MarketMind).
- **DOCX engine** — **docx-js** (`docx` v9), client-side, lazy-loaded. Not `python-docx`.
- **"Manuscript page" meaning** — in-app **Shunn estimate** (Phase 1) + **real page numbers** on
  export (Phase 3 PDF + Phase 4 DOCX footer). **Shunn submission profile** ships via Export Shunn
  DOCX (author field in toolbar).
- **MarketMind styling scope** — **Domain-B docs only** (tables/callouts/code); the novel keeps book
  typography.
- **New dependency tolerance** — kept the **backend dep-free** (no `python-docx`/`weasyprint`/
  LibreOffice); the only new dep is client-side `docx`, lazy-loaded off the critical path.
- **Where exports run** — **client-side** download in the React app (no `GET …/export` route). A
  server-side WeasyPrint route stays available as a follow-on for headless PDFs.

---

*Cross-refs: `src/dominion/api/routers/books.py` (manuscript assembly),
`frontend/src/desk/screens/ManuscriptScreen.tsx` (current rendering),
`frontend/src/desk/prose.ts` (paragraph splitter), `docs/DESIGN.md` §13 (manuscript), and the
MarketMind DOCX Formatting Specification v1.0 (external — the Domain-B styling standard).*
