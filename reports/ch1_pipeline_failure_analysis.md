# Chapter 1 pipeline failure analysis — baseline for `pipeline-recovery-ch1-sequence-budget`

Date: 2026-07-04. Failing run: `51d635ec` (status `repairing`, stage `repair_execution`).
Fixtures preserved under `tests/fixtures/ch1_bad_run/`:
`production_run_detail.json` (full run: issues, repair tasks, artifacts incl. assembled draft),
`chapter_packet.json`, `scene_packets.json`, `chapter_sequence.json`.

## 1. Entry-state chaining is broken (all scenes start from the global chapter entry)

From `chapter_sequence.json` — every scene's `entry_state` is the **identical global string**
even though scenes 2–4 declare `depends_on_scene_no` and `independent_draft_allowed=false`:

| scene | entry_state (prefix) | depends_on | independent |
|---|---|---|---|
| 1 | "Marcus is late at work, examining population/readiness data…" | — | false |
| 2 | "Marcus is late at work, examining population/readiness data…" | 1 | false |
| 3 | "Marcus is late at work, examining population/readiness data…" | 2 | false |
| 4 | "Marcus is late at work, examining population/readiness data…" | 3 | false |

Expected: scene 1 entry = `global_entry_state`; scene N entry = scene N−1 `exit_state`.
The exit_states ARE distinct and correct (work→match started→mutual recognition→coercion),
so the chain inputs exist — derivation simply never consumed them.

## 2. Scene bleed / duplicate irreversible beats

Because every scene restarts from the global entry, each drafter re-derived the whole arc:
scene 2 overran into scene 3/4 responsibilities; scenes 3 and 4 re-performed recognition and
interruption. Assembled draft (`chapter_draft` artifact in `production_run_detail.json`):
`hood` ×11, `red hair` ×7, `recogni*` ×9 — recognition beats staged repeatedly across scenes.
The sequence body carries `beat_ownership` and `forbidden_duplicate_functions`, but nothing
enforces them in drafting prompts or QA.

## 3. Word budget contradiction (approved before drafting, exploded after)

Scene packet `word_budget.hard_max`: 2200 + 2400 + 3200 + 2600 = **10,400**
Chapter budget (sequence `hard_max_words`): **7,200**
Assembled draft: **9,630 words** — over chapter hard_max by 34%, exactly as the arithmetic
guaranteed before a single LLM call was made. QA then flagged `word_budget_exceeded` *after*
assembly and spawned 4 `word_budget` repair tasks for a single global contract error.

## 4. QA missed a hard canon/contract leak: Neurochromatic Eyes in Chapter 1

Chapter 1's resolved rulings say no Eyes signal/notification. The assembled draft contains:

> "…Neurochromatic Eyes flickered at the edge of his perception, turning the field into
> layered probability and emphasis. He didn't need them fully ope…"

Neither scene QA nor chapter QA flagged it (0 of the 24 issues touch it).

## 5. Repair swarm instead of root-cause triage

24 issues: pacing ×10, combat ×5, length ×4, pov_knowledge_leak ×2, budget ×1,
confusing_mystery ×1, reader_context_gap ×1.
10 repair tasks: word_budget ×4, transition ×3, continuity ×2, reader_context ×1.
Nearly all are downstream symptoms of §1–§3. Structural blockers must collapse into single
root repair tasks and gate prose repair until resolved.

## 6. Cross-cutting

- 429 TPM failures have been misclassified as author/QA blockers elsewhere in the pipeline.
- UI presents contradictory state (approved/ready alongside disabled actions with no reason).

## Recovery lanes

L1 sequence chaining · L2 scene scope/beat ownership · L3 budget reconciliation ·
L4 Ch.1 canon guard · L5 repair triage clustering · L6 run orchestration/order ·
L7 provider rate-limit handling · L8 UI gate diagnostics · L9 UI payload performance ·
L10 regression harness over these fixtures. Integration on this branch.
