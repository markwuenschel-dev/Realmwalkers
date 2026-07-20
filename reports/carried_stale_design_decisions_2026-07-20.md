# Carried-stale swarm — design decisions (2026-07-20)

Four of the eight carried-stale items could not be cleanly executed autonomously — each hinges on
a fork only the owner should settle. Read-only design agents (HEAD `21e02d8`) produced the options
below. Each ends with the single question you need to answer and whether it becomes a mechanical
swarm task once decided.

The other four items (IMP-EVID, TWIN-PARITY, CTRL-PLANE, PKT-PIPE) were executed as additive tests
and are verified green — see the batch summary at the end.

---

## 1. BEAT-NOUN — guard the name-first leak, or accept it?

**Why it's a decision, not a fix:** at index 0 *every* token is capitalized (sentence start), so
`token[0].isupper()` gives no signal to tell "Marcus" (name → filter) from "Tavern" (content → keep).
A correct fix needs a proper-noun *source*, which is a design choice. The leak is currently LATENT
(all 25 real fixture beats lead with a directive verb, filtered before the index-0 branch).

**DECISION:** Fix now via (C) a corpus-derived proper-noun set, or (B) thread the packet roster in — or (A) accept + document + pin current behavior?

| Option | What | Effort | Coupling | Swarmable after? |
|---|---|---|---|---|
| A) accept + pin | comment at `scene_scope.py:183-185` + a test pinning current (leaky) behavior | ~30 min | none | trivial |
| B) thread roster | optional `proper_nouns` param through 6 sigs (all defaulted) + build the set from `characters_present` at `production_sequence.py:565` via existing `_roster_name_tokens` | ~½ day | couples scene_scope's caller to `packet_body` roster shape (empty when absent → silent degrade) | yes |
| **C) corpus-derived** ✅ | same `proper_nouns` plumbing, but populate INSIDE scene_scope: any token `_beat_tokens` marks proper (capitalized, index>0) *anywhere* in the beat set, applied uniformly incl. index 0 | ~½ day | none (input already in hand via `beat_ownership_map`) | yes |

**Recommendation — C.** It generalizes the module's own stated invariant ("proper nouns carry no
scope signal") to index 0 using a signal that actually exists — cross-beat recurrence (Marcus/Brent/Seb
all appear capitalized mid-sentence elsewhere) — instead of the useless sentence-start casing signal.
Keeps the module pure; a roster can later be *unioned* into the same param, so C forecloses nothing.

**Counter-argument (for A):** the leak is latent precisely because authoring is directive-first, so
B/C are speculative work on a pure, fully-tested module. "Don't gold-plate" → pin it and spend the
budget when a real name-first beat appears. Rebuttal: A's pinned test enshrines wrong output as correct.

**If C chosen → mechanical swarm task:** add `_corpus_proper_nouns(sequence_body)`, thread the defaulted
param, filter `token in proper_nouns`, add a name-first regression test, keep the 12 existing tests green.

---

## 2. TELEM-AGG — extract the router's SQL aggregation?

**DECISION:** (A) full extraction, (B) only the oracle-guarded rollups now, or (C) lint fence + defer?

`telemetry.py` builds SQL rollups (`_agg_cols:84`, 7 inline `group_by` in `book_telemetry:349-529`,
`compare_runs:688-752`) + ~90 LOC FK-join (`_resolve_links:137`/`_links_for_calls:185`) in the router;
the host `workers/telemetry_agg.py` (imported :49) holds only the reducers. TELEM-ORACLE pins the
rollup *output*, so a rollup extraction is behavior-verifiable; the `/llm-calls` FK path is untested.

| Option | Moves | Effort | Guarded? | Swarmable after? |
|---|---|---|---|---|
| A) full | all SQL + FK-joins → worker | high (~270 LOC) | rollups yes, FK path no | partly (untested half needs a parity test first) |
| **B) partial** ✅ | rollups only (`agg_cols`, `group_model_rows`, `book_telemetry_rollups`, compare inner) | med (~180 LOC) | fully (oracle + compare) | yes — tight, green-or-red on existing tests |
| C) lint-only | add "no `func.`/`.group_by` in routers/" fence, stop | trivial | n/a | n/a |

**Recommendation — B.** Cashes in the TELEM-ORACLE safety net on exactly the subset it covers; leaves
`book_telemetry` a thin HTTP shell; sequences naturally into an A-slice-2 (move the FK path) once the
`_resolve_links` parity test lands. Worker stays fastapi-free, returns dicts the router wraps to `*Out`.

**Counter-argument (for C):** none of this is a bug — only `select().group_by()` *construction* sits at
HTTP altitude. Moving it risks the worker growing a `schemas` dependency (losing its purity) or the
router keeping a dict→`*Out` mapping layer (the "second module" partly reappears). If tidiness < that
coupling cost, the fence is the right stopping point.

**If B chosen → swarmable** with the proposed worker signatures (`agg_cols()`, `group_model_rows()`,
`async book_telemetry_rollups(session, book_id, *, limit, offset) -> BookRollups`, `compare_run_rows()`).

---

## 3. MIG-DRIFT — build the forward-drift gate (the only silent-in-prod item)

**DECISION:** baseline as (A) a checked-in column snapshot, (B) a replayed live-DB schema diff, or (C) — ?

No baseline of any kind exists (no Alembic, no snapshot). The existing guard only checks the STALE
direction and disclaims forward drift in its own docstring. `create_all` provisions new *tables* but
never ALTERs existing ones, so a model column missing from `_COLUMN_ADDS` boots green on the fresh
test DB and throws `UndefinedColumn` in prod only.

| Option | Mechanism | Effort | False-positive risk | Catches type/nullability drift? | Swarmable after? |
|---|---|---|---|---|---|
| **A) metadata snapshot** ✅ | checked-in `tests/schema_baseline.json` `{table:[cols]}`; test fails iff a model col is `table∈baseline ∧ col∉baseline ∧ (table,col)∉_COLUMN_ADDS` | ~1-2h | low (new tables handled; allowlist for exceptions) | no (names only) | yes — self-policing |
| B) migrate-old-DB | check in a `pg_dump` at release N, replay migrations, reflect + diff vs fresh | ~½ day+ | low-med (type/default noise) | yes | mostly |
| C) DDL-text proxy | assert every model col name appears in DDL text | trivial | **all-false-positive** (originals never appear in ALTER) → degenerates into A | — | no |

**Recommendation — A.** It's the mirror image of a test the repo already trusts (static parse vs
`Base.metadata`, no DB), closes exactly the silent-prod case, and has the lowest rubber-stamp risk
because the routine fix is "add the ALTER line," never "bump the snapshot." B is the right *later*
fidelity upgrade. C doesn't actually work.

**Counter-argument (for B):** A checks existence only — a model `JSONB` vs an ALTER `TEXT`, or `NOT NULL`
vs a nullable ADD, sails through A and still misbehaves in prod (the same "green CI / wrong prod" class,
one layer deeper). If type/constraint drift is as likely as forgotten-column drift, only B is the real gate.

**If A chosen → swarmable:** `scripts/snapshot_schema.py` dumps the baseline (seed just after a real
deploy so it = what prod provably has); `tests/test_migration_forward_drift.py` does the diff with a
clear "add the ALTER line" error; optional `_CREATE_ALL_ONLY` allowlist.

---

## 4. ADR28-SUB — wire, keep, or delete the inert ADR-0028 substrate?

**DECISION:** (A) keep + xfail tripwires, (B) delete the unwired substrate, or (C) wire the minimal path?

Re-confirmed at HEAD: `ImportAdoption`/`ImportSceneEvidence`/`RevisionRequest` have **zero constructors**;
`acquire_chapter_workflow_lock` has **zero callers**; `RevisionRequest` gained one reader
(`workers/context/revision.py:26`) but it's gated on `Job.revision_request_id`, which **nothing assigns**,
so it never runs on real data. Cross-links IMP-EVID (its `ValidatedEvidence` dataclass persists nothing).

**⚠ This decision conflicts with a standing project note.** My memory records *"D18 slices 2–5 = ADR-0028
work (revision/adoption/migration/cleanup) — scheduled."* If that's live, this is pre-staged, not forgotten.

| Option | What | Effort | Reversibility |
|---|---|---|---|
| A) keep + xfail | 3 skip/xfail tests that flip green when a writer/lock/constructor lands | tiny | trivial |
| B) delete | remove `chapter_lock.py`, the 3 models, the dead reader branch, `Job.revision_request_id` + its DDL; rule on the enums (`RevisionRequestStatus` is imported by `revision_taxonomy.py`) | small-med | medium (re-addable from git) |
| C) wire minimal | add a `RevisionRequest` writer at the revise seam + set `job.revision_request_id` | multi-session (not decision-sized) | — |

**Recommendation (advisory lean) — B, conditioned.** ADR-0031 D10/D18 name "inert model-only layers"
as exactly what the decomposition must *not* produce; D13 makes delete/replace the default for the
sibling inert module; the substrate stayed inert across two audit passes and accrued doc-drift (the live
`Approval.feedback` path is mislabeled "LEGACY", which ADR-0031 D11 forbids). Delete now, re-add inside
the real slice where writer+reader+verification land together. **THE CONDITION:** if you're about to start
slice 2 this cycle, flip to A — delete/re-add is pure churn and xfail tripwires are the better guard.

**Counter-argument (for A/keep):** ADR-0028 is Accepted and *not* superseded, and MEMORY says slices 2–5
are scheduled — so this is planned, not abandoned. The migration DDL already declares the tables, FK, and
the fiddly active-request partial unique index; deleting means rewriting all of it in weeks. The inert
reader is provably harmless. Under that reading, A is pragmatic and B destroys reviewed design work.

**This one is genuinely yours** — the tie-breaker is your roadmap intent for ADR-0028 slice 2, which the
code cannot reveal.

---

## Execute batch (verified green — for context)

| Lane | File(s) | Tests | Result |
|---|---|---|---|
| IMP-EVID | `tests/test_import_evidence.py` [new] | 23 | pass |
| TWIN-PARITY | `test_draft_queue.py` + `test_telemetry_api.py` | 2 parity | pass (after `book_id` fix) |
| CTRL-PLANE | `tests/test_control_plane.py` [new] | 10 | pass |
| PKT-PIPE | `test_packet_derive.py` | 1 parity | **xfail(strict)** — found a real divergence |

Gate: `905 passed, 2 xfailed, 1 deselected` (ruff ✓ format ✓ pyright ✓). The deselected test is the
pre-existing env-dependent `test_http_smoke.py::test_cors_middleware_is_wired` (fails locally on your
`.env` CORS config, passes on CI — proven to fail independently of this batch).

### Findings the swarm surfaced (bonus)
1. **PKT-PIPE (real):** propose vs update write structurally different `_surface_contract` (update carries
   ~16 extra keys + `visible_character_evidence` on seeds — it projects the post-`to_master` body, propose
   the pre-). Violations match. Pinned as `xfail(strict=True)` → converts to a live green pin the moment
   the paths are aligned.
2. **TWIN-PARITY (latent):** `_resolve_links` uses `LIMIT 1` with no `ORDER BY` while `_links_for_calls`
   uses `DISTINCT ON` with no tiebreak — they'd pick different rows only with duplicate scenes/packets.
   Not covered by the parity test (seeded single rows); worth a follow-up.
3. **CORS test (env):** `test_cors_middleware_is_wired` is env-dependent — arguably its own small
   integrity item (a test that passes on CI but fails on a dev `.env`).
