# Lane 10 — Ch1 pipeline regression harness (`tests/test_ch1_pipeline_regression.py`)

Acceptance/regression suite over `tests/fixtures/ch1_bad_run/` (failing run `51d635ec`,
see `reports/ch1_pipeline_failure_analysis.md`). Pure Python: no network, no LLM, no
Postgres. Helpers/adapters live in `tests/ch1_bad_run_fixtures.py`.

Gate command (Git Bash, from repo root):

```
export PATH="/c/Users/Nalakram/.local/bin:$PATH"
PY=/c/Users/Nalakram/.venvs/realmwalkers-win/Scripts/python.exe
PYTHONPATH="$PWD/src" ANTHROPIC_API_KEY=sk-ant-ci-not-a-real-key \
  "$PY" -m pytest tests/test_ch1_pipeline_regression.py -q
```

Pre-integration state (this commit): **8 passed, 7 skipped**.

## Test inventory

### Tier 1 — fixture canaries (always run; keep the bad-run fixture honestly bad)

| Test | Documents | Lane that fixes it |
|---|---|---|
| `TestFixtureCanaries::test_all_entry_states_identical_to_global_entry` | §1: all 4 sequence `entry_state`s == `global_entry_state` | L1 (fixture stays bad; L1 fixes the *code*) |
| `TestFixtureCanaries::test_exit_states_distinct_and_dependency_chain_declared` | chain inputs exist: distinct exits, deps 1→2→3→4, `independent_draft_allowed=false` | L1 |
| `TestFixtureCanaries::test_scene_budgets_contradict_chapter_envelope` | §3: scene `hard_max` sum 10,400 vs chapter 7,200 (packets AND sequence agree) | L3 |
| `TestFixtureCanaries::test_assembled_draft_busts_chapter_hard_max` | draft ≈9.6k words > 7,200; every scene row over its soft max | L3 |
| `TestFixtureCanaries::test_scene2_prose_contains_scene3_recognition_markers` | §2: hood/red-hair beat owned by scene 3, staged in scene 2 (scene 1 clean control) | L2 |
| `TestFixtureCanaries::test_recognition_beat_replayed_in_scenes_3_and_4` | §2: recognition re-performed in scenes 3 AND 4 (named replay in 4) | L2 |
| `TestFixtureCanaries::test_canon_leak_present_in_draft_and_forbidden_by_packet` | §4: "Neurochromatic Eyes flickered" in real prose (scene 2 row), No-Eyes ruling in packet, 0/24 issues flag it | L4 |
| `TestFixtureCanaries::test_bad_run_predates_new_issue_taxonomy` | §5: 24-issue swarm / 10 repair tasks; none use the recovery issue kinds | L5 (taxonomy) |

### Tier 2 — lane acceptance (importorskip-gated; **all 7 currently skipped/pending**)

| Test | Acceptance criterion | Lane | Gate |
|---|---|---|---|
| `TestLane1EntryStateChaining::test_postpass_chains_fixture_entry_states` | (a) scene1.entry==global; sceneN.entry==sceneN−1.exit over the fixture sequence | L1 | resolver probe: `scene_packet.derive` / `production` / `sequence_chaining` / `scene_packet.chaining` / `packet.sequence_chaining` × `chain_entry_states` & aliases; skips only while NO candidate exists |
| `TestLane1EntryStateChaining::test_derivation_itself_chains_when_lane1_lands_in_place` | (a) at the `derive_chapter_sequence` seam, re-deriving from the REAL packet body chains (directly or via post-pass) | L1 | same resolver; skips only while derivation is unchained AND no post-pass exists |
| `TestLane2SceneScope::test_scene2_bleed_into_scene3_beats_flagged` | (b) scene 2 prose staging scene-3/4 irreversible beats → `scene_scope_bleed` | L2 | `importorskip("dominion.workers.scene_scope")` |
| `TestLane2SceneScope::test_duplicate_recognition_beat_flagged` | (c) recognition in scenes 3 AND 4 → `duplicate_irreversible_beat` | L2 | same |
| `TestLane3BudgetReconciliation::test_budget_contradiction_cannot_pass_silently` | (d) 10,400 vs 7,200 → reconciled hard_max sum ≤ 7,200 OR blocking `sequence_budget_mismatch`; silence or an advisory-only issue FAILS | L3 | `importorskip("dominion.workers.budget_reconciliation")` |
| `TestLane4CanonGuards::test_real_draft_eyes_leak_flagged` | (e) REAL assembled prose + REAL packet prohibitions → `canon_contract_leak` referencing the Neurochromatic passage | L4 | `importorskip("dominion.workers.canon_guards")` |
| `TestLane4CanonGuards::test_ordinary_ui_prose_does_not_flag` | (f) prose built from `allowed_ui_concepts` (incl. ruling-sanctioned "voluntary status confirmed") does NOT flag | L4 | same |

Lane 5 (triage clusters) has no module pinned; the taxonomy canary
(`test_bad_run_predates_new_issue_taxonomy`) pins the four issue-kind names its clusters
key on. Lanes 6–9 (orchestration/rate-limit/UI) are out of harness scope by design.

## Integrator notes

- **Skip vs bite:** a missing lane module ⇒ clean skip (suite green pre-integration).
  A LANDED module that exposes an unrecognized callable name or signature ⇒
  `AdapterMismatch` **failure**, never a skip. Fix by extending the candidate
  name/argument lists in `tests/ch1_bad_run_fixtures.py` (one file), not the assertions.
- Result shapes are normalized (`as_issue_dicts` / `issue_kinds` / `is_blocking`):
  lists or `{"issues"/"items"/"findings": [...]}`, dicts/dataclass-ish objects/strings,
  kind under `issue_kind|kind|issue_type|type`, blocking via `blocking`/`is_blocking`,
  blocker-class severity, or a container-level `blocking_issues` list.
- Both directions were exercised with fake lane modules (scratchpad-only, not
  committed): well-behaved fakes → 15/15 pass; misbehaving fakes (wrong callable name,
  silent budget pass, missed leak, benign false-positive) → 5 loud failures, 0 skips.
