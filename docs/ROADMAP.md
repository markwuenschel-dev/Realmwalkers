# Roadmap — Phase 3 + Writers' Desk live wiring

Execution roadmap for the current effort. The spec is [`DESIGN.md`](DESIGN.md) (esp. §14 phased build,
§15 decisions log); the dev guide is [`BUILD.md`](BUILD.md). This file is the forward-looking plan and
the place to check work off — it does not redefine the architecture.

Two workstreams, **sequenced Phase 3 first**, then **full-parity** wiring of the Writers' Desk to the
live API (no desk feature left on fixtures).

- The Writers' Desk (`frontend/src/desk/`, ported 1:1 from the prototype in PR #15) runs entirely on
  static fixtures (`desk/data.ts`) with local-only state — zero network calls. A complete typed client
  already exists for the review app at `frontend/src/legacy/api/client.ts`.
- Phase 3 (DESIGN §14) is the last stubbed worker code: the three enrichment passes raise `PassError`
  and the per-lane review-lane reviewers were never written. The router + pipeline soft-fail plumbing
  already exists.

---

## Workstream 1 — Phase 3: enrichment passes + review lanes  ⏳

DESIGN §14 Phase 3 / §15 OPEN-8 (lanes), OPEN-10 (partial-pass failure). **Done when:** enrichment
measurably reduces revision requests. One PR.

The router already maps tags→passes in fixed order and the pipeline lands the spine + a WARN flag on
`PassError` (`workers/pipeline.py:36-45`). Only the `run()` bodies and the reviewers are missing.

- [ ] **1a. Enrichment passes** — `workers/specialists/{combat,sensory,dialogue}.py`. Replace each
  `PassError` stub with a transform modeled on `specialists/drafter.py` (LLM-call shape) and
  `reviewers/pacing.py` (token-gating). Each: `llm.complete(model=settings.enrich_model, …,
  budget=ctx.budget)` → the transformed full scene. Transform-only system prompt — deepen one
  dimension, preserve everything else, stay in `ctx.pov`, invent no canon, **preserve ```stat``` blocks
  verbatim** (the pipeline runs `render_stat_blocks` on the returned marker form, `pipeline.py:51`).
  Lanes: combat = fight choreography/spatial clarity/stat-consistent; sensory (tag
  `physical_description`) = concrete grounded sense detail; dialogue = voice/subtext, honoring
  `ctx.dialogue_rules` as authoritative.
- [ ] **Failure contract:** let `BudgetExceeded` propagate (pipeline keeps the spine, aborts remaining
  passes); wrap any other exception and empty/degenerate output as `PassError` so the spine still lands
  flagged. `except BudgetExceeded: raise` / `except Exception as e: raise PassError(...) from e`.
- [ ] **1b. Review lanes** — new `workers/reviewers/{combat,sensory,dialogue}.py` mirroring
  `reviewers/pacing.py`: token-gate on `_MIN_PROSE_CHARS`, `llm.complete(model=settings.review_model)`,
  parse via `reviewers/base.py` helpers (`parse_json_objects`, `advisory_severity`, `Flag`). Advisory
  only — INFO/WARN, never HARD, never mutate.
- [ ] **1c. Router + config** — register the three lanes in `router.TAG_REVIEWERS` keyed by the same
  tags as the passes (`combat`, `physical_description`, `dialogue`); `reviewers_for()` already merges
  onto `ALWAYS_REVIEWERS`. Add `enrich_model: str = "claude-sonnet-4-6"` to `shared/config.py`
  (generative → defaults to the draft model; separate knob to tune without code change).
- [ ] **1d. Tests** — rewrite `tests/test_enrichment_passes.py` (currently asserts `PassError` with
  `ctx=None`): mock `llm.complete` (mirror `tests/test_drafter.py`); assert transformed prose, a
  preserved ```stat``` block, `PassError` on empty output, `BudgetExceeded` propagation. Add
  `tests/test_review_lanes.py` (mirror `test_reviewers_advisory.py`). Extend `tests/test_router.py`
  (`reviewers_for(["combat"])` includes the lane; `passes_for` order unchanged).
- [ ] **1e. Docs** — flip Phase 3 → ✅ in `README.md` (build-phases + real/stubbed table), `BUILD.md`,
  and DESIGN §14; Phase 4 (`draft_ahead` + parallelism) becomes the only remaining stub.

---

## Workstream 2 — Writers' Desk → live API (full parity)  ⬜

Mount stays `frontend/src/desk/`. `tokenize()` anchors markers by **substring** (`indexOf`,
`prose.ts:50`), so every inline marker (entity / conflict / annotation / suggestion) is located
client-side from a quote the server supplies — no offset math. Suggested as three PRs.

### PR-A — data foundation + already-backed screens  ⬜
- [ ] `desk/api/client.ts` (reuse `legacy/api/client.ts` + DTOs `legacy/types.ts`) + `desk/api/adapters.ts`
  (API DTO → `desk/types.ts` view-models).
- [ ] Fetch plumbing in `desk/state.ts`: lightweight `useEffect`/`useState` hooks (no new deps; project
  has no react-query) with loading/error/empty states + a selected-book context (API is multi-book;
  default to first `GET /books`). Replace fixture imports screen-by-screen.
- [ ] Schema: add nullable `title` to `Chapter` + `Scene` (`shared/models.py` + DTOs); client falls back
  to `"Chapter N"`/`"Scene N"`. Needed by several screens. Rerun `scripts/init_db.py`.
- [ ] Wire (endpoints all exist): **Inbox** ← `GET /scenes/pending` (STATS computed client-side);
  **Scene core** ← `GET /scenes/{id}` (continuity rail from critiques whose payload has
  `prose_value`/`ledger_value`; Notes from non-continuity advisory critiques; Changes from
  `beat.expected_state_changes`; pipeline row from `passes_run` + per-reviewer severity — replaces the
  hardcoded arrays at `SceneScreen.tsx:194-240`), decision footer → `POST /scenes/{id}/decision`
  (+`feedback`/`edited_prose`), keep prose/ledger → `POST /scenes/{id}/continuity/resolve`;
  **Chapters** ← `GET /chapters?book_id=` + `/chapters/{id}/scenes` (board drag stays local-only —
  persisting order means renumbering `scene_no`); **Diff** ← `GET /scenes/{id}/versions` reusing
  `legacy/lib/diff.ts:lineDiff` (adapter pairs del+add into `"change"` rows); **Manuscript** ←
  `GET /books/{id}/manuscript`.

### PR-B — read surfaces + Ledger + entity cards  ⬜
- [ ] `GET /books/{id}/characters` ← `CharacterState.stats_json` (+ optional role) → entity hover-cards
  + Ledger "Characters".
- [ ] `GET /books/{id}/canon?kind=location|item|…` ← `CanonEntity` → Ledger "Locations"/"Items" + counts.
- [ ] Wire `LedgerScreen` + Scene entity hover-cards (`makeCard` "entity", `SceneScreen.tsx:43`); entity
  markers assembled client-side from character names present in the prose.

### PR-C — write surfaces: Threads, Annotations, Suggestions, continuity spans  ⬜
New models in `shared/models.py` (rerun `init_db.py`). These are net-new persistent domain concepts not
in DESIGN today — proposed here, to fold into DESIGN §3/§15 once settled.
- [ ] **`Thread`** (book_id, name, kind, state, note, `beats` JSONB = `[{scene_no,label,flag}]`) →
  `GET /books/{id}/threads` (+ `POST`/`PUT` curation). Backs Ledger "Threads" + thread map.
- [ ] **`Annotation`** (scene_id, version, quote, author, note, created_at) → `GET/POST/DELETE
  /scenes/{id}/annotations`. Backs Notes-tab margin notes + inline `anno` markers (quote-anchored).
- [ ] **`Suggestion`** (scene_id, version, `old`/quote, new_text, author, why, status
  pending|accepted|rejected) → `GET`/`POST /scenes/{id}/suggestions`, `POST /suggestions/{id}/decision`.
  Backs Changes/suggesting mode + inline `sugg` markers.
- [ ] **Continuity span:** extend `reviewers/continuity.py` to add `span` + `context_sentence` to the
  flag payload (the `Critique.payload` docstring already promises these, `models.py:158`). Backs inline
  `conflict` markers + the conflict-card context.
- [ ] Wire suggesting mode (accept/reject), margin notes (create/select), and the full per-paragraph
  marker adapter (entities + conflict spans + annotation quotes + suggestion old-text via `tokenize`).
  Retire `desk/data.ts`.

---

## Reuse (don't reinvent)
- Pass/reviewer templates: `specialists/drafter.py`, `reviewers/pacing.py`; helpers in `reviewers/base.py`.
- Pipeline soft-fail (OPEN-10) + router are already wired — passes/lanes just slot in.
- Frontend: `legacy/api/client.ts`, `legacy/types.ts`, `legacy/lib/diff.ts` (`lineDiff`),
  `desk/prose.ts` (`tokenize` substring anchoring). Continuity payload already matches the desk conflict card 1:1.

## Verification
- **Phase 3:** `pytest -q`, `ruff check src tests`, `mypy src`. End-to-end: enqueue a beat tagged
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
