# Execution Contract: N1 — Greenlet enrich-after-commit systemic class

Status: **ready**

Source: `reports/codebase_integrity_audit_2026-07-07_pass2.html`, candidate **N1** (lane GI2-1; links C7/N2, overlaps N13). Selected 2026-07-07.

### Grilling resolutions (2026-07-07)

A grilling session settled the design tree:
- **Fix shape:** per-site `await session.refresh(row)`, matching the 4 sites already in tree. `eager_defaults` (the structural "can't recur" option) stays a follow-up.
- **Test coverage:** all 12 at-risk endpoints get a red-capable test, parametrized by resource group, reusing existing seeders.
- **Scope discipline — N1 stays a boring bug-fix.** Every changed line must map to the live-500 class. N13 (scene_packet transaction-ownership refactor) is **split out as the immediate next PR**, reviewed on its own as a transaction-ownership change — not hidden inside the reliability patch.
- **Facts resolved:** the repair `/verify` endpoint is excluded (it serializes a freshly-INSERTed `RepairVerification`, and INSERT server-defaults return via RETURNING → loaded/safe; the bug is the UPDATE path of `onupdate` columns only). `DraftRunTimeline` has no API serialization site → mechanism-test only, no endpoint fix.

---

## 1. Executive mission

Eliminate the systemic `MissingGreenlet` latent-500 class: every API handler that mutates a server-side-`onupdate` model, commits, then serializes the row without first refreshing it. Fix the ~12 unrefreshed sites to match the 4 already-correct ones, and add red-capable endpoint tests so the class cannot silently recur.

## 2. Current baseline

- **Branch:** `main` @ `2b6e18b` (post-Wave-1). Working tree clean.
- **What runs today:** CI (`.github/workflows/ci.yml`) — `static` (ruff + changed-files pyright), `tests` (real Postgres, `DOMINION_REQUIRE_DB=1`, `pytest -q -rs`), `frontend`. Locally: ruff/pyright via the Windows venv; **no local Postgres**, so DB-backed tests self-skip locally and only truly run in CI (or against a reachable Postgres with `DOMINION_REQUIRE_DB=1`).
- **Pre-existing failures:** none on `main`. One intentional `strict xfail` (`tests/test_full_chapter_run.py::test_fully_drafted_chapter_through_run_reaches_final_ready`) is expected-xfail, not a failure.
- **Mechanism (confirmed this baseline):** `shared/db.py:14` `expire_on_commit=False`; models `ScenePacket`/`ChapterSequence`/`ProductionRun`/`RepairTask`/`DraftRunTimeline` carry `updated_at` with a server-side `onupdate` (`shared/models.py:280,305,637,812,880`); the matching `*Out` schemas mark `updated_at` **required** (`schemas.py:1602,1771,1970`). SQLAlchemy expires server-computed columns at flush regardless of `expire_on_commit`, so a post-commit `model_validate` reads `updated_at` via a sync lazy-load on the async session → `MissingGreenlet` 500. **Proven in CI** on 2026-07-08: `test_mark_stale_reconciles_beats` failed with exactly this error until `session.refresh` was added.
- **Reference fixes already in tree (the pattern to replicate):** `api/routers/production.py:472` (align-scene-count), `api/routers/production.py:53` (`_create_detail`), `api/routers/scene_packets.py:306` (update), `:453` (mark-stale).

## 3. Strategic meaning

Reliability is currently the repo's weak axis (2/5) solely because of this class: ~12 destructive or token-spending endpoints (cancel/resume/approve a run, apply/reject/rollback a repair, approve a sequence, batch-approve packets) return a 500 on their success path. It is the highest-severity, highest-leverage candidate, and — unlike a one-off — a single consistent fix plus a red-capable test per endpoint removes the whole class and its recurrence.

## 4. Scope

- Add `await session.refresh(<row>)` (matching the 4 existing sites) between `commit()` and serialization at the **12 confirmed at-risk endpoints**: 11 in `api/routers/production.py`, 1 in `api/routers/scene_packets.py`.
- Add **red-capable endpoint tests** (assert 200-not-500 on the success path) covering those 12 endpoints, grouped by resource.
- Add **one mechanism regression test** documenting the class over the 5 `onupdate` models.
- Prove the new tests are red on unfixed code (negative-fixture step).

## 5. Non-goals

- **Not** a broad "add refresh after every commit" sweep — only the mutate→commit→serialize sites on `onupdate` models. GET/list endpoints serialize freshly-loaded rows and are explicitly excluded.
- **Not** the `eager_defaults` structural consolidation (see §9 — recorded as a follow-up).
- **Not** N13 (scene_packet transaction-ownership refactor: facade functions owning commit+refresh+enrich) — **the immediate next PR**, reviewed as a transaction-ownership change, not folded into this reliability patch.
- **Not** N2 (the sweeper `greenlet_spawn` race) — likely the same class but data-specific and unreproduced; separate `diagnose` loop → follow-up.
- **Not** the production god-module re-cut (N3).

## 6. Blast-radius summary (from GI2-1)

- **Producers of the mutation:** facade functions in `workers/production.py` (`cancel_production_run`, `resume_production_run`, `approve_final_chapter`, repair/sequence mutators) and `workers/scene_packet/__init__.py` (`approve_scene_packets`) — they mutate + return the ORM row; **they do not serialize**, so no change there.
- **Fix locus:** the **router** handlers (`api/routers/production.py`, `api/routers/scene_packets.py`) that commit + serialize.
- **Response contracts:** unchanged — `response_model` is already `ProductionRunOut`/`RepairTaskOut`/`ChapterSequenceOut`/`ScenePacketOut`; the fix makes the success path actually fulfill them (500 → 200). No OpenAPI/`generated.ts`/`types.ts` change.
- **Fixtures/goldens:** none affected. **Cross-language mirrors:** none.

## 7. Contracts / seams involved

- **`updated_at` server-`onupdate` seam** — `shared/models.py` (owner: DB model layer). Authoritative; not modified.
- **Router serialize helpers** — `_run_out` (`api/routers/production.py:41`) + the repair/sequence serialize expressions (exact form per endpoint confirmed in T1) + `enrich_scene_packet_out` (`workers/scene_packet/approval_policy.py:224`). The refresh precedes these calls; the helpers themselves are unchanged.

## 8. Human decisions required

**None** — no hard human-decision category applies (connected-impact-sweep list checked: no public-contract change — successful responses were always specified to return the `*Out` model; no schema/migration/deletion/numerical/GPU/security/routing fork). The design-shape choice (per-site refresh vs. a shared helper vs. `eager_defaults`) is an implementation decision, resolved in §9 with rationale — it does not block this contract.

## 9. Implementation strategy

**Decided shape: per-site `await session.refresh(row)`**, identical to the 4 sites already in tree, inserted between `commit()` and the serialize call. Rationale: lowest risk, byte-consistent with the established pattern, zero new abstraction, no SQL-behavior change. Recurrence is handled by the per-endpoint red-capable tests (§11) rather than by a new seam.

Rejected alternatives (named, not silently dropped):
- **Shared `refresh_before_serialize(session, *rows)` helper** — marginal DRY benefit over `session.refresh`, but introduces an abstraction the 4 existing sites don't use; inconsistent for a bug-fix mission. *Rejected for this mission.*
- **`eager_defaults=True` on the 5 `onupdate` models** — the true structural class-fix (RETURNING loads `updated_at` at flush, no per-site discipline ever needed), but it changes SQL behavior for *all* updates of 5 core models, carries perf and regression-risk, and needs its own verification. *Recorded as follow-up `N1-followup: eager_defaults consolidation`* — revisit once coverage (N10) exists to measure it.

## 10. Task graph

```
T1  (discovery, no code)            — confirm 12 sites + per-endpoint serialize expr + verify command
T2  (depends T1)                    — production.py: run endpoints (cancel/resume/approve-final) + tests
T3  (depends T1, after T2 — same file) — production.py: repair endpoints (apply/approve-apply/reject/rollback) + tests
T4  (depends T1, after T3 — same file) — production.py: sequence endpoints (derive/update/approve/revise) + tests
T5  (depends T1; parallel to T2-T4) — scene_packets.py: batch-approve + test
T6  (depends T1; parallel to T2-T5) — mechanism regression test over the 5 onupdate models
T7  (depends T2-T6)                 — negative-fixture proof: tests go red on reverted refresh
```

T2/T3/T4 all edit `api/routers/production.py` → **sequential** (same file). T5 (`scene_packets.py`) and T6 (new test file) are **parallel-eligible** with the production.py chain.

## 11. Task-by-task plan

### T1 — Ground the sites and the verification command *(discovery; no production code)*
- **Depends:** none.
- **Purpose:** confirm each at-risk endpoint's exact serialize expression and lock the verify command before editing.
- **Files:** `api/routers/production.py`, `api/routers/scene_packets.py` (read-only), `.github/workflows/ci.yml`, `justfile` (read-only).
- **Action:** for each endpoint at `production.py` lines ~186 (cancel), ~196 (resume), ~532 (approve-final), ~354 (apply), ~381 (approve-apply), ~409 (reject), ~421 (rollback), ~447 (derive-seq), ~459 (update-seq), ~492 (approve-seq), ~504 (revise-seq), and `scene_packets.py:392` (batch-approve): record the row variable and the serialize call (`_run_out(run)` / `RepairTaskOut.model_validate(...)` / `ChapterSequenceOut.model_validate(...)` / `enrich_scene_packet_out(r)`). Confirm each mutates an `onupdate` model and lacks a preceding refresh.
- **Check:** a written site table (12 rows) checked into the PR description; no code check.
- **Verify:** `rg -n "await session.commit\(\)" src/dominion/api/routers/production.py src/dominion/api/routers/scene_packets.py` cross-referenced against the serialize call on the next non-blank line; confirm the 4 reference sites already carry `session.refresh`. Establish the acceptance command: `DOMINION_TEST_DATABASE_URL=<throwaway> DOMINION_REQUIRE_DB=1 UV_PROJECT_ENVIRONMENT=/c/Users/Nalakram/.venvs/realmwalkers-win uv run --no-sync pytest <new test files> -q` — run against the disposable Postgres per the §14 protocol; CI is the final gate, not first execution.
- **Risk/rollback:** none (read-only). If an endpoint's serialize form differs from the two expected shapes, note it and adjust T2–T5.

### T2 — Refresh + tests for run mutation endpoints
- **Depends:** T1.
- **Purpose:** stop cancel/resume/approve-final from 500ing on success.
- **Files:** `api/routers/production.py`; `tests/test_production_run_mutation.py` `NEW`.
- **Action:** insert `await session.refresh(run)` after `await session.commit()` and before `_run_out(run)` in `cancel_production_run` (~186), `resume_production_run` (~196), `approve_final_chapter` endpoint (~526–532). Mirror the comment at `production.py:472`.
- **Check:** `NEW` tests seed a `ProductionRun` in the right precondition, call each endpoint via the app/session, assert HTTP 200 and a well-formed `ProductionRunOut` (`updated_at` present) — red-capable (500 without the refresh).
- **Verify:** `... pytest tests/test_production_run_mutation.py -q` → all pass (CI or Postgres-local).
- **Risk/rollback:** refresh on a row the mutator left in an odd state could surface an unrelated error; low. Rollback = revert the 3 inserted lines.

### T3 — Refresh + tests for repair-task mutation endpoints
- **Depends:** T1; edit **after T2** (same file).
- **Purpose:** stop apply/approve-apply/reject/rollback from 500ing.
- **Files:** `api/routers/production.py`; `tests/test_repair_task_mutation.py` `NEW`.
- **Action:** insert `await session.refresh(task)` before the `RepairTaskOut` serialize in `apply` (~354), `approve-apply` (~381), `reject` (~409), `rollback` (~421). **`/verify` is excluded** — it serializes a freshly-INSERTed `RepairVerification` (INSERT server-defaults return via RETURNING → loaded/safe), not the mutated `RepairTask`.
- **Check:** `NEW` tests seed a `RepairTask`, drive each endpoint, assert 200 + valid `RepairTaskOut`. Red-capable.
- **Verify:** `... pytest tests/test_repair_task_mutation.py -q` → pass.
- **Risk/rollback:** repair endpoints have richer preconditions (a queued/applied task); seeding may need a parent run. Rollback = revert inserted lines.

### T4 — Refresh + tests for chapter-sequence mutation endpoints
- **Depends:** T1; edit **after T3** (same file).
- **Purpose:** stop derive/update/approve/revise-sequence from 500ing.
- **Files:** `api/routers/production.py`; `tests/test_chapter_sequence_mutation.py` `NEW`.
- **Action:** insert `await session.refresh(sequence)` before the `ChapterSequenceOut` serialize in `derive` (~447), `update` PUT (~459), `approve` (~492), `revise` (~504). (align-scene-count ~472 already has it — leave as the reference.)
- **Check:** `NEW` tests seed a `ChapterSequence`, drive each endpoint, assert 200 + valid `ChapterSequenceOut`. Red-capable.
- **Verify:** `... pytest tests/test_chapter_sequence_mutation.py -q` → pass.
- **Risk/rollback:** derive may create-or-update; ensure the UPDATE path is the one tested. Rollback = revert inserted lines.

### T5 — Refresh + test for scene-packet batch-approve
- **Depends:** T1; **parallel** to T2–T4.
- **Purpose:** stop batch-approve from 500ing on success.
- **Files:** `api/routers/scene_packets.py`; `tests/test_scene_packet.py` (extend — reuse existing seeding helpers).
- **Action:** at `scene_packets.py:392`, before `return [enrich_scene_packet_out(r) for r in rows]`, add `for r in rows: await session.refresh(r)` (mirror the mark-stale fix at `:453`).
- **Check:** extend `tests/test_scene_packet.py`: seed a chapter with ≥2 approved-eligible packets, call `approve_scene_packets` endpoint, assert it returns the enriched list without raising (200) — red-capable.
- **Verify:** `... pytest tests/test_scene_packet.py -k batch -q` → pass.
- **Risk/rollback:** N per-row refresh (bounded by scene count). Rollback = revert the loop.

### T6 — Mechanism regression test (documents the class)
- **Depends:** T1; **parallel** to T2–T5.
- **Purpose:** one test that pins the mechanism for all 5 `onupdate` models, so the class is understood even as endpoints evolve.
- **Files:** `tests/test_onupdate_serialization.py` `NEW`.
- **Action:** parametrize over `ScenePacket, ChapterSequence, ProductionRun, RepairTask, DraftRunTimeline`: insert a row, mutate a field + `commit()`, then `model_validate` the matching `*Out` (or refresh-then-validate) and assert no `MissingGreenlet`. `DraftRunTimeline` is **defensive-only** here — it has no API serialization endpoint (confirmed), so it gets the mechanism assertion but no endpoint fix. Include a comment linking to N1.
- **Check:** the test itself; asserts serialize-after-mutate is safe once refreshed.
- **Verify:** `... pytest tests/test_onupdate_serialization.py -q` → pass.
- **Risk/rollback:** a model may need a parent row to insert; if seeding one is disproportionately heavy, cover it via its endpoint test instead and note the exclusion. Rollback = delete the file.

### T7 — Negative-fixture proof
- **Depends:** T2–T6.
- **Purpose:** prove the new checks are red-capable (a check never shown failing proves nothing).
- **Files:** none committed (proof step, recorded in PR body).
- **Action:** temporarily revert one inserted `session.refresh` (e.g. the cancel-run site), run that endpoint's test, capture the `MissingGreenlet` failure, then restore.
- **Check:** the captured red output.
- **Verify:** with the refresh reverted, `... pytest tests/test_production_run_mutation.py -k cancel -q` → **fails** with `MissingGreenlet`; after restore → passes. Paste both into the PR.
- **Risk/rollback:** ensure the revert is fully restored before commit (git diff clean for that line).

## 12. Execution mode

**Connected-impact sweep.** The mission fixes one behavior (post-commit serialization) across a shared state-update path touching many call sites in two routers; connected-impact-sweep supplies the "enumerate all consumers of the pattern, fix as one coherent unit, verify" discipline, with this contract's scope and gates. Not swarm — T2/T3/T4 serialize on one file, so parallel lanes would collide.

## 13. Required commands

```bash
# Static (runnable locally via the Windows venv)
UV_PROJECT_ENVIRONMENT=/c/Users/Nalakram/.venvs/realmwalkers-win uv run --no-sync ruff check src tests
UV_PROJECT_ENVIRONMENT=/c/Users/Nalakram/.venvs/realmwalkers-win uv run --no-sync ruff format --check src tests
PYTHONPATH="$PWD/src" /c/Users/Nalakram/.venvs/realmwalkers-win/Scripts/pyright.exe src/dominion/api/routers/production.py src/dominion/api/routers/scene_packets.py

# DB-backed tests — run against a PROVISIONED THROWAWAY Postgres (see §14 protocol), not CI-first.
# Provision (any isolated, non-production DB — Docker shown; a temp Railway/Neon/Supabase DB is equally fine):
#   docker run --rm -d --name n1pg -e POSTGRES_USER=dominion -e POSTGRES_PASSWORD=dominion \
#     -e POSTGRES_DB=dominion_test -p 5432:5432 pgvector/pgvector:pg16
# Point tests at it + require DB so they fail loudly instead of skipping:
export DOMINION_TEST_DATABASE_URL=postgresql+asyncpg://dominion:dominion@127.0.0.1:5432/dominion_test
DOMINION_REQUIRE_DB=1 UV_PROJECT_ENVIRONMENT=/c/Users/Nalakram/.venvs/realmwalkers-win uv run --no-sync \
  pytest tests/test_production_run_mutation.py tests/test_repair_task_mutation.py \
         tests/test_chapter_sequence_mutation.py tests/test_onupdate_serialization.py \
         tests/test_scene_packet.py -q -rs
```

## 14. Verification gates

### Verification protocol — disposable Postgres, not CI-first

**Do not rely on CI-only unless disposable Postgres provisioning is impossible.** This bug is DB-only-reproducible — it reached production precisely because CI was the *first* place it ran. The executor proves red→green locally against a real database before pushing.

**Primary path.** Before finalizing the patch, provision a throwaway Postgres — local Docker, or a temporary Railway/Neon/Supabase instance, or any isolated DB that is **not** production/staging data — point `DOMINION_TEST_DATABASE_URL` at it, and run the N1 endpoint tests with `DOMINION_REQUIRE_DB=1` so they **fail loudly instead of skipping**.

**Required proof loop:**
1. Write the parametrized endpoint tests first.
2. Run the N1 subset against real Postgres on the **unpatched** code; capture **at least one representative red failure** proving the greenlet class (`MissingGreenlet`).
3. Apply the mechanical refresh fix across the 12 at-risk UPDATE paths.
4. Re-run the same N1 subset → **green**.
5. Run the broader relevant backend test slice.
6. **Push only after local disposable-DB red→green is proven.** CI remains the *final* gate, not the first real execution.

**Fallback path.** If no throwaway Postgres can be provisioned, **state that explicitly in the handoff**, push a **draft** PR, and treat CI as the execution harness. In that fallback the PR is **not review-ready until CI proves the N1 subset green**.

### Per-phase gates
- **After T1:** the 12-row site table exists; the 4 reference sites confirmed already-refreshed.
- **Per T2–T6:** each new/updated test **fails on the pre-fix code** and **passes on the post-fix code** — red→green shown (steps 2 & 4 above), not asserted.
- **Phase green:** `ruff check` + `ruff format --check` clean; changed-files pyright clean; the full DB test selection green against the disposable Postgres (and then CI).
- **T7:** documented red-on-revert + green-on-restore for at least one site.

## 15. Failure codes

Standard: `FAIL-SCOPE-CREEP`, `FAIL-PHANTOM-TARGET`, `FAIL-UNVERIFIED-TASK`, `FAIL-FAKE-GREEN`, `FAIL-BURIED-DECISION`. Mission-specific:
- `FAIL-NO-REFRESH-SITE` — an at-risk endpoint from the T1 table shipped without its refresh.
- `FAIL-GET-TOUCHED` — a read-only/GET endpoint was modified (out of scope; GETs serialize fresh rows).
- `FAIL-GREEN-WITHOUT-DB` — a test-pass claim made from a local run where Postgres was unreachable (tests skipped, not run). DB tests only count green from CI or a real Postgres.

## 16. Negative fixtures

- **T7** is the primary negative fixture: reverting one `session.refresh` must make its endpoint test raise `MissingGreenlet` (proof the tests catch the class).
- Each endpoint test is itself a negative fixture for its site: it is red on the pre-fix baseline.
- **Guard against fake-green:** because DB tests self-skip without Postgres, every green claim cites the CI run id (or a local run with `DOMINION_REQUIRE_DB=1` and Postgres up) — a skipped test is not a pass (`FAIL-GREEN-WITHOUT-DB`).

## 17. Review plan

- **Spec compliance:** all 12 T1 sites carry a refresh; no GET endpoint touched; response models unchanged; each site has a red-capable test; T7 red-on-revert shown.
- **Code quality:** refresh placement/comment matches the 4 reference sites; no new abstraction snuck in beyond the decided shape; tests reuse existing seeding helpers (no parallel harness); no scope creep into N13/N2/N3.

## 18. Merge gate

- **Primary:** push only after the §14 proof loop is done — local disposable-DB **red-on-unpatched then green-on-patched** captured and pasted into the PR body (plus T7's red-on-revert). Ruff + format + changed-files pyright green. CI is the **final** gate: merge only once CI's N1 subset is also green.
- **Fallback (no throwaway DB):** open a **draft** PR stating the executor could not provision Postgres; it is not review-ready until CI proves the N1 subset green.
- Follow the ship flow (feature branch, detailed PR body, watch CI to green before merge).

## 19. Definition of done

Answerable by running §13 with no judgment call:
1. `rg "session.refresh" src/dominion/api/routers/production.py` shows a refresh at all run/repair/sequence mutation sites (11) + the pre-existing align-scene-count; `scene_packets.py` batch-approve shows the refresh loop. → done/not-done.
2. The four new/extended test files pass in CI (`-rs` shows the DB tests **ran**, not skipped). → done/not-done.
3. T7's revert produces `MissingGreenlet`; restore produces green. → done/not-done.
4. `git diff --stat` touches only the two router files + the test files (no GET endpoints, no facade, no schema/types.ts). → done/not-done.

---

## Follow-ups (out of scope, recorded)

- **N1-followup — `eager_defaults` consolidation:** adopt `eager_defaults=True` on the 5 `onupdate` models to remove per-site refresh discipline entirely; gate on N10 (coverage) existing to measure the RETURNING/perf impact.
- **N13 (immediate next PR)** — scene_packet transaction-ownership refactor: convert the mutating scene_packet front-door functions to own commit+refresh+enrich (Q5 option 2 — self-committing command facade), including new `edit`/`mark_stale` functions, and thin out their routes. Reviewed as a transaction-ownership change. **Ship with an ADR:** "scene_packet command facade owns commit+refresh+enrich; other routers remain caller-commits" — records the deliberate cross-facade asymmetry (why scene_packet commits internally but production doesn't).
- **N2** — diagnose the sweeper `greenlet_spawn` race (likely this class; needs the captured traceback / a reproducing fixture).
- **A static/AST lint** asserting "no post-commit `model_validate`/enrich of an `onupdate` model without a preceding refresh" — the fully-recurrence-proof guard, if the endpoint tests prove insufficient over time.
