# L2 — Beat-ownership scope guards (`scene_scope_bleed`, `duplicate_irreversible_beat`)

Recovery lane 2 of the Ch1 pipeline failure (`reports/ch1_pipeline_failure_analysis.md` §2).
Evidence in the assembled draft: `hood` ×11, `red hair` ×7, `recogni*` ×9 — scene 2 overran into
scene 3/4 responsibilities; scenes 3 AND 4 both staged recognition; scene 4 also re-staged the
interruption.

## Root cause — ownership existed, enforcement did not

`derive_chapter_sequence` (src/dominion/workers/production.py:485) builds `beat_ownership`
(beat text → owning scene_no), per-scene `owned_beats`, and `forbidden_duplicate_functions` into
the ChapterSequence body. Tracing where that ownership actually flowed:

1. **Drafting prompt — the data arrived but was never rendered, and future beats never arrived at
   all.** `load_scene_packet_fields` (src/dominion/workers/context/contracts.py) overlays the
   scene's own `owned_beats`/`required_beats` from the sequence into the effective packet body, and
   `project()` (src/dominion/workers/scene_packet/projections.py) lifted `required_beats` into the
   flat drafter contract — but `_contract_block` (src/dominion/workers/specialists/drafter.py)
   never rendered required/owned beats into MUST, and NOTHING anywhere computed or injected the
   beats owned by LATER scenes. The drafter was never told "the hood-tear/recognition belongs to
   scene 3 — do not perform it."
2. **Chapter QA — a literal placeholder.** `run_chapter_draft_qa` (production.py) contained:
   "Placeholder: required beats / forbidden would require deeper analysis of prose vs contracts."
   `evaluate_chapter_sequence` checks duplicate ownership only in the PLAN, never in the prose.
3. **Scene QA is packet-level.** `scene_packet/qa.py` is an LLM attacker of the packet body; it
   never sees drafted prose, so it cannot catch bleed. Combined with the L1 defect (every scene
   restarting from the global chapter entry), each drafter re-derived the whole arc unopposed.

## Fix

### New module — `src/dominion/workers/scene_scope.py` (pure, importable, no I/O/LLM/dominion imports)

Match patterns are DERIVED from the `beat_ownership` entries themselves: keyword extraction
(drop the leading authoring directive, proper nouns, generic stopwords), a light suffix-stripping
stemmer with prefix alignment (so `recognition`/`recognized`, `coercion`/`coerced` match), and a
count threshold that scales with keyword count (all of ≤2, 2-of-3, then ≥ max(3, 40%)).
Irreversibility is classified from the beat's own language via a generic narrative-function
lexicon (reveal/recognition/interruption/consent/death/arrival…), never story strings.

- `detect_scene_scope_bleed(scene_no, prose, sequence_body)` → issues of kind
  **`scene_scope_bleed`** when prose performs a beat owned by a LATER scene
  (severity `block` if the beat is irreversible, else `repair`).
- `detect_duplicate_irreversible_beats(scene_prose_by_no, sequence_body)` → issues of kind
  **`duplicate_irreversible_beat`** when an irreversible owned beat (or any
  `forbidden_duplicate_functions` entry) is performed in more than one scene (severity `block`).
- `evaluate_scene_scope(...)` runs both; `beats_owned_by_later_scenes` /
  `owned_beats_for_scene` / `beat_ownership_map` are the prompt-side projections.

### QA wiring (production.py — declared shared-file edit, QA/issue-collection region only)

- `run_chapter_draft_qa`: the placeholder is replaced with `evaluate_scene_scope` over the
  assembled scene rows; findings join the QA findings with `issue_gates(severity)`, and a `block`
  finding flips the verdict to `block` (gates `final_chapter`).
- `assemble_run`: right after the `chapter_draft_qa` artifact is created, scope findings are
  persisted as Issue rows (validator `scene_scope`, kinds `scene_scope_bleed` /
  `duplicate_irreversible_beat`, severity `hard` for block-level findings) attached to the QA
  artifact, signature-deduped so re-assembly never duplicates them — so triage clustering (lane 5)
  has real Issue rows to cluster. Note: rows are created after this pass's `ready_for_human`
  gate, which is already protected by the QA `block` verdict for all irreversible findings.

### Drafter prompt wiring (declared edits)

- `context/contracts.py`: computes `beats_owned_by_later_scenes(sp.scene_no, seq.body)` and
  injects them into the effective packet body as `"(owned by scene N) <beat>"` strings.
- `scene_packet/projections.py`: `_flat_drafter_contract` now lifts `owned_beats` and
  `beats_owned_by_later_scenes` alongside `required_beats`/`forbidden_beats`.
- `specialists/drafter.py` `_contract_block`: MUST NOT now includes "perform, stage, or
  pre-resolve this beat — it belongs to a LATER scene: …" for every future-owned beat, and MUST
  now includes "perform this beat in THIS scene — it is owned here, and only here: …" for the
  scene's owned beats (previously in the contract dict but never rendered).

## Tests — `tests/test_scene_scope_bleed.py` (13, deterministic, no network/LLM/Postgres)

Fixture sequence body (`tests/fixtures/ch1_bad_run/chapter_sequence.json`) + synthetic prose:
scene 2 performing the hood-tear/recognition beat → `scene_scope_bleed` (owner 3, block);
recognition in scenes 3 AND 4 → `duplicate_irreversible_beat` ([3,4]); clean chapter (scene 3
alone performing its own recognition) → zero issues; plus keyword derivation, stem alignment,
irreversibility classification, ownership projections, `run_chapter_draft_qa` block wiring, and
module purity (lane 10 imports from `dominion.workers.scene_scope`).

## Verification

- `pytest tests/test_scene_scope_bleed.py` — 13 passed.
- Impacted subset (`test_production_runs`, `test_packet_derive`, `test_scene_packet_projections`,
  `test_drafter`, `test_phase2`, `test_pipeline_reviewers` via full run) — 40 passed.
- Full suite: DB-bound failures present both WITH and WITHOUT this diff (differing random subsets
  of the same sqlalchemy-error tests across the two runs; sampled failures pass in isolation) —
  shared `dominion_test` Postgres contention from the parallel recovery lanes, pre-existing.
- `ruff check` / `ruff format` / `pyright` clean on all six touched files.

## Declared shared-file edits (for the integrator)

- `src/dominion/workers/production.py` — 3 edits, all in the QA/issue-collection region:
  one import line; the `run_chapter_draft_qa` placeholder replacement; the Issue-row block in
  `assemble_run` immediately after the `chapter_draft_qa` artifact (before `reader_simulation`).
- `src/dominion/workers/context/contracts.py`, `src/dominion/workers/scene_packet/projections.py`,
  `src/dominion/workers/specialists/drafter.py` — prompt-injection wiring as above.
- No pydantic schema changes (no codegen needed): findings/issues reuse existing free-form
  finding dicts and the existing Issue model; the two new `issue_kind` strings are values, not
  schema.
