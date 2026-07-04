# Lane 3 — budget reconciliation: scene word budgets vs the chapter envelope

Branch: `pipeline-recovery-ch1-sequence-budget` (worktree lane 3). Baseline: `4b9b282`.
Failure evidence: `reports/ch1_pipeline_failure_analysis.md` §3 and `tests/fixtures/ch1_bad_run/`.

## 1. Trace — where the numbers are born, and where they were (never) compared

| Artifact | Where created | Value in the ch1 bad run |
|---|---|---|
| Sequence `target_words` / `max_words` / `hard_max_words` | `production.py::derive_chapter_sequence` (~L558-565): `hard_max_words = chapter_max or chapter_target`, from `scene_packet/inputs.py::chapter_targets` (explicit `chapter_target_words`/`chapter_max_words` on the packet body, else sum of seed targets with **no** max) | 7,200 / 7,200 / 7,200 |
| Sequence body scene `word_budget` (min/target/max/hard_max) | `length/planner.py::plan_word_budgets` — deterministic split of the chapter target by scene-type weight; `hard_max = target * 1.60` (capped per scene at 4,500/6,500 and, only when given, at `chapter_max_words`) | 2,200 + 2,400 + 3,200 + 2,600 = **10,400** |
| Scene packet `word_budget` | Copied verbatim from the same planner output in `scene_packet/derive.py` (~L451, L499); `scene_packet/validation.py` (~L171) *enforces* the copy (`word_budget_override` if the model deviates); `scene_packet/staleness.py` re-runs the planner for staleness hashes | identical 10,400 |
| Comparison of the two | **Nowhere.** `evaluate_chapter_sequence` checks planned *targets*; QA checks each scene against its *own* budget; assembly checks nothing until post-hoc `word_budget_exceeded` | absent — the contradiction was approved |

Root cause: the planner normalizes *targets* to the chapter target, then multiplies each by
1.6 for `hard_max` and caps them only individually. Sum(hard_max) ≈ 1.6 × chapter target by
construction — every chapter's persisted envelope was contradicted by its own scene budgets,
and nothing upstream of assembly ever added the four numbers together. Ch1 drafted 9,630
words against a 7,200 envelope, then QA spawned 4 `word_budget` repair tasks for one global error.

## 2. What was built

### `src/dominion/workers/budget_reconciliation.py` (new, pure, stdlib-only)

- `reconcile(sequence_body, scene_budgets) -> ReconciliationResult(budgets, issues, changed)`.
  Accepts the chapter `hard_max_words` (int) or any mapping carrying it (sequence dump with
  `scenes`/`body.scenes` works directly).
- Pinned policy: the chapter envelope is **authoritative**.
  - `sum(hard_max) <= hard_max_words` → untouched, no issues.
  - Overflow → scale down proportionally: each scene keeps its `min` floor; the headroom above
    the floor is compressed by one shared integer-floored ratio (`min + (delta * headroom) // total_over`),
    preserving relative scene weights and guaranteeing the scaled sum fits. `target` and `max`
    get the same floor-anchored treatment, so `min <= target <= max <= hard_max` survives per scene.
  - Impossible (`sum(min floors) > hard_max_words`) → budgets untouched + exactly **one** blocking
    issue of kind **`sequence_budget_mismatch`** (`BudgetIssue(blocks_drafting=True)`), never per-scene spam.
- `check_sequence_budget_consistency(hard_max_words, scene_budgets)` — the gate-side check: a
  *persisted* envelope that is inconsistent in either flavor (scalable overflow or impossible)
  returns one blocking `sequence_budget_mismatch`, because the gate cannot rewrite stored
  packets — re-derivation is the fix. Sequences with no numeric envelope are skipped (nothing
  to contradict; no false positives on legacy rows).
- Ch1 numbers reconcile to hard_max 1,523 + 1,661 + 2,215 + 1,800 = 7,199 ≤ 7,200, floors and
  rank order (3 > 4 > 2 > 1) intact.

### Wiring (a) — derivation emits consistent budgets

`length/planner.py::plan_word_budgets` now reconciles its output against the same envelope
production will persist (`chapter_max_words or chapter_target_words`) as its final step. One
uncontested edit covers every consumer: sequence derivation (`production.py`), scene-packet
derivation (`scene_packet/derive.py`), and staleness recomputation (`scene_packet/staleness.py`) —
freshly derived sequences and packets can no longer contradict their own envelope. Consistent
plans pass through byte-identical (no hash churn for healthy chapters). An impossible plan is
left untouched here (pure function, no issue channel) — the draft gate blocks it.

### Wiring (b) — draft gate blocks contradictions before LLM spend (declared: shared file)

`draft_readiness.py::compute_draft_readiness` (shared with lanes 6/8 — addition is one import
line plus one fenced block between the beat-blocker loop and the `draftable` computation):
loads the chapter's latest `ChapterSequence` (same `updated_at desc` selection production uses),
runs `check_sequence_budget_consistency`, and prepends at most one `sequence_budget_mismatch`
blocker (`DraftQueueBlockerOut`) naming both sums and the fix. Existing logic then does the
rest: `draftable` goes false via `len(blockers) == 0` and `disabled_reason` surfaces the
blocker's message — a contradictory envelope now stops the Draft gate with a stated reason
before any LLM call. Replaying the ch1 fixture numbers through the gate check yields exactly
one blocker (10,400 vs 7,200).

Also: `draft_queue.py` — registered `"sequence_budget_mismatch"` in `DraftBlockerReason` (one line).

## 3. Tests — `tests/test_budget_reconciliation.py` (20 pass; pure, no DB/LLM/network)

- (a) ch1 fixture: 10,400 vs 7,200 reconciles to sum ≤ 7,200, floors preserved, per-scene
  ordering intact, relative weights (rank order) preserved, non-numeric budget keys untouched.
- (b) valid envelope passes through unchanged (`changed=False`, no issues), incl. exact-fit.
- (c) impossible envelope (floors 9,000 > 7,200) → blocking `sequence_budget_mismatch`, budgets unrewritten.
- (d) one issue regardless of scene count (8 scenes → 1 issue); gate check on the persisted ch1
  data → exactly 1 blocker.
- Planner wiring: ch1 numbers replayed through `plan_word_budgets` come out reconciled; the
  1.6× auto-overflow (4 × 1,800-target scenes → 11,520 vs 7,200) now fits; a plan with envelope
  headroom is not rescaled.

## 4. Declared changes to shared/contested files

- `draft_readiness.py` (shared lanes 6/8): +1 import line, +1 fenced ~35-line block, no existing
  lines modified.
- `production.py`: **not touched** (the planner edit reaches its call site without entering the file).
- `draft_queue.py`: +1 Literal member.
- Pydantic schemas: **none changed** (`DraftQueueBlockerOut.reason` is `str`).
- `tests/test_scene_packet.py`: two assertions updated (1500 → 1200 target, + hard_max 1500) —
  they hardcoded the pre-policy planner output, which was the ch1 bug in miniature (a
  single-scene 1,500-word chapter carrying a 2,400-word scene hard_max).

## 5. Verification

- `pytest tests/test_budget_reconciliation.py` + the two updated scene-packet tests: **20 passed**.
- Full suite in a quiet window: 2 failed → both were the hardcoded planner numbers, updated; all
  other tests pass. (Later full-suite runs show hundreds of `DeadlockDetected` from parallel
  recovery lanes sharing `dominion_test` — environmental, not lane 3.)
- `ruff check` + `ruff format`: clean. `pyright` on all touched source + lane test file: 0 errors
  (`test_scene_packet.py`'s 34 pre-existing errors are identical at baseline).

## 6. Notes for other lanes

- Lane 10: import surface is `reconcile`, `check_sequence_budget_consistency`,
  `SEQUENCE_BUDGET_MISMATCH`, `BudgetIssue` (`.as_dict()`), `ReconciliationResult` — pure module,
  safe to import anywhere.
- Lane 5 (repair triage): the gate blocker plus derivation reconciliation should eliminate the
  "4 word_budget repair tasks for one global error" pattern at the source; post-assembly
  `word_budget_exceeded` remains as a backstop.
- Manual seed budgets are scaled too when they overflow the envelope — the envelope is
  authoritative over the author's per-scene call by pinned policy. If that ever needs a carve-out,
  it belongs in `plan_word_budgets` before the reconcile step.
