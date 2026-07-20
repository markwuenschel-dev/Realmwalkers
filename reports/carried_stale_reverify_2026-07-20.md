# Carried-stale re-verification — 2026-07-20

Re-verification of the 8 **carried-stale** items from the 2026-07-18 integrity audit
(items that were open as of 2026-07-17 but *not* re-examined by the 07-18 sampling swarm).
Each was re-checked against `main @ 21e02d8` (38 commits after the 07-17 baseline) by an
independent read-only agent that re-located current `file:line`, ran absence/reader
searches, and re-scored. Line numbers below are current unless noted.

**Headline:** all 8 are still live — **7 STILL-OPEN, 1 PARTIALLY-RESOLVED** (TWIN-PARITY).
None were silently resolved by the 38 intervening commits, though several had their
*secondary* concerns partially addressed. Priorities on the report's 0–19 scale.

| # | ID | Status | Pri (was→now) | Cat | Action | What's left |
|---|----|--------|---------------|-----|--------|-------------|
| 1 | IMP-EVID | STILL-OPEN | 12 → 12 | Testing | fitness-check | 3 DB/LLM-free pure fns + idle fake, zero tests — **and no importer anywhere** (dead pending a caller) |
| 2 | CTRL-PLANE | STILL-OPEN | 11 → 10 | Testing | fitness-check | autonomy/policy/preset + distill→RuleProposal + beats/markup/threads all untested; HTTP-HARNESS now unblocks |
| 3 | TWIN-PARITY | PARTIALLY-RESOLVED | 12 → 9 | Maintainability | fitness-check | 2 of 3 twins now parity-anchored; `draft_queue` prefetched twin (readiness-vs-schedule) is the remnant |
| 4 | TELEM-AGG | STILL-OPEN | 9 → 9 | Architecture | design | ~180 LOC SQL rollups+joins still in the router; TELEM-ORACLE now pins output for a safe extraction |
| 5 | PKT-PIPE | STILL-OPEN | 10 → 9 | Architecture | design | canonicalize pipeline hand-typed twice, steps 2/3 swapped, no parity test |
| 6 | BEAT-NOUN | STILL-OPEN (latent) | 10 → 7 | Bug | fitness-check | leading-name leak is real (traced) but unreached — all 25 real beats are directive-led |
| 7 | MIG-DRIFT | STILL-OPEN | 7 → 7 | Data/Schema | fitness-check | **forward-drift gate still absent: green CI / red prod.** The silent one |
| 8 | ADR28-SUB | STILL-OPEN | 4 → 4 | Wiring | triage | write-dead ADR-0028 scaffolding; one inert reader appeared; wire-vs-delete owner call |

**How to read it beyond raw priority:**
- **Quick, provable now:** IMP-EVID (3 pure fns), TWIN-PARITY (1 parity test), BEAT-NOUN (1-line + test).
- **Unblocked by HTTP-HARNESS:** CTRL-PLANE — the highest-*risk* item (autonomy safety boundary), now trivially reachable.
- **Design pass first:** TELEM-AGG, PKT-PIPE.
- **Sleeper:** MIG-DRIFT — low priority number, but the only item that fails **silently in production** (passes CI, throws `UndefinedColumn` in prod).
- **Owner decision:** ADR28-SUB — wire-vs-delete; the new inert reader mildly favors "wire, in progress."
- **Cross-link:** IMP-EVID and ADR28-SUB are the same not-yet-wired import-adoption slice from two ends — `import_evidence.py` produces a `ValidatedEvidence` dataclass that nothing persists, and `ImportSceneEvidence`/`ImportAdoption` have zero constructors.

---

## 1. IMP-EVID — STILL-OPEN (pri 12)

**Zero test coverage confirmed; and the module has no importer anywhere in the repo — dead pending a caller.**

- **CAT/ACTION:** Testing/Fixture/Golden · fitness-check
- **LOC:** `src/dominion/workers/import_evidence.py` — pure fns `validate_ledger:90`, `_deterministic_chunks:172`, `_merge_chunk_ledgers:195`; Protocol `ImportEvidenceExtractor:123`; idle `FakeImportEvidenceExtractor:130-169`; `LlmImportEvidenceExtractor:229`. (Line numbers unchanged from 07-17 — file untouched since.)
- **SYM:** Extracts a span-anchored fact ledger ("what a scene's prose already establishes") during import adoption. Three DB/LLM-free pure functions and a deterministic fake (built "so CI can prove checkpoint/resume, retry, stale invalidation… without a provider," docstring:8) sit entirely unexercised.
- **SEAM:** the evidence-extraction stage of import adoption (ADR-0028) — the pure `SceneSource → ValidatedEvidence` adapter boundary.
- **RULE:** a shipped, purpose-built test double must be wired into a test; import adoption is untested at this layer.
- **EV (FACT, this pass):** grep of `tests/` for every public name → 0 matches (139 test files swept). Repo-wide grep for any importer (`from dominion.workers.import_evidence`, `LlmImportEvidenceExtractor(`, `FakeImportEvidenceExtractor(`) → 0 — only config/registry/model/migration *declarations* name it (config.py:94-96,289; agent_registry.py:79,276-281; models.py:427/439; migrations.py:260-269). `git log --since=2026-07-17` on the file → empty.
- **CHK:** `tests/test_import_evidence.py` — unit-test the three pure fns directly (section-fill/coercion/out-of-range for `validate_ledger`; boundary cuts + full coverage for `_deterministic_chunks`; span offset-shift + entry/exit-state merge for `_merge_chunk_ledgers`), then drive `FakeImportEvidenceExtractor` for determinism/retry/`ValidatedEvidence` shape. *Integration test of the adoption caller is blocked — no caller exists yet.*
- **SCORES:** s4 c5 l4 lo4 te5 br4 rr4 hd2
- **NOTE:** finding is **broader** than the old card — no importer anywhere, not just no tests. Pure-fn tests are actionable now; the "wire the fake into the adoption caller" half is un-actionable until the caller is built (see ADR28-SUB).

## 2. CTRL-PLANE — STILL-OPEN (pri 11 → 10)

**Every control-plane mutation surface still untested; HTTP-HARNESS added only a read-only `GET /settings/models` smoke and now makes the mutation/422 paths trivially reachable.**

- **CAT/ACTION:** Testing/Fixture/Golden · fitness-check
- **LOC (current):** `api/routers/settings.py` — `set_autonomy PUT /autonomy:157` (422 human_required/D16 guard :160-165) **uncovered**; `set_agent_policy:109` uncovered; preset CRUD `apply_preset:73`/`save_custom_preset:82`/`delete_custom_preset:91` uncovered; `set_agent_globals:100` endpoint uncovered (its helper `agent_ops.apply_globals` IS covered directly — separate GLOBALS finding); `get_models:39` **now covered** (read-only, not a risk surface). `api/routers/learning.py` — `distill_rules:28`, `list_rule_proposals:83`, `decide_rule_proposal:109` uncovered. `workers/learning/distill.py` — `propose_rules:110` etc. uncovered. `beats.py`, `markup.py`, `threads.py` — all handlers uncovered.
- **SYM:** the HTTP surface that writes the autonomy ceiling / per-agent policy (the A1b/A1c/D16 safety boundary) and the autonomous distill→RuleProposal pipeline (self-modifies each POV's `voice_spec`) still have no direct-call or HTTP test. A bug in the ceiling 422-guard, a policy/preset mutation, or distill's coercion/dedupe ships silently.
- **SEAM:** the settings/autonomy control plane + the autonomous rule-learning pipeline (human-gated, but proposal *generation* is unchecked).
- **RULE:** the highest-risk modules (they gate autonomy and self-modify the rule base) have no matching test.
- **EV (FACT, all searches over `tests/`):** `distill|RuleProposal|propose_rules` → 0. `set_autonomy|set_agent_policy|apply_preset|...|routers.settings` → 1 hit (a docstring mention only). beats/markup/threads handler names + `routers.(beats|markup|threads)` → 0. `app_client` paths = `/health,/books,/activity,/scenes/pending,/jobs/status,/settings/models,/chapters` only. Names-lie **confirmed**: `test_learning.py` imports `routers.reviews/scenes` + the config singleton, scopes itself to EditPair capture, never imports the learning router or distill.
- **CHK:** `tests/test_control_plane.py` on the `app_client` fixture (conftest.py:217): (1) `PUT /settings/autonomy` ceiling=`human_required` → assert 422 (settings.py:160-165) + valid write round-trips; (2) policy happy + unknown-setting 422; (3) preset create→apply→delete; (4) distill: seed EditPairs, monkeypatch `llm.complete` to canned JSON, `POST …/distill` → assert RuleProposal rows + dedupe, then `…/decision` accept appends to `PovProfile.voice_spec`; (5) one write+read per beats/markup/threads.
- **SCORES:** s5 c5 l4 lo4 te5 br5 rr5 hd2
- **NOTE:** 11→10 only because HTTP-HARNESS lowered effort to near-trivial and removed the "is the settings router even wired" doubt. Severity/blast-radius unchanged. The card's "zero coverage" claim is still accurate for every listed surface.

## 3. TWIN-PARITY — PARTIALLY-RESOLVED (pri 12 → 9)

**1 of 3 twins now behaviorally parity-guarded; the `draft_queue` prefetched resolver and the per-call `_resolve_links` twin remain hand-duplicated with an entirely untested sibling.**

- **CAT/ACTION:** Maintainability · fitness-check
- **LOC (current):** `workers/draft_queue.py:139-195` `resolve_approved_scene_packet_for_beat` (DB) vs `:198-249` `_prefetched` (docstring:204 "Read-only twin… Same decision tree"). `api/routers/telemetry.py:80-81` `_group` vs `:104-117` `_group_rows` (docstring:105 "SQL twin"). `telemetry.py:137-182` `_resolve_links` vs `:185-229` `_links_for_calls` (docstring:186 "Batched").
- **SYM:** each decision tree retyped by hand per performance variant. Live divergence risk concentrates in draft_queue: the prefetched twin drives `GET /draft/readiness` while the DB twin drives scheduling — drift shows a beat "ready" that scheduling then blocks, silently.
- **SEAM:** missing a pure decision-core with two thin data-access adapters + a head-to-head parity test per pair.
- **EV (FACT):** `_prefetched` searched repo-wide → only draft_readiness.py + def, **0 test refs**; `test_draft_queue.py` exercises only the DB twin. `_resolve_links` callers (`list_llm_calls`/`llm_call_detail`) → **0 tests**. `_group_rows` **is** parity-anchored by `test_book_telemetry_sql_rollups_match_python_reference` (test_telemetry_api.py:110-176). `_links_for_calls` **is** behaviorally tested (test_telemetry_api.py:206-218) but with no head-to-head against `_resolve_links`.
- **CORRECTION to old card:** its blanket "no test references the batch/SQL variant of any pair" is now **partly false** — `_links_for_calls` and `_group_rows` gained coverage. Still true for the draft_queue prefetched twin and `_resolve_links`.
- **CHK:** two head-to-head parity tests: (1) seed beats+packets, assert `resolve_…_for_beat` vs `_prefetched` return the same packet-or-blocker across valid/missing-scene_no/no-approved/duplicate/stale branches; (2) assert `{c.id:_resolve_links(...)}` == `_links_for_calls(...)` field-for-field.
- **SCORES:** s3 c5 l4 lo3 te5 br3 rr3 hd2
- **NOTE:** the `production_repair.py`→`production_fidelity.py` split did not touch these; API-router telemetry line numbers unchanged.

## 4. TELEM-AGG — STILL-OPEN (pri 9)

**Nothing extracted since 07-17; the router's SQL-aggregation block is byte-for-byte at the audited lines. The only delta is a test-only oracle that now pins that behavior.**

- **CAT/ACTION:** Architecture Fitness · design
- **LOC (current):** `src/dominion/api/routers/telemetry.py` — `_agg_cols():84-101` (func.count / coalesce(sum)×4 / count().filter×3 …), `_group_rows():104-117`, `_resolve_links()/_links_for_calls():137-229` (per-call + batched DISTINCT-ON), `_apply_call_filters():267-318`, `book_telemetry():349-529` (7 inline `select(…, *_agg_cols()).group_by(…)` rollups + an `editorial_rows .join(ProductionRun)`), `compare_runs()/_run_rows():677-752`. Host module `workers/telemetry_agg.py` imported at :49-59.
- **SYM:** ~180 LOC of SQL aggregate/group-by + ~90 LOC of multi-query FK-join resolution live in the HTTP router. The worker was given the consuming reducer (`totals_from_model_rows`, `_totals`, `group_calls`) but not the producing side — one logical unit (build-rows → reduce-rows) is split across the router/worker seam.
- **RULE:** routers should translate HTTP↔worker, not construct SQL aggregations/joins.
- **EV (FACT):** read telemetry.py (753 lines) + telemetry_agg.py (194 lines) in full; worker defs are all Python per-row reducers (zero `select(`/`func.`/`.group_by`). `git log --since=2026-07-16` on both paths → no commits. `git show --stat 0b2b2ec` (TELEM-ORACLE) = tests only (+36).
- **CHK:** grep convention flagging `func.`/`.group_by(`/`.join(` inside `api/routers/**` (name aggregation/group-by specifically so `_apply_call_filters`/`_resolve_links` aren't false-positives). The TELEM-ORACLE literal oracle already pins the rollup output, making the extraction safely verifiable.
- **SCORES:** s3 c5 l3 lo2 te3 br2 rr3 hd3

## 5. PKT-PIPE — STILL-OPEN (pri 10 → 9)

**The 4-call canonicalize pipeline is still hand-typed twice with `build_surface_contract` fed a different-shaped input and steps 2/3 reordered; no seam, no facade `__all__`, no parity test.**

- **CAT/ACTION:** Architecture Fitness · design
- **LOC (current):** Propose (`workers/packet/__init__.py`): `evaluate_chapter_packet_internal:377` → `build_surface_contract(internal):405` → `to_master_packet:437` → attach `_surface_contract:454` → `validate_master_packet:458`. Update (`api/routers/packets.py`): `evaluate…:119` → `to_master_packet:120` → `build_surface_contract(canonical):127` → attach:128 → validate:132. Router comment:116-118 still calls it "Same deterministic pipeline as propose."
- **SYM:** the two sequences swap steps 2/3 — propose derives the surface projection from the pre-canonical body then canonicalizes; update canonicalizes first then derives from the canonical body. Safe only if `build_surface_contract` output is insensitive to whether its input went through `to_master_packet` — an implicit, unpinned cross-pipeline invariant. `packet/__init__.py` never became a facade (no `__all__`), so packets.py re-imports 4 internal submodules and re-assembles the pipeline.
- **SEAM:** no `run_deterministic_pipeline(body)` single seam (grep across src → 0 hits).
- **EV (FACT):** read both sequences end-to-end and enumerated step order; no `__all__` (read all 577 lines); no single seam (grep); no parity test (only update-path test asserts seed-id minting, not surface-contract equality). **New:** `to_master_packet` idempotence is now documented (master.py:30-32) + pinned (test_master_packet.py:109-112) — but that pins `to_master∘to_master==to_master`, **not** `build_surface_contract(x)==build_surface_contract(to_master(x))`, the property this finding needs.
- **CHK:** parity test extending test_packet_derive.py:116-133 — propose a packet, capture `_surface_contract` + violations; PUT the byte-identical body via `update_packet`; assert equal.
- **SCORES:** s4 c4 l3 lo3 te4 br3 rr4 hd2
- **NOTE:** the router comment now documents the divergence it denies.

## 6. BEAT-NOUN — STILL-OPEN but LATENT (pri 10 → 7)

**Code byte-identical to 07-17; a name-first beat still leaks its leading name (proven by execution), but the leak is unreachable in the real corpus.**

- **CAT/ACTION:** Bug · fitness-check
- **LOC (current):** `workers/scene_scope.py:160-167` `_beat_tokens` (`is_proper = index>0 and token[0].isupper()` at :166 — index-0 capitalized token never marked proper) and `:170-189` `beat_keywords` (the `if index==0:` branch at :183-185 is still a no-op `pass`). Live readers: `evaluate_scene_scope:367-377` ← `production_sequence.py:565`, feeding `verdict` block/warn.
- **SYM (FACT by execution):** `_beat_tokens("Marcus arrives at the tavern")` → `marcus` at index 0 carries `is_proper=False`; `beat_keywords(...)` → `['marcus','arrives','tavern']`, so `marcus` **is** emitted. The directive form "Show Marcus…" still filters Marcus. **New FACT refining the prior INF:** all 25 real `beat_ownership` fixture entries are directive-first; the 10 distinct leading tokens (Show/Have/Use/Let/Introduce/End/Bridge/Set/Keep/Establish) are all in `_DIRECTIVE_VERBS`, so a name never lands at index 0 — the leak never fires on real data. The old card's "inflates false positives in nearly every scene" **overstates**: latent, not active.
- **RULE:** a stated invariant ("names carry no scope signal") not enforced at the one position where the capitalization heuristic has no signal; correctness rests on an unenforced authoring convention.
- **EV:** FACT (executed the trace), FACT (readers live), FACT (all 25 fixture beats directive-led). Existing `test_beat_keywords_derived_from_beat_text` (test_scene_scope_bleed.py:80-87) asserts `"marcus" not in keywords` only for a **mid-sentence** Marcus — the leading-name case is uncovered.
- **CHK:** `beat_keywords("Marcus arrives at the tavern")` should exclude `"marcus"`; today returns `['marcus','arrives','tavern']`.
- **SCORES:** s2 c4 l2 lo4 te4 br2 rr2 hd2
- **NOTE:** 10→7: mechanism now proven (c 3→4) but reachability drops (l 3→2). Real bug only if a beat is ever authored name-first; one-line fix if pursued.

## 7. MIG-DRIFT — STILL-OPEN (pri 7)

**The forward-drift gate still does not exist; the parity guard remains stale-direction-only and structurally cannot see a model column missing from `_COLUMN_ADDS`.**

- **CAT/ACTION:** Data/Schema/Contract · fitness-check
- **LOC (current):** `shared/migrations.py:24-111` `_COLUMN_ADDS`, `:115-161` `_BACKFILLS`, `:164-270` `_EXTRA_DDL` vs `shared/models.py`; guard `tests/test_migration_column_parity.py:31-42` (iterates `_COLUMN_ADDS`→metadata only; forward gap self-disclaimed :8-11); `conftest.py:179`+`:182` (fresh `create_all` once per run) + `scripts/init_db.py:21`/`:24` (`create_all` never ALTERs).
- **SYM:** add a nullable column to an existing model, forget the `_COLUMN_ADDS` line: `create_all` still builds it on the fresh test DB, so every test passes and CI is green, while persistent prod Postgres never gets it → `UndefinedColumnError` **in prod only**.
- **SEAM:** ORM model (`Base.metadata`) vs hand-maintained DDL list, verified against a fixture (`create_all`'d fresh) that structurally cannot observe forward drift because it reflects the model, not the persisted schema.
- **EV (FACT):** read migrations.py (all 3 lists + `apply_lightweight_migrations`), the parity test (stale-only + disclaimer), conftest ordering, init_db. Search `_COLUMN_ADDS|snapshot|forward.drift` across tests → only the stale-only guard; glob `*schema*snapshot*` → none (no baseline to diff).
- **CHK:** persist a per-release `Base.metadata` snapshot; fail when a current model column is neither in the last snapshot nor a `_COLUMN_ADDS` entry since. Plus a data-bearing test per remaining `_BACKFILLS` entry (canon source/status, books series/book_no still uncovered).
- **SCORES:** s5 c5 l4 lo2 te3 br5 rr4 hd3
- **NOTE:** GLOBALS resolved as a point-instance (migrations.py:81) but that's not the structural gate. TEST-DB-SKIP is **orthogonal** — fail-loud DB tests still run against a fresh `create_all` DB where every model column exists. `_BACKFILLS` now has 1 string-parity test (not data-bearing); `_EXTRA_DDL` has existence tests for 2 of ~35 indexes. Core claim unchanged, re-confirmed FACT. **This is the only item that fails silently in production.**

## 8. ADR28-SUB — STILL-OPEN (pri 4)

**All 4 symbols still have zero writers/constructors; substrate is still write-dead scaffolding. One inert reader for RevisionRequest appeared — it reads rows nothing inserts, gated on an FK nothing sets.**

- **CAT/ACTION:** Wiring/Integration · triage (owner wire-vs-delete decision)
- **LOC (current):** `shared/chapter_lock.py:60` `acquire_chapter_workflow_lock` — **0 external callers** (0 importers). `shared/models.py:388` `ImportAdoption` — 0 constructors/readers/tests. `:427` `ImportSceneEvidence` — 0 constructors/readers/tests (import_evidence.py produces a `ValidatedEvidence` dataclass, "never touches the DB" — does **not** persist this model). `:454` `RevisionRequest` — 0 constructors/writers; **1 reader** at `workers/context/revision.py:26` (`session.get(RevisionRequest, job.revision_request_id)`, reachable via assemble.py:30), 0 tests.
- **SYM:** no mutation/insert path constructs any of the four, and no path takes the chapter lock. RevisionRequest's new reader is gated on `Job.revision_request_id`, which is **never assigned anywhere** (only column def + reader + migration DDL), so the branch is always False on real data — every real revise job falls through to the legacy Approval branch.
- **RULE:** reachability — write-dead scaffolding for a not-yet-wired slice vs forgotten dead code; owner call. The new inert reader mildly favors "wire, in progress."
- **EV (FACT):** `acquire_chapter_workflow_lock(` → 2 self-hits only; `from …chapter_lock` → 0. `ImportAdoption(`/`ImportSceneEvidence(`/`RevisionRequest(` → 1 hit each = the class def, 0 in tests. `revision_request_id` → column def + reader + migration DDL, **no assignment** anywhere. **INF:** consistent with staged ADR-0028 decomposition; ADR-0031 still asserts "The ADR-0028 layer is inert."
- **CHK:** skip/xfail tests flipped on when wiring lands: (1) a real revise path SETS `job.revision_request_id`; (2) `acquire_chapter_workflow_lock` taken on a mutation path; (3) an `ImportAdoption`/`ImportSceneEvidence` row constructed. No concurrency test until ≥1 real writer/lock call site exists.
- **SCORES:** s2 c5 l3 lo3 te3 br5 rr2 hd5
- **NOTE:** old card's blanket "zero callers" is now strictly false for RevisionRequest (one inert reader), but nothing was truly wired.

---

*Method: 8 independent read-only agents at `main @ 21e02d8`, 2026-07-20. Each re-located
current `file:line`, ran absence/reader searches, distinguished FACT (read/executed this pass)
from INF (reasoned), and re-scored on the 07-18 report's axes. Not an exhaustive re-scan —
this is a targeted re-verification of the 8 previously-tracked carried-stale items only.*
