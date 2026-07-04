# Lane 6 — run orchestration: explicit stages + hard order enforcement

Branch `recovery/l6-run-orchestration` off baseline `4b9b282`. Companion to
`reports/ch1_pipeline_failure_analysis.md` (§3 budget contradiction, §5 repair swarm, §6 429
misclassification are this lane's targets).

## 1. Execution order as it stood (trace)

`create_production_run` (workers/production.py) ran, in one call:
`run_started → contract_classification → chapter_sequence (ensure_chapter_sequence)
→ issue_snapshot (scene artifacts + critique→Issue normalization + missing_scene issues)
→ assemble_run → [auto_triage] triage_production_run`.

Order violations found:

| # | Violation | Evidence |
|---|-----------|----------|
| 1 | **Assembly never refused.** `assemble_run` concatenated whatever prose existed, created `chapter_draft`/`chapter_draft_qa`/`reader_simulation`/`agent_evaluation` artifacts even when sequence scenes had no prose, and only *recorded* `missing_scene` issues afterwards. | old lines ~1294-1301: `missing_scene_nos` computed but never gated anything |
| 2 | **Sequence QA verdict ignored downstream.** `ensure_chapter_sequence` sets `status=blocked` / `qa_verdict=block_drafting` (the ch1 fixture IS blocked with `entry_exit_mismatches`), but neither `queue_draft_jobs_for_missing_sequence_scenes` nor `assemble_run` ever read it — drafting and assembly proceeded against a known-broken skeleton. | fixture `chapter_sequence.json`: `status="blocked"`, `qa_verdict="block_drafting"` |
| 3 | **Budget contradiction undetected before spend.** Scene-packet `hard_max` 2200+2400+3200+2600 = 10,400 vs chapter `hard_max_words` 7,200 — an overrun guaranteed by arithmetic before the first drafter call; `evaluate_chapter_sequence` only sums per-scene *targets* (7,200 = pass). | analysis §3 |
| 4 | **Structural issues scattered into repair.** `triage_production_run` grouped every accepted issue into per-scene repair tasks (`repair_queue → repair_execution`); no notion of a structural blocker gating prose repair (ch1: 24 issues → 10 tasks off 3 root causes). | analysis §5 |
| 5 | **Rate limits fell into a black hole at run level.** A draft job failing with `LlmRateLimited` marked only the Job FAILED (`worker.run_once`); the owning ProductionRun kept its previous stage with no retryable signal. | workers/worker.py except-path |
| 6 | **Stage strings were ad hoc** (`chapter_assembly` claimed by triage even when nothing was assembled; `awaiting_scene_drafts` vs the pinned `waiting_for_scene_drafts`). | old triage tail, old queue tail |

Precondition (a) — a run cannot start without an approved chapter packet — **already held**
(`create_production_run` raises `ValueError("no approved chapter packet for this chapter")`) and is
unchanged.

## 2. What changed

### New: `src/dominion/workers/run_stages.py` (pure, DB-free — the extracted transition functions)

- Pinned stage constants: `waiting_for_scene_drafts | drafting_scenes | scene_qa |
  assembling_chapter | chapter_qa | structural_repair_required | provider_rate_limited`
  (plain strings; the column stays a string, no enum migration).
- `STRUCTURAL_BLOCKING_ISSUE_KINDS = {sequence_budget_mismatch, scene_scope_bleed,
  duplicate_irreversible_beat, canon_contract_leak}` (pinned; other lanes emit, this lane routes).
- `evaluate_assembly_readiness(sequence_body, scenes_with_prose, sequence_blocked)` → refuse to
  `waiting_for_scene_drafts` (missing prose, with scene numbers) or `structural_repair_required`
  (blocked sequence), else proceed to `assembling_chapter`.
- `classify_qa_outcome(issue_kinds)` → `structural_repair_required` when pinned structural kinds
  present, else `chapter_qa`.
- `evaluate_drafting_readiness(...)` → refuses BEFORE any LLM call on: missing sequence, QA-blocked
  sequence, contradictory budget arithmetic (`sum(scene hard_max) > chapter hard_max` →
  `sequence_budget_mismatch`), or any expected scene lacking an APPROVED NON-STALE ScenePacket.
- `is_provider_rate_limited` / `stage_after_draft_failure` → `provider_rate_limited` for 429-class
  failures; **None for everything else** (a 429 can never land in a contract-failure state, and this
  lane routes no other failure kind).

Lane 3's `compute_draft_readiness` additions were not visible in this worktree at baseline, so the
budget check is lane-local per the brief; integrator reconciles (`draft_readiness.py` untouched by
this lane).

### `src/dominion/workers/production.py` (surgical, L6-commented regions)

- `assemble_run` head: assembly gate — structured refusal recorded as run event `assembly_refused`
  (reason + violations payload), run parked in `waiting_for_scene_drafts` (or
  `structural_repair_required` for a blocked sequence), **no artifacts created**, no exception dump.
  On pass: stage `assembling_chapter`.
- `assemble_run` tail (not-ready branch): chapter-QA routing — structural kinds among open issues or
  `chapter_draft_qa` findings → stage `structural_repair_required` + `structural_repair_required`
  event; otherwise stage `chapter_qa` (was: always `chapter_assembly`).
- `triage_production_run`: structural gate — structural issues are ESCALATED (with IssueDecision
  rows), NO repair tasks are created, non-structural issues stay proposed (prose repair gated), run
  → `structural_repair_required` + event. The no-tasks tail no longer overwrites the stage with
  `chapter_assembly`.
- `queue_draft_jobs_for_missing_sequence_scenes`: drafting gate (`evaluate_drafting_readiness`)
  before any queueing — `draft_blocked` event with violations; queued jobs now set stage
  `drafting_scenes` (was `awaiting_scene_drafts`).
- `update_timeline_after_scene`: run stage → `scene_qa` after a scene + critiques persist.
- New `mark_run_provider_rate_limited(session, run_id, error)`: stage `provider_rate_limited` +
  `provider_rate_limited` event (`retryable: true`), status untouched.
- `resume_production_run`: resuming from `provider_rate_limited` re-enters at
  `waiting_for_scene_drafts`.
- `run_final_qa`: post-refusal error names the parked stage instead of "artifact not found".

### `src/dominion/workers/worker.py`

- Captures `job.production_run_id` as a primitive pre-rollback; on job failure, if
  `stage_after_draft_failure(exc)` classifies a rate limit, calls
  `mark_run_provider_rate_limited` (best-effort, never masks the original raise).

## 3. Canonical order now enforced

```
approved ChapterPacket  ──(a, pre-existing, verified)──► run starts
derived sequence OK + approved non-stale ScenePackets + budget arithmetic OK ──(b,3)──► drafting_scenes
scene persisted + critiques ──► scene_qa
all sequence scenes have prose ──(c)──► assembling_chapter   [else assembly_refused → waiting_for_scene_drafts]
assembly artifacts + deterministic chapter QA ──► chapter_qa
structural blocking issue kinds ──(d)──► structural_repair_required   [prose repair gated]
provider 429 past retries ──(e)──► provider_rate_limited   [retryable; never contract-failure]
```

All gates are deterministic (set/arithmetic) and run before LLM spend at their boundary.

## 4. Tests (`tests/test_run_orchestration.py` — pure, no DB/network/LLM)

16 tests over the extracted functions, including: missing prose → refusal +
`waiting_for_scene_drafts`; structural kinds after QA → `structural_repair_required`; rate-limit
classification → `provider_rate_limited` (and non-429 → None); stale/missing packets and
contradictory budgets refuse drafting. Regression: the preserved ch1 fixtures are refused twice
over — `sequence_blocked` as preserved, `sequence_budget_mismatch` (10,400 > 7,200) once the
sequence is repaired.

`tests/test_production_runs.py` lifecycle smoke updated to the new contract: run start with a
missing scene now asserts NO `chapter_draft`/`chapter_draft_qa` artifacts, stage
`waiting_for_scene_drafts`, and the `assembly_refused` event naming scene 2.

## 5. Verification

- `pytest tests/test_run_orchestration.py tests/test_production_runs.py` → 21 passed
  (on `dominion_test_l6`; the shared `dominion_test` DB deadlocks under concurrent lanes).
- Full `pytest tests -q` on the isolated DB → green (see commit).
- `ruff check` + `ruff format` clean; `pyright` on the three touched src files → 0 errors.

## 6. Notes for the integrator

- `production.py` regions touched: `assemble_run` head/tail, `triage_production_run` gate + tail,
  `queue_draft_jobs_for_missing_sequence_scenes` gate + stage string, `update_timeline_after_scene`
  (one stage line), new `mark_run_provider_rate_limited`, `resume_production_run` (two lines),
  `run_final_qa` (message). All marked with `# L6` comments; contested regions
  (create_production_run internals, repair apply/verify) untouched.
- The legacy per-scene approved-packet check inside the queue loop is now redundant behind the gate
  but kept as defense in depth.
- Stage string migration: `awaiting_scene_drafts` → `drafting_scenes`; triage no longer emits
  `chapter_assembly` when it created no tasks. The frontend renders `current_stage` as free text
  (ProductionScreen), so no UI contract breaks.
- Lane 7 owns richer rate-limit handling; this lane only guarantees the run-level classification
  invariant (429 → `provider_rate_limited`, retryable, never contract-failure).
