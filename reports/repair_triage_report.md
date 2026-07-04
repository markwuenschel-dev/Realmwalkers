# L5 — Repair triage clustering (root-cause, not symptom swarm)

Lane 5 of the Ch1 pipeline recovery (`reports/ch1_pipeline_failure_analysis.md` section 5).

## Problem

The bad Ch1 run (`tests/fixtures/ch1_bad_run/production_run_detail.json`) produced
24 issues (pacing x10, combat x5, length x4, pov_knowledge_leak x2, budget x1,
confusing_mystery x1, reader_context_gap x1) which the old triage scattered into
10 per-scene repair tasks (word_budget x4, transition x3, continuity x2,
reader_context x1). Nearly all were downstream symptoms of three structural root
causes: broken entry-state chaining, scene scope bleed, and the scene/chapter
word-budget contradiction. The swarm rewrote symptoms while the causes stayed live.

## Change

### New pure module: `src/dominion/workers/repair_triage.py`

Deterministic, DB-free clustering onto the pinned root-cause keys:

| Cluster key | Members mapped in | Task shape |
|---|---|---|
| `sequence_entry_state` | entry/exit/transition-mismatch symptoms (pacing/transition kinds, entry_state claims) | ONE chapter-scoped task, `chapter_structural` |
| `scene_scope_bleed` | `scene_scope_bleed`, `duplicate_irreversible_beat`, duplicated-beat claim text | ONE chapter-scoped task, `cross_scene` |
| `budget_mismatch` | `sequence_budget_mismatch`, `length`, `budget`, word-budget symptoms | ONE chapter-scoped task, `chapter_structural` |
| `canon_contract_leak` | `canon_contract_leak`, canon validator | ONE chapter-scoped task, `chapter_structural` |
| `prose_polish` | residual line-level issues (dialogue, pov_knowledge_leak, combat choreography, ...) | per-scene tasks, DEFERRED while any structural cluster is unresolved |
| `infra_rate_limit` | `infra_rate_limit`, provider 429 / rate-limit text | NEVER a repair task (retry state) |

API: `infer_root_cause(issue)`, `cluster_issues(issues)`,
`plan_repair_tasks(issues) -> TriagePlan` (structural clusters in pinned order,
prose lane, rate-limit lane, `defer_prose` gate), plus `STRUCTURAL_AUTHORITY`
and `ROOT_CAUSE_INSTRUCTIONS` metadata for task construction.

### `triage_production_run` (src/dominion/workers/production.py)

- Accepted issues are clustered by root cause BEFORE repair-task creation.
- Each structural cluster produces exactly ONE chapter-scoped root repair task
  (`scene_id/scene_no = None`, `repair_kind = cluster key`, root-cause instruction
  preamble, all member issue ids on `issue_ids`).
- `prose_polish` issues keep today's per-scene grouping, but tasks are NOT created
  while any structural cluster task is unresolved (planned now, or still open in
  the DB with status queued/running/waiting_for_human/failed). Deferred issues stay
  `accepted`; a `repair_deferred` event records the gate. Re-running triage after
  structural tasks resolve picks the accepted-untasked issues back up and releases
  the prose tasks.
- `infra_rate_limit` issues are accepted as retry state, never create tasks, and
  emit a `rate_limit_retry_state` event.
- Preserved behavior: `missing_scene` still escalates; `info` severity still rejects.

Declared edits outside the strict triage region (both are the triage call path):

- `_queue_repair_task_from_issues` gained optional kwargs `repair_kind`,
  `authority_level`, `chapter_scoped`, `instruction_preamble` (defaults preserve
  existing behavior for all other callers, e.g. manual `decide_issue`).
- `_highest_authority` return annotation corrected `str` -> `RepairAuthorityLevel`.

## Effect on the bad Ch1 run

23 accepted issues (24 minus one info reject) now collapse to **3 structural root
tasks** instead of 10 symptom repairs:

- `sequence_entry_state` x9 (all pacing/transition symptoms)
- `budget_mismatch` x5 (length x4 + budget x1)
- `scene_scope_bleed` x1 (duplicated hood-tear/recognition beats)
- 8 `prose_polish` issues deferred behind them (combat choreography x4,
  pov_knowledge_leak x2, confusing_mystery x1, reader_context_gap x1)

## Tests

`tests/test_repair_triage_clustering.py` — pure, in-memory Issue-like rows, no
DB/network/LLM (clustering was extracted precisely so the DB-coupled triage shell
stays thin):

- (a) three transition/entry-mismatch issues -> ONE `sequence_entry_state` cluster
- (b) word-budget contradiction -> ONE `budget_mismatch` cluster, no rewrite swarm
- (c) duplicate recognition beats -> ONE `scene_scope_bleed` cluster
- (d) prose_polish deferred while structural clusters exist (and released without)
- infra_rate_limit never joins a repair lane
- fixture regression: the real 23-issue bad run -> exactly 3 structural clusters
  (9/1/5) + 8 deferred prose issues, no issue lost

Gates: new tests 7/7 pass; production-adjacent suites pass; ruff check/format
clean; pyright clean on both touched files.
