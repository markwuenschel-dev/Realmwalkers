# Roadmap

> **This file is no longer the live roadmap.** It is retained as a historical record, and because
> [`README.md`](../README.md) and [`BUILD.md`](BUILD.md) link to this path.
>
> The plan it used to carry predates every ADR in the 0028–0033 range and no longer describes what is
> being built or what has shipped. Rather than invent a replacement plan here, this file now points at
> the sources that are actually maintained.

## Where the live plan lives

| For | Read |
|---|---|
| **What the system is, and which subsystems are live** | [`CONTEXT.md`](../CONTEXT.md) — the glossary is the source of truth for domain terms, and its `Implementation status` blocks state what has landed on `main` and when |
| **Architecture decisions and what is authorized to be built** | [`docs/adr/`](adr/) — most recently ADR-0028 through ADR-0033. Newer ADRs carry an explicit `implementation_authorized` flag on line 3; **an ADR without that flag is not a work order** |
| **The forward plan, per destination** | The wayfinder **map issues** — [#213](https://github.com/markwuenschel-dev/Realmwalkers/issues/213) (ADR-0030 autonomy engine) and [#258](https://github.com/markwuenschel-dev/Realmwalkers/issues/258) (ADR-0028 imported-prose lifecycle tail) |
| **What is actually queued** | The open issues themselves — map issues are decisions-only and hand off to execution tickets |
| **Architecture rationale (stable, still current)** | [`DESIGN.md`](DESIGN.md), [`contract_first_drafting.md`](contract_first_drafting.md) |
| **How to build, test and deploy** | [`BUILD.md`](BUILD.md), [`DEPLOY.md`](DEPLOY.md) |

---

# Archived — superseded

Everything below this line is a historical record. **It is not a plan, and its claims are not
maintained.** When this file was archived on 2026-07-29, one claim below was measurably false and one
stated a status that cannot be supported; both are corrected inline so the record cannot be misread.

## Archived — repo scrub + drafting unblock (2026-07-03 plan)

> **Note (2026-07-03), as written at the time:** the plan archived below predates contract-first
> drafting. Drafting now runs chapter packets → scene packets → derived beats → draft, and beat-first
> drafting is disabled — the authoritative contract is
> [`contract_first_drafting.md`](contract_first_drafting.md).

- **Phase 1 — repo scrub (PR #138, merged):** path-aware CI (`changes` gate + `ci-passed`
  aggregator, cached builds), ~~test suite trimmed 579 → 215~~, worker modules 91 → 81 (`learning`
  promoted, legacy CLIs like `workers/enqueue` removed), stale-canon cleanup UI/API on the Ledger.

  > **Correction 2026-07-29 — the test-count figure is stale.** Measured at HEAD `033eb20`:
  > `find tests -name 'test_*.py' | wc -l` → **160** files, and
  > `grep -rc "^\s*def test_\|^\s*async def test_" tests --include='test_*.py'` summed → **1096**
  > test functions. Neither `579` nor `215` describes the suite today; the suite grew back by roughly
  > 5× after the trim. The trim itself happened — only the number is frozen at PR #138.

- ~~**Phase 2 — drafting unblock (in progress):**~~ a repair-severity tier so *repairable* packet
  issues become repair tasks instead of hard drafting blocks; one canonical packet artifact (instead
  of parallel packet representations); production-tab remediation UX so blockers are fixable from
  the Desk.

  > **Correction 2026-07-29 — "in progress" is not a supportable status.** This line was written
  > before ADR-0028 through ADR-0033 existed and has not been reconciled with them.
  > `docs/agent-briefs/phase2-drafting-unblock.md:256` already flagged this file as
  > *"predates contract-first packets — update or archive"*. **Whether these three items shipped was
  > not re-derived when this file was archived** — do not read the strikethrough as "done" or as
  > "abandoned". If the question matters, verify against `CONTEXT.md` and the production-repair
  > sources rather than trusting this line.

---

## Archived — Phase 3 + Writers' Desk live wiring (shipped)

The plan below is kept as a record; both workstreams landed. It predates the contract-first packet
flow, so its beat-first framing (approve beats → draft) no longer describes the shipped path.

Two workstreams, **sequenced Phase 3 first**, then **full-parity** wiring of the Writers' Desk to the
live API (no desk feature left on fixtures).

- The Writers' Desk (`frontend/src/desk/`, ported 1:1 from the prototype in PR #15) runs entirely on
  static fixtures (`desk/data.ts`) with local-only state — zero network calls. A complete typed client
  already exists at `frontend/src/desk/api/client.ts` (ported from the retired Vite review app).
- Phase 3 (DESIGN §14) is the last stubbed worker code: the three enrichment passes raise `PassError`
  and the per-lane review-lane reviewers were never written. The router + pipeline soft-fail plumbing
  already exists.

---

## Workstream 1 — Phase 3: enrichment passes + review lanes  ✅

DESIGN §14 Phase 3 / §15 OPEN-8 (lanes), OPEN-10 (partial-pass failure). **Done when:** enrichment
measurably reduces revision requests. One PR.

The router already maps tags→passes in fixed order and the pipeline lands the spine + a WARN flag on
`PassError` (`workers/pipeline.py:36-45`). Only the `run()` bodies and the reviewers are missing.

- [x] **1a. Enrichment passes** — `workers/specialists/{combat,sensory,dialogue}.py`. Replace each
  `PassError` stub with a transform modeled on `specialists/drafter.py` (LLM-call shape) and
  `reviewers/pacing.py` (token-gating). Each: `llm.complete(model=settings.enrich_model, …,
  budget=ctx.budget)` → the transformed full scene. Transform-only system prompt — deepen one
  dimension, preserve everything else, stay in `ctx.pov`, invent no canon, **preserve ```stat``` blocks
  verbatim** (the pipeline runs `render_stat_blocks` on the returned marker form, `pipeline.py:51`).
  Lanes: combat = fight choreography/spatial clarity/stat-consistent; sensory
  = concrete grounded sense detail; dialogue = voice/subtext, honoring
  `ctx.dialogue_rules` as authoritative.
- [x] **Failure contract:** let `BudgetExceeded` propagate (pipeline keeps the spine, aborts remaining
  passes); wrap any other exception and empty/degenerate output as `PassError` so the spine still lands
  flagged. `except BudgetExceeded: raise` / `except Exception as e: raise PassError(...) from e`.
- [x] **1b. Review lanes** — new `workers/reviewers/{combat,sensory,dialogue}.py` mirroring
  `reviewers/pacing.py`: token-gate on `_MIN_PROSE_CHARS`, `llm.complete(model=settings.review_model)`,
  parse via `reviewers/base.py` helpers (`parse_json_objects`, `advisory_severity`, `Flag`). Advisory
  only — INFO/WARN, never HARD, never mutate.
- [x] **1c. Router + config** — register the three lanes in `router.TAG_REVIEWERS` keyed by the same
  tags as the passes (`combat`, `sensory`, `dialogue`); `reviewers_for()` already merges
  onto `ALWAYS_REVIEWERS`. Add `enrich_model: str = "claude-sonnet-4-6"` to `shared/config.py`
  (generative → defaults to the draft model; separate knob to tune without code change).
- [x] **1d. Tests** — rewrite `tests/test_enrichment_passes.py` (currently asserts `PassError` with
  `ctx=None`): mock `llm.complete` (mirror `tests/test_drafter.py`); assert transformed prose, a
  preserved ```stat``` block, `PassError` on empty output, `BudgetExceeded` propagation. Add
  `tests/test_review_lanes.py` (mirror `test_reviewers_advisory.py`). Extend `tests/test_router.py`
  (`reviewers_for(["combat"])` includes the lane; `passes_for` order unchanged).
- [x] **1e. Docs** — flip Phase 3 → ✅ in `README.md` (build-phases + real/stubbed table), `BUILD.md`,
  and DESIGN §14; Phase 4 (`draft_ahead` + parallelism) becomes the only remaining stub.

---

## Workstream 2 — Writers' Desk → live API (full parity)  🟢 shipped

Mount stays `frontend/src/desk/`. `tokenize()` anchors markers by **substring** (`indexOf`,
`prose.ts`), so every inline marker (entity / conflict) is located client-side from a quote the
server supplies — no offset math.

**Shipped (de-mocked, terminal-free loop):** `desk/data.ts` is **deleted**; every screen reads live
data through `desk/api/client.ts` + the polling data layer `desk/api/data.tsx` (no react-query, no new
deps). New backend: `POST /jobs/draft-next` + `GET /jobs/status` (browser-driven worker, single-flight
background drain — drafting runs only when you act), `GET /books/{id}/characters`, `GET /books/{id}/canon`,
and full `Thread`/`ThreadBeat` CRUD. The Planner (Inbox) closes gate 1 in-browser: create book → outline
chapter → approve beats → draft. Approve/revise/resolve hit the API; revise + auto-advance auto-fire the
draft trigger. Verified: `tsc -b` clean; backend `ruff`/`mypy`/`pytest` (124) green.

**Deviations from the plan below (intentional):** used a polling data layer instead of `adapters.ts`;
no `title` columns added — scene/chapter labels are derived from the prose snippet; `Thread` beats are a
child `ThreadBeat` table, not JSONB; board drag-reorder dropped.

**Also shipped — the write-surfaces:** human-authored **Annotations** (quote-anchored margin notes)
and track-changes **Suggestions** (replace quote → new text; accept/reject; accepted ones fold into
`edited_prose` on approve, so they reach canon through the same human gate). Inline `anno`/`sugg`
markers render in the prose. Backend: `Annotation`/`Suggestion` models + the `markup` router.

**Continuity span:** `reviewers/continuity.py` now adds a deterministic `span` (char offsets of the
flagged value in the prose) and a sentence-scoped `context_sentence` fallback to the flag payload.
Inline `conflict` markers stay substring-anchored on `payload.prose_value` (per the no-offset-math
design); `span` is supplementary metadata + the conflict-card context is now reliably populated.

Original three-PR plan, with status:

### PR-A — data foundation + already-backed screens  ✅
- [x] `desk/api/client.ts` (+ self-contained `desk/api/types.ts`) and a polling data layer
  `desk/api/data.tsx` (replaces the proposed `adapters.ts`).
- [x] Fetch plumbing as a `DeskDataProvider` context: loading/error/empty states + selected-book context
  (defaults to first `GET /books`); polls `GET /jobs/status` and refreshes while drafting. Fixture imports
  removed screen-by-screen; `desk/data.ts` deleted.
- [x] No `title` columns added — `desk/lib/format.ts` derives a label from the prose snippet (falls back
  to `"Scene N"`). Avoids a schema change.
- [x] Wire (endpoints all exist): **Inbox** ← `GET /scenes/pending` (STATS computed client-side);
  **Scene core** ← `GET /scenes/{id}` (continuity rail from critiques whose payload has
  `prose_value`/`ledger_value`; Notes from non-continuity advisory critiques; Changes from
  `beat.expected_state_changes`; pipeline row from `passes_run` + per-reviewer severity — replaces the
  hardcoded arrays at `SceneScreen.tsx:194-240`), decision footer → `POST /scenes/{id}/decision`
  (+`feedback`/`edited_prose`), keep prose/ledger → `POST /scenes/{id}/continuity/resolve`;
  **Chapters** ← `GET /chapters?book_id=` + `/chapters/{id}/scenes` (board drag stays local-only —
  persisting order means renumbering `scene_no`); **Diff** ← `GET /scenes/{id}/versions` reusing
  `desk/lib/diff.ts:lineDiff` (adapter pairs del+add into `"change"` rows); **Manuscript** ←
  `GET /books/{id}/manuscript`.

### PR-B — read surfaces + Ledger + entity cards  ✅
- [x] `GET /books/{id}/characters` ← `CharacterState.stats_json` (+ canon body + `is_pov`) → entity
  hover-cards + Ledger "Characters".
- [x] `GET /books/{id}/canon?kind=location|item|…` ← `CanonEntity` → Ledger sections + counts.
- [x] Wired `LedgerScreen` + Scene entity hover-cards (`makeCard` "entity"); entity markers assembled
  client-side from character names present in the prose.

### PR-C — write surfaces: Threads, Annotations, Suggestions, continuity spans  ✅
New models in `shared/models.py` (rerun `init_db.py`). These are net-new persistent domain concepts not
in DESIGN today — proposed here, to fold into DESIGN §3/§15 once settled.
- [x] **`Thread` + `ThreadBeat`** (a child beats table rather than JSONB) → `GET`/`POST /books/{id}/threads`,
  `PUT`/`DELETE /threads/{id}`, `POST /threads/{id}/beats`. Backs Ledger "Threads" (curatable from the UI).
- [x] **`Annotation`** (scene_id, version, quote, author, note) → `GET`/`POST /scenes/{id}/annotations`,
  `DELETE /annotations/{id}`. Backs margin notes (reading-view gutter) + inline `anno` markers.
- [x] **`Suggestion`** (scene_id, version, quote, new_text, author, why, status) →
  `GET`/`POST /scenes/{id}/suggestions`, `POST /suggestions/{id}/decision`, `DELETE`. Backs the
  suggesting mode (accept/reject) + inline `sugg` track-changes; accepted ones fold into `edited_prose`
  on approve (`desk/lib/format.ts:applyAcceptedSuggestions`).
- [x] **Continuity span:** `reviewers/continuity.py` now emits `span` ([start,end] char offsets, located
  deterministically) + a sentence-scoped `context_sentence` fallback. Inline `conflict` markers remain
  substring-anchored on `payload.prose_value`; `span` is supplementary.
- [x] `desk/data.ts` retired (deleted). Per-paragraph marker adapter ships entities, conflict spans,
  annotation quotes, and suggestion old-text via `tokenize` substring anchoring.

---

## Reuse (don't reinvent)
- Pass/reviewer templates: `specialists/drafter.py`, `reviewers/pacing.py`; helpers in `reviewers/base.py`.
- Pipeline soft-fail (OPEN-10) + router are already wired — passes/lanes just slot in.
- Frontend: `desk/api/client.ts`, `desk/api/types.ts`, `desk/lib/diff.ts` (`lineDiff`),
  `desk/prose.ts` (`tokenize` substring anchoring). Continuity payload already matches the desk conflict card 1:1.

## Verification
- **Phase 3:** `pytest -q`, `ruff check src tests`, `pyright`. End-to-end: enqueue a beat tagged
  `combat`/`dialogue`, `python -m dominion.workers.worker --once` (needs `ANTHROPIC_API_KEY` + Postgres),
  confirm `scene.passes_run` includes the lane and a lane critique appears.
- **Frontend:** `cd frontend && npm run build` after each PR. Manual: API on :8000 + `npm run dev` on
  :5173; seed via `seed_continuity_demo.py`; confirm Inbox/Scene/Diff/Manuscript/Ledger/entity-cards
  render live data and approve/revise/resolve hit the API.

## Notes / deferred
- Migrations are `create_all` (additive); no Alembic — production migration out of scope (matches the
  `scripts/init_db.py` note). New tables require rerunning `scripts/init_db.py`.
- Board drag-reorder and per-chapter word `target` stay local/derived (no ordering/target model).
- Threads are authored/curated, not auto-derived; PR-C ships read + basic write, not auto-population.
