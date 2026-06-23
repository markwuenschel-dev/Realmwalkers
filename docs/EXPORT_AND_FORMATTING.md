# Export & Formatting — Design Note

**Status:** Phase 1 in progress. Plan for exporting Realmwalkers content to **Markdown, DOCX,
and PDF**, with page numbers, table/box rendering, and a shared styling standard. Uses the
**MarketMind DOCX Formatting Specification** as the polish bar for document-style output.

## Decisions (resolved 2026-06-22)
- **Domain:** book typography for the manuscript **and** reuse the docx-js/MarketMind spec to
  color/format/render the ASCII tables (so both domains, table styling via the MarketMind converter).
- **DOCX engine:** **docx-js** — lets us port the MarketMind spec verbatim and unifies book + table/
  callout styling. Runs client-side in the React app (or a small Node step) and downloads a `.docx`.
- **"Manuscript page":** **Shunn submission format** (~250 wpp) **+ in-app page estimate**.
- **Phase 1:** ✅ in-app Shunn page estimate + Markdown export (no new deps) — done in
  `ManuscriptScreen.tsx`. Next: Phase 2 = docx-js DOCX (book format + Shunn profile + MarketMind
  table/callout rendering); Phase 3 = PDF (print-CSS for the manuscript).

---

## 1. What this site can do today (honest assessment)

| Capability | Today |
|---|---|
| Render manuscript in-browser | ✅ Title page, chapter headers, scene-break `⁂`, chapter-end `✦`, layout toggle (Page/Wide/Two-column) — all React + inline CSS |
| Render "boxes" (stat windows, canon cards) | ✅ but **browser-only** — they're React/CSS `<div>`s, nothing exists outside the DOM |
| Render Markdown tables | ❌ The Manuscript view uses a trivial paragraph splitter (`prose.seg`); canon docs (full of tables) aren't surfaced in-app at all |
| Export to Markdown / DOCX / PDF | ❌ **None.** No export code anywhere; no `python-docx` / `docx-js` / `weasyprint` / `reportlab` installed |
| Page numbers | ❌ It's a scrolling web page — no pagination concept |
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

### DOCX — two engine options
| Option | Where | Pros | Cons |
|---|---|---|---|
| **`python-docx`** | Backend (Python) | Lives with the data; one stack; tables/shading/borders/fields all doable | Page-number/TOC fields need raw OXML; reimplements MarketMind from scratch |
| **`docx-js`** (reuse) | Frontend or small Node step | **Port the MarketMind spec verbatim** (it *is* docx-js); first-class `TableOfContents`, `PageNumber` | Adds a JS doc path; runs client-side or needs a Node service |

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
- **Web (in-app):** render Markdown tables as styled HTML `<table>` (navy header, thin borders);
  render ```stat windows as boxed "system windows"; render Domain-B callouts as colored boxes. (Needs
  a Markdown renderer in the frontend — none today.)
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
1. **Markdown export** + **in-app page-count estimate** — trivial, immediate value.
2. **In-app Markdown rendering** (tables + stat-window boxes) — unblocks viewing canon docs *and* the
   PDF path (same HTML).
3. **Manuscript PDF** via print-CSS (`@page`, page numbers, scene/chapter formatting).
4. **DOCX** — choose engine (§3); start with whichever domain you prioritize.
5. **DOCX→PDF parity** (LibreOffice) only if required.

---

## 7. Decisions needed
- **Which domain first** — the **novel manuscript** (book typography) or the **canon/design docs**
  (MarketMind-style)? Different formatting systems; pick the one you need sooner.
- **DOCX engine** — **`python-docx`** (server, one stack) or **reuse `docx-js`** (port the MarketMind
  converter directly)?
- **"Manuscript page" meaning** — real page numbers, **Shunn** submission format, an in-app estimate,
  or all three?
- **MarketMind styling scope** — Domain-B docs only (recommended), or do you want any of it on the
  novel too?
- **New dependency tolerance** — OK to add `python-docx` / `weasyprint`? Is **LibreOffice on the
  server** acceptable for exact DOCX→PDF, or keep deps light?
- **Where exports run** — server-side endpoint (`GET /books/{id}/export?format=docx`) vs client-side
  download in the React app.

---

*Cross-refs: `src/dominion/api/routers/books.py` (manuscript assembly),
`frontend/src/desk/screens/ManuscriptScreen.tsx` (current rendering),
`frontend/src/desk/prose.ts` (paragraph splitter), `docs/DESIGN.md` §13 (manuscript), and the
MarketMind DOCX Formatting Specification v1.0 (external — the Domain-B styling standard).*
