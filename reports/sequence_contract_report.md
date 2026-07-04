# LANE 1 — Scene entry/exit chaining fix (`sequence_entry_state`)

Failing run evidence: `tests/fixtures/ch1_bad_run/chapter_sequence.json` — all four scenes carry the
identical global `entry_state` while `depends_on_scene_no` (1→2→3), `independent_draft_allowed=false`,
and distinct, correct `exit_state`s were all present. Scenes 2–4 restarted the whole chapter arc
(issue kind `scene_scope_bleed` downstream of a broken `sequence_entry_state`).

## Root cause

Two independent gaps, both required for the failure:

1. **Sequence build never chained.** `derive_chapter_sequence`
   (`src/dominion/workers/production.py:514`) copied `seed.get("entry_state") or
   packet_body.get("entry_state")` verbatim per scene. The chapter-packet author had emitted the
   global entry into every seed, and no post-pass ever rewrote scene N's entry to scene N−1's exit —
   even though `depends_on_scene_no` was being set to N−1 and `independent_draft_allowed` to false
   right below (lines 528–530). `evaluate_chapter_sequence` did detect the mismatch
   (`entry_exit_mismatches`) but only as a warning payload; nothing repaired it.
2. **The drafter never received ANY opening state.** The flat drafter contract
   (`src/dominion/workers/scene_packet/projections.py`, `_flat_drafter_contract`) lifted `exit_state`
   but dropped `entry_state` entirely; the drafter prompt (`src/dominion/workers/specialists/drafter.py`,
   `_contract_block` / `_beat_prompt`) had a MUST "end the scene at this state" but no "open from this
   state", and `ctx.prior_exit_state` (live `DraftRunTimeline.current_exit_state`) was assembled into
   `SceneContext` but never used in any prompt. So even a correctly chained sequence would not have
   reached the drafting prompt.

## Fix (exact locations, post-fix line numbers)

Deterministic chaining post-pass — `chain_scene_entry_states(body)`:

- `src/dominion/workers/production.py:575-620` — new pure function. Ordered by `scene_no`:
  scene 1 `entry_state = global_entry_state`, `depends_on_scene_no = None`; dependent scene N
  (`independent_draft_allowed` false) gets `entry_state` = its `depends_on` scene's `exit_state`;
  `depends_on_scene_no` must reference an earlier existing scene (missing/invalid defaults to N−1);
  `unlocks_scene_no` must reference a later existing scene (missing/invalid defaults to next-in-order,
  `None` for the last). Independent scenes keep their authored entry. Exit states are never touched.
- `src/dominion/workers/production.py:555` — `derive_chapter_sequence` returns through the post-pass,
  so every derived/re-derived sequence (`ensure_chapter_sequence`, the `/chapter-sequence/derive`
  route, production run start) is chained at build time.
- `src/dominion/workers/production.py:2888` — `update_chapter_sequence` runs the post-pass on manual
  body edits before persisting + QA, so human edits can't reintroduce the break.
- `src/dominion/workers/production.py:788-796` — `evaluate_chapter_sequence` mismatch check now
  compares a scene's entry against its `depends_on` target's exit (default previous) and skips
  `independent_draft_allowed=true` scenes, matching the enforcement contract (an intentionally
  independent scene no longer produces a spurious `block_drafting`).

Drafter actually receives the chained state:

- `src/dominion/workers/context/contracts.py:54-59` — the sequence overlay now **overwrites** the
  scene-packet body's `entry_state` with the sequence's chained value for dependent scenes (was:
  fill-only-if-missing, so a stale packet-body entry beat the chained plan). Independent scenes keep
  packet-authored entry.
- `src/dominion/workers/scene_packet/projections.py:91-92` — `_flat_drafter_contract` lifts
  `entry_state` into the flat contract (previously dropped).
- `src/dominion/workers/specialists/drafter.py:96-101` — `_contract_block` adds the MUST line: "open
  the scene FROM this state — … do not restage, replay, or re-establish any of it". Applies to both
  draft and revise prompts (both build the contract block).
- `src/dominion/workers/specialists/drafter.py:165-172` — `_beat_prompt` prefix now injects
  `ctx.prior_exit_state` (live `DraftRunTimeline` state, updated after each drafted scene) as "Where
  the story stands as this scene opens", covering the runtime chain even when the planned sequence is
  stale. For scene 1 the timeline is seeded with `global_entry_state`, so the text stays correct.
- `src/dominion/workers/production.py:1947-1954` — the production-run `scene_packet` artifact view
  applies the same authority rule (sequence entry overwrites a stale packet entry for dependent
  scenes) instead of `setdefault`, so recorded artifacts match what drafting consumed.

## Tests

`tests/test_sequence_chaining.py` — 8 tests, deterministic pure-Python, no network/LLM/Postgres,
driven by the preserved bad-run fixture:

- `test_bad_run_fixture_reproduces_the_break` — fixture still shows the failure shape.
- `test_chain_pass_repairs_the_bad_run` — scene 1 entry == `global_entry_state`; scene 2 entry ==
  scene 1 exit; scene 3 entry == scene 2 exit; scene 4 entry == scene 3 exit; exits untouched.
- `test_chain_pass_enforces_dependency_links` — depends_on defaulting (None/dangling/forward → N−1),
  valid earlier reference kept (and its exit used), unlocks normalization.
- `test_independent_scene_keeps_authored_entry` — `independent_draft_allowed=true` keeps its
  authored entry; neighbors still chain.
- `test_evaluate_accepts_chained_body_and_flags_unchained` — QA flags the bad run, passes the
  chained body.
- `test_evaluate_does_not_flag_independent_scene`.
- `test_derive_chapter_sequence_chains_seed_entries` — the real derivation path chains seeds that
  all carry the global entry (the exact Ch1 authoring shape).
- `test_drafter_contract_carries_entry_state` — `entry_state` reaches the flat drafter contract.

Gates: `pytest tests/test_sequence_chaining.py` 8 passed; adjacent suites
(`test_production_runs.py`, `test_drafter.py`, `test_phase2.py`, `test_packet_derive.py`,
`test_scene_packet_projections.py`) 27 passed; `ruff check` / `ruff format --check` / `pyright`
clean on all touched files.

## Out-of-lane edits declared

- None functionally out of lane. All edits serve entry-state chaining/consumption. Two lane-adjacent
  notes for the integrator:
  - `evaluate_chapter_sequence` mismatch semantics changed (depends_on-aware + independent-scene
    exemption) — this can flip a previously `block_drafting` verdict to `approve` for sequences with
    intentionally independent scenes.
  - `specialists/drafter.py` prompt gained two blocks (contract MUST `entry_state`, prefix
    `prior_exit_state`) — touches the drafting prompt that L2 (scene scope/beat ownership) may also
    edit; textual merge risk only.
- No pydantic response schema changed (sequence `body` is an untyped JSON dict end-to-end); no
  codegen needed.
