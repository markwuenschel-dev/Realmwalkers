# Execution Contract: C1 — Sweeper reads expired ORM attribute after savepoint rollback (N1-sibling greenlet hazard)

Status: **ready**

Source: `reports/codebase_integrity_audit_2026-07-08.html` — candidate **C1** (priority 16, top of queue). Lane sources: graded_integrity (GI1), captain-verified.

---

## 1. Executive mission

Make the autonomous self-repair sweeper safe against the greenlet class that N1 just closed on the API surface. Inside `_sweep_one_run`, each per-task/per-stage step runs in a `session.begin_nested()` savepoint; when a step raises, the savepoint rolls back and expires the attributes it mutated on the live ORM object, but the surrounding `except`/`record_activity` code then reads that same object **outside** the savepoint — a sync lazy-load on the async session → `MissingGreenlet`, which escapes the per-task guard and parks the whole run. Harden every post-savepoint ORM read in `_sweep_one_run` to use primitives captured before the savepoint (or a fresh `session.get` re-fetch), mirroring the already-correct pattern in `background_work.drain_queued_repair_tasks`, and lock the class with a red-capable regression test plus a structural fitness check.

## 2. Current baseline

- **Branch:** `fix/n1-greenlet-enrich-after-commit` @ `4d489fe`. Working tree: one unrelated deleted file (`book1/manuscript/scenes/SCENE-001_earth-opening.md`) + the new audit report (untracked) — **neither touched by this contract**.
- **What runs today:** `scripts/verify.ps1` / `just verify` → `ruff check`, `ruff format --check`, `pyright` (changed `src/` files vs `origin/main`), `pytest -q -rs` with `DOMINION_REQUIRE_DB=1` + Postgres/pgvector on `127.0.0.1:5432`.
- **DB-gated:** `_sweep_one_run` tests need a real Postgres. Locally that means the Windows venv + a throwaway Postgres (see §13); CI is the final gate, not first execution.
- **Pre-existing failures:** none known on this branch (N1 suite green in CI at ship).
- **Grounded evidence (read this run):**
  - `src/dominion/workers/sweeper.py:185-296` — `_sweep_one_run` with **three** savepoints: triage `:204`, apply `:235`, verify `:289`.
  - Apply loop `:224-270`: `except ValueError` (`:254`) and `except Exception` (`:268`) both read `str(task.id)`; success path `:244,:250` reads `task.authority_level`.
  - Verify loop `:285-296`: `begin_nested` `:289`; `except Exception` `:294` reads `str(task.id)`.
  - Documented-but-unreproduced crash: `sweeper.py:200-202` and `:366-370` ("a data-specific greenlet_spawn fires OUTSIDE the wrapped stages on one real run and no synthetic run reproduces it — the frame names the exact line").
  - **Correct reference pattern:** `src/dominion/workers/background_work.py:192-213` — captures `task_id = task.id` before the `try`, `await session.rollback()` in `except`, then `parked = await session.get(RepairTask, task_id)` to re-fetch. The sweeper does **not** do this.
  - Mutation source: `src/dominion/workers/production_repair.py:636+` — `apply_repair_task(session, task_id)` re-`get`s the same session-identity object, sets `task.human_approved_at` (`:660`) / `task.status` (`:647,:710,:739`), records events, then can `raise ValueError` (`:542,:645,:677`). Same session ⇒ the mutated object is the sweeper-loop `task`.
  - Existing harness: `tests/test_sweeper.py` (`db_factory`, `_seed_run`, `_approval_task`, `_cfg`) — already contains a **sibling** repro `test_sweeper_triage_realwork_no_greenlet:192-221`, proving faithful greenlet reproduction is achievable in this fixture.

## 3. Strategic meaning

The sweeper is the always-on autonomy loop; a greenlet 500 here parks a run every tick and stalls self-repair with no data loss but no progress. C1 is the last known live instance of the exact class N1 eliminated on the API surface — closing it makes the reliability grade defensible (3→4 on that axis) and turns a self-documented "can't reproduce" comment into an enforced check. Scores: severity 4, confidence 4, locality 5, regression_risk 1, human_decision_risk 1 → priority 16.

## 4. Scope

- All three savepoint-guarded stages in `_sweep_one_run` (triage `:204`, apply `:235`, verify `:289`) and their `except`/`record_activity`/logging paths.
- One red-capable regression test reproducing the apply-path expiry, plus assertions the loop continues and records the blocked activity.
- One structural fitness check forbidding ORM-attribute reads after a `begin_nested()` in this seam.

## 5. Non-goals

- **Not broad cleanup.** No god-module re-cut of `production.py` (C5), no enum work (C4), no retention set-delete (C11), no sweeper-registry eviction (C13) — all separate ledger candidates / follow-ups.
- **Not** changing `apply_repair_task` / `verify_repair_task` internals in `production_repair.py` — the fix lives in the *caller* (`sweeper.py`).
- **Not** modifying `background_work.py` — it is the correct reference, read-only here.
- **Not** changing sweeper behavior, ceilings, attempt caps, activity semantics, or config.
- **Not** a compatibility shim — a direct fix of the caller's post-savepoint reads.

## 6. Blast-radius summary

Locality 5 / blast 2 / regression_risk 1 — contained to `_sweep_one_run` (one function) + `tests/test_sweeper.py`. No schema, no DTO, no wire contract, no cross-language surface. Consumers of the sweeper (the `/pipeline` dashboard, `sweeper_status`) read the heartbeat/activity feed, both unchanged in shape. The only behavioral change: a task whose apply/verify raises now records its `blocked` activity reliably instead of sometimes crashing the run — strictly a reliability improvement to an existing path.

## 7. Contracts / seams involved

- **Seam:** ORM-identity lifetime across an async `begin_nested()` savepoint abort vs. the async-session no-sync-IO rule. **Rule (authoritative):** never read ORM attributes after a commit/rollback/savepoint-abort on an async session without a refresh or a primitive captured beforehand. Owner: `dominion.workers` async-session discipline (same rule N1 enforced on routers).
- **Reference contract:** `background_work.drain_queued_repair_tasks` (`background_work.py:192-213`) is the canonical shape this fix converges the sweeper onto.

## 8. Human decisions required

None. C1 is a pure reliability fix (human_decision_risk 1); no public-contract, migration, policy, deletion, or numerical fork. Contract issues as **ready**.

## 9. Implementation strategy

**Decided shape — primitive-capture + defensive re-fetch, applied to all three savepoint stages:**

1. Before each `begin_nested()` (or at the top of each loop iteration), capture the primitives the stage's error/success/logging paths need as plain locals: `tid = str(task.id)`, `authority = task.authority_level` (a `StrEnum` value — safe to read pre-savepoint).
2. In every `except` and `record_activity`/log call for that stage, use the captured locals — never `task.<attr>` after the savepoint.
3. Where a post-savepoint path genuinely needs a live row (none currently do in the except paths, but guard against regression), re-fetch with `await session.get(RepairTask, tid)` after the savepoint settles.

**Rejected alternatives:**
- *`await session.refresh(task)` in each except* — refresh itself is IO that can fail on a rolled-back/expired object and re-raises the same class; primitive capture is cheaper and cannot lazy-load. Rejected.
- *Wrap the whole loop body in one savepoint* — changes isolation semantics (one bad task would abort siblings), contradicting the module's per-task isolation design (`sweeper.py:186-188`). Rejected.
- *`expire_on_commit`/`Session` config change* — global blast radius, unrelated to savepoint-rollback expiry (which happens regardless). Rejected.

**Uncertainty gating the approach** (the candidate's one stated unknown): which stage/attribute actually triggers the production greenlet, and whether a synthetic run reproduces it. **T1 resolves this before the fix is written** — downstream tasks assume T1 produced a red behavioral test; if T1 proves the synthetic apply-path repro will not go red (PK-only reads in the except path may not lazy-load), it pins the mechanism from the production traceback and the class is instead locked by the T4 structural check, with T1 downgraded to a non-red characterization test (recorded in its Verify).

## 10. Task graph

- **T1** (repro/discovery) — no deps. Gates T2–T4.
- **T2** (fix apply + triage + verify stages; depends: T1)
- **T3** (green the T1 regression; depends: T2)
- **T4** (structural fitness check + negative fixture; depends: T2) — parallelizable with T3.
- **T5** (full verify + review; depends: T3, T4)

## 11. Task-by-task plan

### T1 — Reproduce the apply-path expiry as a red test (depends: none)
- **Purpose:** Prove the greenlet class fires from `_sweep_one_run`'s apply path on current code, and pin exactly which read triggers it.
- **Files:** `tests/test_sweeper.py` (existing).
- **Action:** Add `test_sweeper_apply_raises_midmutation_no_greenlet(db_factory)`. Seed a run + a chapter-scoped `WAITING_FOR_HUMAN` approval task within the default ceiling (reuse `_seed_run`/`_approval_task`). Drive `apply_repair_task` to raise **after** it mutates `task.human_approved_at`: prefer forcing the real downstream `ValueError` (e.g. a chapter-scoped repair whose preconditions fail → `production_repair.py:542`); if the real path can't be provoked deterministically, `monkeypatch` `production.apply_repair_task` with a fake that `t = await session.get(RepairTask, task_id); t.human_approved_at = datetime.now(UTC); t.status = RepairTaskStatus.RUNNING; raise RuntimeError("boom")` so the savepoint rolls back a genuinely-mutated identity object. Call `await sweeper._sweep_one_run(s, run.id, _cfg())` and assert: (a) it does **not** raise `MissingGreenlet`/`greenlet_spawn`; (b) a `sweeper_blocked` (or `run_blocked`) activity was recorded; (c) `needs_human` path completed. Remember `sweeper._attempts.clear()`/`_warned_human.clear()`.
- **Check:** the new test itself.
- **Verify:** `UV_PROJECT_ENVIRONMENT=~/.venvs/realmwalkers-win uv run --no-sync pytest tests/test_sweeper.py::test_sweeper_apply_raises_midmutation_no_greenlet -q -rs` against a throwaway Postgres → **RED** with `MissingGreenlet`/`greenlet_spawn` on current code. Record the failing frame (file:line) in the test docstring. If it does not go red, record that finding and the production traceback line in the docstring and proceed (T4 becomes the primary lock; see §9).
- **Risk / rollback:** repro may be non-deterministic. Mitigation: the monkeypatch fallback makes the mutation+raise deterministic. Rollback: delete the test.

### T2 — Harden all three savepoint stages to primitive-capture / re-fetch (depends: T1)
- **Purpose:** Remove every post-savepoint ORM read in `_sweep_one_run`.
- **Files:** `src/dominion/workers/sweeper.py` (`_sweep_one_run`, `:185-296`).
- **Action:** In the apply loop (`:224-270`): at the top of the iteration capture `tid = str(task.id)` and `authority = task.authority_level`; replace `str(task.id)` / `task.authority_level` in the success `record_activity` (`:244,:250`), `except ValueError` (`:254-266`), and `except Exception` (`:268-270`) with the locals. In the verify loop (`:285-296`): capture `tid = str(task.id)` before `begin_nested`; use it in the `except` log (`:294`). Triage stage (`:204-207`): confirm no `task`-attribute read post-savepoint (it logs `rid` only) — no change if clean, else apply the same rule. Follow `background_work.py:192-213` shape.
- **Check:** T1's regression test (goes green after this task) + T4's structural check.
- **Verify:** `... pytest tests/test_sweeper.py -q -rs` → all sweeper tests pass, including T1's; and `ruff check src/dominion/workers/sweeper.py`, `ruff format --check src/dominion/workers/sweeper.py`, `uv run --no-sync pyright src/dominion/workers/sweeper.py` clean.
- **Risk / rollback:** capturing `authority` pre-savepoint assumes it isn't mutated by apply — verified: `apply_repair_task` never writes `authority_level`. Rollback: `git checkout src/dominion/workers/sweeper.py`.

### T3 — Confirm the regression test now passes green (depends: T2)
- **Purpose:** Prove the fix closes the reproduced class (red→green).
- **Files:** none (runs T1's test against T2's code).
- **Action:** Re-run T1's test on the fixed code.
- **Check:** T1's test.
- **Verify:** `... pytest tests/test_sweeper.py::test_sweeper_apply_raises_midmutation_no_greenlet -q -rs` → **GREEN**. Paste both the T1 red output and this green output in the PR.
- **Risk / rollback:** if still red, the fix missed the triggering read — return to T2 and widen the capture to the frame T1's docstring named.

### T4 — Structural fitness check: forbid ORM-attr reads after `begin_nested` in the sweeper (depends: T2)
- **Purpose:** Make the class non-recurring — a future edit that reintroduces a post-savepoint `task.<attr>`/`run.<attr>` read fails a check.
- **Files:** `tests/test_sweeper_loop.py` (existing) **or** `tests/test_sweeper_greenlet_guard.py` `NEW`.
- **Action:** Add an AST/source check over `src/dominion/workers/sweeper.py`: within `_sweep_one_run`, assert no `except` block that follows a `begin_nested()` contains an attribute read on the loop variable (`task.`/`run.` other than a captured-primitive assignment). Implement as a focused source scan (regex on the except-block bodies is acceptable given the single-file scope) with a clear failure message naming the offending line.
- **Check:** this test.
- **Negative fixture:** in the same test, feed the checker an inline bad snippet (a string with `except Exception: log.error(x=task.authority_level)` after a `begin_nested`) and assert the checker flags it — proving the check can go red (see §16).
- **Verify:** `... pytest tests/test_sweeper_greenlet_guard.py -q` (or the loop file) → passes on fixed `sweeper.py`; the embedded negative fixture asserts the checker rejects the bad snippet.
- **Risk / rollback:** over-strict regex could false-positive on legitimate captures. Mitigation: scope to except-block bodies only, allow-list `tid`/`authority` local assignments. Rollback: delete the test.

### T5 — Full verification + review (depends: T3, T4)
- **Purpose:** Prove nothing else regressed and scope held.
- **Files:** none.
- **Action:** Run the full gate; run the review plan (§17).
- **Check:** full suite + lint + types.
- **Verify:** see §13 / §18.
- **Risk / rollback:** unrelated pre-existing failures — record separately, do not fix under this contract.

## 12. Execution mode

**Sequential.** Single vertical seam in one function + its tests; no independent parallel surfaces, no contract/schema fan-out (so not connected-impact-sweep; not a swarm). Matches the candidate's `execution_mode: sequential`.

## 13. Required commands

Windows venv per project convention (Git Bash `uv run` fails on the WSL-format `.venv`):

```bash
export UV_PROJECT_ENVIRONMENT=/c/Users/Nalakram/.venvs/realmwalkers-win
export DOMINION_REQUIRE_DB=1          # throwaway Postgres+pgvector on 127.0.0.1:5432
# targeted (red→green):
uv run --no-sync pytest tests/test_sweeper.py -q -rs
# structural check:
uv run --no-sync pytest tests/test_sweeper_greenlet_guard.py -q
# full gate (final):
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pyright src/dominion/workers/sweeper.py
uv run --no-sync pytest -q -rs
```

Or, on the owner's machine: `scripts/verify.ps1`. DB-only-reproducible bug → provision a disposable Postgres and prove red→green locally before pushing; CI (`.github/workflows/ci.yml`, `DOMINION_REQUIRE_DB=1`) is the final gate.

## 14. Verification gates

| Phase | Gate | Expectation |
|-------|------|-------------|
| After T1 | `pytest ::test_sweeper_apply_raises_midmutation_no_greenlet` | **RED** (`MissingGreenlet`) on current code — or documented non-repro + pinned traceback |
| After T2 | `ruff`/`ruff format`/`pyright` on `sweeper.py` | clean |
| After T3 | same T1 test | **GREEN** |
| After T4 | structural check + its negative fixture | check green on fixed file; negative fixture proves it can go red |
| After T5 | full `pytest -q -rs` | green; skip lines surfaced, no fake-green |

## 15. Failure codes

```text
FAIL-SCOPE-CREEP        — touched production.py / enums / retention / registries / background_work.
FAIL-PHANTOM-TARGET     — named a file/line absent from the baseline and not marked NEW.
FAIL-UNVERIFIED-TASK    — reported a task done without pasting its verify command output.
FAIL-FAKE-GREEN         — claimed green while DB tests skipped (no DOMINION_REQUIRE_DB / no Postgres).
FAIL-NO-RED             — shipped the regression/structural check without ever showing it fail first.
FAIL-BURIED-DECISION    — resolved a human-decision inside a task (none exist here; flag if one appears).
FAIL-REFRESH-BANDAID    — "fixed" an except path with session.refresh(task) (rejected — see §9).
```

## 16. Negative fixtures

- **T1 is the behavioral negative fixture:** it exercises the invalid state (apply raises after mutating an identity object inside a savepoint) and must fail loudly (`MissingGreenlet`) before T2.
- **T4 carries a structural negative fixture:** an inline bad snippet (`except` reading `task.authority_level` after `begin_nested`) that the checker must flag — proving the fitness check is red-capable and not vacuous.

## 17. Review plan

- **Spec compliance:** only `_sweep_one_run` + the two/new test files changed; all three savepoint stages covered; `background_work.py`/`production_repair.py` untouched; behavior unchanged except reliability; T1 shown red then green.
- **Code quality:** captures are minimal primitives (no over-fetching); no `session.refresh` band-aid; the fix reads as the `background_work` sibling; the structural check is scoped and message-clear; no broad cleanup bled in.

## 18. Merge gate

Open the PR when all hold, with pasted output:
1. T1 red (pre-fix) **and** green (post-fix).
2. Structural check green + its negative fixture proven red-capable.
3. `uv run --no-sync pytest -q -rs` green against Postgres (no skipped DB tests).
4. `ruff check`, `ruff format --check`, `pyright src/dominion/workers/sweeper.py` clean.
5. Diff limited to `src/dominion/workers/sweeper.py` + `tests/test_sweeper*.py` (+ optional `NEW` guard test).

PR body: selected candidate (C1), what changed, why (the savepoint-expiry mechanism + the `background_work` precedent), integrity impact (N1-class closed in autonomy path), verification evidence (red→green), the new fitness check, risks/follow-ups (C5/C11/C13 remain separate).

## 19. Definition of done

Running the §13 commands answers done/not-done with no judgment:
- `test_sweeper_apply_raises_midmutation_no_greenlet` is **red on `4d489fe`** and **green after the fix**.
- The structural check passes on the fixed `sweeper.py` and its negative fixture proves it rejects a post-savepoint ORM read.
- Full `pytest -q -rs` is green against Postgres with no DB skips; ruff + pyright clean.
- The diff touches only `sweeper.py` and the sweeper tests.

---

### Follow-ups (out of scope, recorded)
- **C11** retention set-based delete · **C13** sweeper-registry eviction — both in `sweeper.py`/`retention.py` neighborhood; batch with a future autonomy-hygiene loop.
- **C5** `production.py` de-shim + god-module split.
- Consider generalizing the T4 structural check into a repo-wide import-linter/AST rule if a third instance of the class appears (currently: routers=fixed, sweeper=this contract).
