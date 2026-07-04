# Lane 4 — deterministic `canon_contract_leak` guard from packet prohibitions

Failure addressed (analysis §4): run `51d635ec`'s assembled Ch1 draft contained
"Neurochromatic Eyes flickered at the edge of his perception, turning the field into layered
probability and emphasis" although the chapter contract ruled "No Eyes notification in Chapter 1
… No [ INTERFACE ], no Neurochromatic Eyes, no Meszkhal item signal". Zero of the run's 24
issues flagged it.

## Root cause

The prohibition never existed in a deterministically checkable form:

1. **The ruling lives only in free text.** The "no Eyes in Ch1" decision is
   `open_questions.resolved[1].resolution` — prose a human wrote into the packet editor. Nothing
   converts a ruling into scannable contract terms.
2. **The allowlists actively mask it.** `canon_locks[0]` ("Marcus uses Neurochromatic Eyes …
   do not rename") and the `surface_terms` entry for the Eyes (allowed: "Neurochromatic Eyes",
   "Eyes", "ocular interface"; forbidden: only the legacy rename "Angelic Fortitude") both state
   the term is the CORRECT NAME. Every allowlist-shaped check therefore saw the on-page use as
   fine. A canon **lock** (background truth / naming discipline) and an on-page **prohibition**
   (this concept must not appear this chapter) were conflated into one vocabulary.
3. **No post-assembly prose-vs-contract scan existed.** `run_chapter_draft_qa` in
   `src/dominion/workers/production.py` carried the literal comment
   "*Placeholder: required beats / forbidden would require deeper analysis of prose vs
   contracts*". The only prose checks were budget/continuity/roster-visibility. Scene-level QA
   (`scene_packet/qa.py`) attacks the packet, not drafted prose, and although the ruling text
   rode along via `format_chapter_rulings`, an LLM QA pass is capped at `repair` severity and
   was never asked for an explicit term scan — it simply never inferred the ban from free text.
4. `forbidden_ui_concepts` / `forbidden_knowledge` / `forbidden_reveals` are abstract sentences
   ("Any UI language implying …") — none of them names the Eyes, so even a field-literal check
   of those fields would have missed this leak.

## Fix

New pure module **`src/dominion/workers/canon_guards.py`** (stdlib + pure `dominion.shared`
helpers only — no DB/LLM/I-O; safe for the lane-10 harness to import):

- `derive_prohibited_terms(packet_body, open_questions)` — derives the prohibition list from the
  packet's OWN fields, three tiers:
  - **`resolved_ruling` → `block`**: explicit negations in resolved rulings
    ("no/Not <Capitalized Phrase>", "no [ BRACKET TOKEN ]"). Present/mentioned character names
    are excluded ("No Serra interiority yet" must not ban "Serra"); allowlists are deliberately
    NOT consulted — a chapter ruling overrides general naming policy (the exact masking that let
    this leak survive).
  - **`forbidden_surface_term` → `block`/`repair`**: explicit `forbidden_surface_terms` from
    `surface_terms`/`entity_bindings` + short literal `characters_forbidden` entries. Entries
    timed to an in-chapter event ("until visual identity is revealed mid-duel") are skipped
    (whole-prose scan can't adjudicate position); entries deferred beyond the chapter ("after
    chapter 1 …", "deferred beyond chapter 1" — Roth, true name) stay hard for the whole chapter.
  - **`prohibition_field` → `warn` (advisory, never gating)**: non-possessive proper-noun
    phrases extracted from the abstract prohibition sentences, filtered against allowed
    names/concepts.
- `scan_prose_for_leaks` / `scan_packet_prose` — word-boundary scan; multi-word terms
  case-insensitive, heuristically extracted single words case-sensitive ("no Eyes" must not flag
  "his eyes narrowed"); curly/straight-quote normalization; shorter matches subsumed by longer
  ones ("Eyes" inside "Neurochromatic Eyes" reports once). Findings carry
  `kind: canon_contract_leak`, term, source, `contract_reference`, excerpt, severity + the
  standard `blocks_*` gates.
- Derivation reads only authoritative body fields; the derived `_surface_contract` blob is
  ignored (it contains mechanically truncated entries like "Any on" that would poison a scan).

### Wiring

- **Chapter QA (surgical, declared — `production.py`)**: `run_chapter_draft_qa` gained an
  `open_questions` param and replaces the placeholder comment with a `scan_packet_prose` pass;
  `block` findings set the verdict to `block` (allowed: deterministic checks may hard-block).
  `assemble_run` now passes `approved_packet.open_questions`.
- **Scene QA (`scene_packet/qa.py`)**: `build_prefix` appends an
  `ON-PAGE PROHIBITED TERMS` block (`format_prohibited_terms_block`, hard tiers only), and
  `_SYSTEM` instructs QA to flag on-page packet fields staging such a term as
  `canon_contract_leak` — the prohibition now reaches scene-level calls as an explicit list, not
  buried ruling prose.

## Verification

`tests/test_ch1_canon_guard.py` (13 tests, pure, real fixture
`tests/fixtures/ch1_bad_run/chapter_packet.json`; "Neurochromatic Eyes" is hardcoded ONLY in
tests): (a) the exact missed leak sentence → `canon_contract_leak`, severity `block`, verdict
`block` through `run_chapter_draft_qa`; (b) ordinary game-UI prose ("scoreboard updated",
"health bar dipped"), lowercase "eyes", "eyeshadow", on-page "Serra" → zero findings; (c) a
synthetic later-chapter contract (Eyes ruling removed, concept allowlisted, canon lock kept) →
the same prose does NOT flag, proving locks alone never prohibit. Also covered: `[ interface ]`
and "Eyes of Meszkhal" flag; Sarah/Chad/Angelic Fortitude/Xylorane/Roth derived; scene-QA prefix
carries/omits the list correctly.

Gates: 13/13 new tests pass; existing pure `run_chapter_draft_qa` and scene-packet QA tests
pass; ruff check/format clean; pyright clean on all touched files. (One pre-existing
`test_production_runs.py` failure requires live Postgres and fails identically on the baseline.)

## Known limits / follow-ups

- Ruling extraction targets capitalized/bracketed concepts after an explicit negation; lowercase
  concept bans ("the full altana/kiss event stays off-limits") stay advisory-tier only.
- In-chapter timed surface terms (Serra pre-reveal) need position-aware scanning (scene-indexed
  spans) to enforce deterministically — natural lane 2/10 extension.
- The drafter prompt (`specialists/drafter.py`, not this lane's file) could also carry
  `format_prohibited_terms_block` next to its `_contract_block`.
