# Agent Brief — Phase 2: Unblock Drafting (F/G/I) + Docs Truthing

> **Status:** Phase 1 shipped in PR #138 (path-aware CI, cached build, tests 579→215, workers 91→81,
> stale-canon cleanup UI/API, context hygiene). This brief covers the deferred, higher-risk workstreams
> that touch the production drafting path. Do them in the order below.

## Prime directive

The user cannot reach production drafting: the packet pipeline **hard-blocks on repairable issues**.
The goal is to make drafting reachable from a chapter outline in the browser, without the pipeline
dead-ending on things an author agent could just fix. Reserve hard blocks for a small set of true
blockers; route everything else to **repair tasks**. Do this as a **reduction** — one canonical packet
artifact, machine-readable QA, no new orchestration/wrapper/meta layers. When in doubt, delete/merge/simplify.

Inherit every non-negotiable from the original repo-scrub brief (delete bloat, production path wins,
tests for confidence, agents emit reviewable JSON artifacts, no parallel packet system, no compatibility
shims). This brief adds specifics.

---

## Ground truth (verified by the phase-1 mapping — read the code, but start here)

### The block is at the CHAPTER-packet + surface-contract layer, NOT the scene layer

The **scene** validator `src/dominion/workers/scene_packet/validation.py` is **already writer-first**: its
module docstring states it hard-blocks only true draft-safety failures (malformed body, unrecoverable
word budget, absent-character-on-page, reader/POV leak, scene-number contradiction) and warns everything
else. Do **not** "fix" it blindly — it's the model for the policy you're applying upstream.

The hard stops the user actually hits are upstream, at chapter-packet proposal:

| Layer | File :: function | Blocking checks |
|---|---|---|
| Chapter contract | `packet/__init__.py :: propose_packet` (fail-closed) driven by `packet/validation.py :: validate_chapter_packet_contract` | `roster_double_bucketed` — a name in two of present/absent/mentioned_only/forbidden (present∩absent, present∩forbidden, mentioned_only∩forbidden) |
| Surface projection | `packet/surface_contract.py :: build_surface_contract` | `forbidden_surface_term_unprojectable`, `forbidden_surface_leak` |
| LLM QA | `packet/qa.py` (+ `scene_packet/qa.py`) | LLM emits `BLOCK_DRAFTING` |
| Scene contract (already writer-first) | `scene_packet/validation.py :: validate_scene_packet_contract` / `evaluate_scene_packet` → `draft_blockers` → `scene_packet/approval_policy.py :: status_after_author_qa` | `word_budget_override`, `scene_no_mismatch`, `absent_character_on_page`, `absent_character_reader_pov_leak` |

### The severity model has only two levels

Both validators use `Severity = Literal["warn", "block"]` (`ScenePacketViolation` /
`ChapterPacket` violation dataclasses). `draft_blockers()` = `severity == "block"`; approval policies
gate on `severity == "block"`. **There is no `repair` / `blocks_final_export` tier** — so a fixable issue
can only be a hard block or an ignorable warning. That binary is the root cause.

### QA issue format is inconsistent (Workstream G target)

Deterministic violations are `{kind, field, detail, severity}`. **Chapter QA issues are `{kind, detail}`
with no guaranteed severity.** `residual_risks` are freeform strings. Production `Issue` rows are yet
another schema. Escalation uses a coarse `RiskLevel` (`risk_scorer.py`) with no per-dimension score.

### There is no single packet artifact — 5+ competing sources of truth

1. `ChapterPacket.body` (JSONB) — the `AuthorPacketInternal` shape from `packet/author.py`. Roster is
   **4 flat string arrays** (`characters_present/absent/mentioned_only/forbidden`) + `scene_seeds[]`,
   `claims[]`, locks, `surface_terms`, `confidence`.
2. `ChapterPacket.body._surface_contract` — a drafter-safe projection embedded *inside* the same body;
   `propose_packet` also overwrites top-level `scene_seeds` from it (`packet/__init__.py`), so scene_seeds
   exists twice in one row.
3. `ChapterPacket.open_questions` — a **sibling column**, not in body (already caused a "resolved ruling
   re-attacked" bug).
4. `ScenePacket.body` — scene-local contract (`known_before_scene`, `learned_during_scene`,
   `must_remain_hidden`, `pov_permissions`, `intentional_mysteries`, `reviewer_*`, `claim_sources`).
5. `Beat` columns + `ChapterSequence.body` + per-run `Artifact` rows re-copying every domain body.

**Good news:** scene packets are *already partly derived views* — `scene_packet/projections.py :: project`
and `scene_packet/derive.py` consume `body._surface_contract`. The architecture is halfway to "derive,
don't duplicate." Finish that; don't add a sixth format.

### Verification reality (READ THIS)

- **No local Postgres** — the deployment is Railway-only (persistent volume, `init_db` at boot). DB-backed
  tests (`db_factory`) run **only in CI**. Do not claim a DB path works from a local run.
- **The severity mechanism IS locally verifiable.** These four validation/approval test files are
  **pure-unit (0 `db_factory`)**: `tests/test_scene_packet_validation.py` (8),
  `tests/test_scene_packet_approval_policy.py` (6), `tests/test_packet_validation.py` (8),
  `tests/test_packet_approval_policy.py` (5). Extend them to lock the new severity semantics; they run
  in <1s without a DB.
- **Behavior-freeze discipline:** every check that stays `block` must keep its existing block test green.
  Only the reclassified checks change. Run the four pure files before/after and diff intent.
- Local gate (Git Bash): `export PATH="/c/Users/Nalakram/.local/bin:$PATH"; export DOMINION_REQUIRE_DB=0;
  export ANTHROPIC_API_KEY=sk-ant-ci-not-a-real-key; uv run --no-sync pytest <pure files> -q`,
  plus `uv run --no-sync ruff check src tests` and `pyright` on changed files. CI is the DB gate.
- `packet/` and `scene_packet/` **mirror each other module-for-module** (author/qa/validation/
  approval_policy/parse). Any severity change must be made in **both** tiers to stay consistent.

---

## Workstream F/G-1 — Repair-task severity tier (DO THIS FIRST; it's the actual unblock)

This is the surgical, locally-verifiable change that makes drafting reachable. Ship it on its own PR
before the artifact refactor.

### Severity model

Extend `Severity` to `Literal["warn", "repair", "block"]` in **both** `packet/validation.py` and
`scene_packet/validation.py`. Semantics:

- **`block`** — true blocker. Stops drafting and everything downstream. Reserve for: schema
  invalid/unparseable body; **direct canon contradiction**; impossible timeline; missing required
  contract field; a contradiction between chapter-level and scene-level requirements; no usable/draftable
  scene purpose. (This is the brief's canonical blocking list — nothing else.)
- **`repair`** — fixable. Does **NOT** block drafting or human review. **Blocks final export.** Emitted as
  a machine-readable repair task routed back to the author agent.
- **`warn`** — advisory. Blocks nothing.

Update the accessors (keep names stable; they're imported widely):
- `draft_blockers()` stays `severity == "block"` **only** → drafting becomes reachable when only
  repair/warn issues remain.
- Add `repair_tasks()` = `severity == "repair"`.
- Add `export_blockers()` = `severity in ("block", "repair")`.
- `draftable` / `is_clean` reflect `not draft_blockers()` (unchanged meaning, new outcome).

Update the approval policies (`packet/approval_policy.py`, `scene_packet/approval_policy.py`,
`status_after_author_qa`): a packet with only `repair`/`warn` issues is **draftable and approvable**
(status `pass_with_warnings` or `approved_with_repairs` — pick one and use it consistently). Only `block`
sets BLOCKED. `gates.py :: GateRefusal` should fire on `block`, not `repair`.

### Machine-readable issue format (Workstream G)

Give every issue a stable serialization (extend the existing `as_dict`). Normalize **chapter** QA issues
to carry `severity` too (they currently don't). Each issue:

```json
{
  "issue_code": "PRESENT_CHARACTER_NOT_VISIBLE",
  "severity": "repair",
  "target": { "chapter_no": 1, "scene_no": 2, "field": "scenes[1].characters_present" },
  "problem": "…",
  "required_change": "…",
  "suggested_patch": {},
  "blocks_drafting": false,
  "blocks_human_review": false,
  "blocks_final_export": true
}
```

`blocks_*` are derived from severity (`block` → drafting+review+export true; `repair` → only export true;
`warn` → all false). Keep it a pure function so it's unit-testable.

### Reclassification (the judgment calls — get these right)

| Check | File | New severity | Why |
|---|---|---|---|
| `roster_double_bucketed` (present∩absent / present∩forbidden / mentioned_only∩forbidden) | `packet/validation.py` | **repair** | A name in two buckets is a fixable data-entry contradiction, not a canon contradiction. Route to the packet author to fix the roster. |
| `forbidden_surface_term_unprojectable`, `forbidden_surface_leak` | `packet/surface_contract.py` | **repair** | Fixable by adjusting surface terms / projection. |
| LLM `BLOCK_DRAFTING` (from `qa.py`) | `packet/qa.py`, `scene_packet/qa.py` | **repair** (max) | The LLM QA is "an attacker good at semantic risk, unreliable at hard facts" (per `scene_packet/validation.py` docstring). **No LLM-driven control path** (original brief). LLM QA may raise repair tasks; it must never hard-block drafting. Only deterministic checks may `block`. |
| `invalid_body` (unparseable), `scene_no_mismatch` | `scene_packet/validation.py` | **stay block** | Structural/true blockers. |
| `absent_character_on_page`, `absent_character_reader_pov_leak`, `word_budget_override` | `scene_packet/validation.py` | **CONFIRM with user, default repair** | These are fixable roster/beat/budget mis-buckets. The scene layer intentionally blocks them today; the brief wants fixable issues to be repair tasks. Default to `repair` but call this out in your report — it's the one place your change alters the already-writer-first scene layer. |

Add the missing positive check the brief names — **`PRESENT_CHARACTER_NOT_VISIBLE`**: a character listed
present/`reader_must_notice` with no visible evidence (dialogue/action/description/thought/named
reference). Emit it as **`repair`** (blocks final export, not drafting), unless the chapter contract
explicitly marks the scene as failing without that evidence. This is a *draft-time* QA check — wire it
where the draft is graded, not into the packet-time deterministic gate.

### Tests (pure, no DB)

Extend the four pure files to assert: a `repair` violation is NOT in `draft_blockers()`, IS in
`export_blockers()` and `repair_tasks()`, serializes with `blocks_drafting=false, blocks_final_export=true`;
approval policy leaves a repair-only packet draftable/approvable; a `block` violation still blocks. Keep at
least one existing block case and one warn case per file (behavior-freeze).

### Acceptance
- A chapter packet with only roster/surface/LLM issues is **draftable and approvable** from the API.
- Packet QA emits machine-readable issue objects with `blocks_drafting/human_review/final_export`.
- No LLM QA path can hard-block drafting.
- The four pure test files pass; CI (DB) green.

---

## Workstream F-2 — One canonical packet artifact (collapse the 5 sources)

Only after F/G-1. This is the "one source of truth" reduction. **Do not add a sixth format.**

- Make `ChapterPacket.body` conform to the **canonical `chapter_master_packet.json` schema** (see the
  original brief's shape: `schema_version`, ids, `pov`, `status`, `source_inputs`, `chapter_contract`,
  `cast[]` with graded `presence`/`reader_must_notice`/`minimum_visible_evidence`, `scenes[]` with
  `visible_character_evidence[]`, `qa{verdict,blocking_issues,warnings,repair_tasks,graded_by,
  last_checked_at}`, `lineage`). The biggest change: the flat 4-string-array roster becomes structured
  `cast[]` objects.
- **Fold, don't add:** move `ChapterPacket.open_questions` (sibling column) **into** the body's contract;
  keep `_surface_contract` as an explicitly-**derived** projection (document it's derived, not
  authoritative; stop the double-write of `scene_seeds`).
- **Scene packets stay derived views** from the master (they already partly are via
  `scene_packet/projections.py`). Delete any path that treats a scene packet as an independent source.
- Provide: a JSON Schema file, a pure `validate_master_packet()` function, a pure unit test, and a
  migration/back-compat read for existing `ChapterPacket.body` rows (idempotent, in
  `shared/migrations.py` if a column changes; the body is JSONB so prefer a tolerant reader over a hard
  migration).
- Persist/inspect: expose the raw canonical JSON via the API so the UI can show/download it (Workstream I
  + the Packets tab gap).
- **Optional (higher-risk, only if it removes net code):** unify the mirrored `packet/` ↔ `scene_packet/`
  author/qa/validation/approval_policy modules by parametrizing on tier. The map flagged this as the only
  path below ~50 worker files but "highest risk, do last, behavior-freeze + full pytest required." Skip
  unless it clearly deletes more than it adds.

### Acceptance
- One canonical schema; existing packet paths use it or are deleted; scene packets are derived, not a
  second source. Human can inspect/approve/edit/retire the artifact from the UI. Agents can grade the JSON
  without hidden context.

---

## Workstream G — Grading (fold into existing QA, no new subsystem)

The graders must emit the Workstream-G score object
(`{artifact_id, artifact_type, schema_version, grader, verdict, score{overall, canon_consistency,
reader_clarity, scene_utility, specificity, non_contradiction, actionability}, blocking_issues, warnings,
repair_tasks, approved_for_next_stage}`) — but implement it **inside `packet/qa.py` / `scene_packet/qa.py`**,
not as a new agent framework. Scoring bands: `pass` overall ≥90 & no blockers; `pass_with_warnings` ≥80 &
no blockers; `revise_required` 60–79 or repair tasks; `fail` <60 or canon/contract contradiction. Blocking
reserved for the brief's list (canon contradiction, impossible timeline, missing required field, schema
invalid, unparseable, chapter↔scene contradiction, no draftable scene purpose).

---

## Workstream I — Production tab unblock (frontend; depends on F/G-1)

`frontend/src/desk/screens/ProductionScreen.tsx` today: when the approved-packet precondition is unmet,
`startRun`/`loadRuns` throw and it dumps the raw `e.message` into a red banner (~L277–285); a `blocked`
run status maps to red (`statusTone()` ~L52–54) as a terminal chip **with no remediation, no guidance, no
link back to Packets**. That dead-end is the whole problem.

- Replace the raw-error banner with a **structured blocked state**: show *why* (the packet blockers /
  repair tasks from the machine-readable QA), and a direct action/link to the **Packets** tab to
  create/repair/QA/approve the required packet. Reuse `frontend/src/desk/lib/packetBlockers.ts` (packets
  already have a blockers helper; production has none) rather than writing a parallel one.
- Allow **approve-with-warnings/repairs** where the packet is structurally valid with only repair/warn
  issues.
- Surface repair tasks as actionable items (they're machine-readable now).
- Add a raw `chapter_master_packet.json` view/download in `PacketsScreen.tsx` (the Packets UI has
  inspect/approve/edit but no canonical-JSON view; retire is hard-delete only — add soft retire wired to
  the existing `markScenePacketsStale`/new retire endpoint).
- Tests: extend the existing `ProductionScreen.test.tsx` / `PacketsScreen.test.tsx` (vitest, no DB) to
  assert the blocked state renders remediation + links, and approve-with-warnings calls the right method.

### Acceptance
- From a chapter outline, a user reaches a draftable state in the browser. The Production tab explains any
  block and offers a repair path — **no dead-end "blocked" with no way forward.**

---

## Workstream K — Docs truthing (cheap; can run in parallel any time)

Make the docs match the shipped production path (post-phase-1). Prefer editing over adding.

- **README.md:** diagram says "React (Vite)" → it's a **Next.js BFF** (contradicts its own Running-it
  section); layout/CLI examples point to `workers/enqueue` (**deleted** in phase 1 — `learning` promoted,
  legacy CLIs removed); presents **beat-first** drafting as the loop → it's **contract-first** (see
  `docs/contract_first_drafting.md`); dev section shows `mypy` → CI uses **pyright**; drop the
  `src/legacy/` review-app note.
- **DESIGN.md:** §1/§2/§13 topology is obsolete (Astro showcase, separate repos, Fly/Render/VPS) → single
  Railway container; still frames drafting beat-first → contract-first.
- **BUILD.md:** stale `python -m dominion.workers.enqueue` example; beat-first flow; mypy-not-pyright.
- **ROADMAP.md:** predates contract-first packets — update or archive (non-RAG-ingested).
- Do **not** touch `series/`/`book1/` canon markdown (author content). Docs are not RAG-ingested
  (ingestion is scoped to `series/canon` + `book1/manuscript/scenes`), so this is safe.

---

## Suggested agent choreography

1. **F/G-1 severity tier** (one agent, one PR) — pure-test-verifiable; highest value; unblocks drafting.
   Land and CI-green before anything else.
2. In parallel after (1) lands: **I production tab** (frontend agent) and **K docs** (cheap, independent).
3. **F-2 canonical artifact** (one agent) — depends on (1); the reduction pass. Optional packet/scene
   unification only if it deletes net code.
4. **G grading** folds into (1)/(F-2)'s QA modules — not a separate agent unless scoped tightly.

Each patch agent works on its own branch, verifies with the pure tests + ruff + pyright locally, and
relies on the PR's CI for DB-backed and e2e validation. Do not run parallel agents that edit the same
`test_*_validation`/`approval_policy` files (they'll conflict) — sequence packet-touching work.

## Required final report (every patch agent)

Return the original brief's patch-agent JSON: `{summary, files_deleted, files_moved, files_modified,
files_added, net_file_count_change, test_count_before, test_count_after, test_runtime_before_seconds,
test_runtime_after_seconds, ci_expected_impact, railway_expected_impact, production_paths_preserved,
legacy_paths_removed_from_agent_reach, json_artifacts_added_or_changed, risks, manual_followups}`. Note
explicitly any check you reclassified from `block`→`repair` and whether the user confirmed the scene-layer
ones.

## Definition of done

- Drafting is reachable from a chapter outline in the browser; repairable issues become repair tasks, not
  hard stops; only the brief's true-blocker list hard-blocks.
- One canonical packet artifact; scene packets derived from it; QA emits machine-readable graded issues.
- No new wrapper/orchestration/meta layer; net code preferably down.
- Pure validation/approval tests green locally; PR CI (DB + e2e) green. No stale docs left behind.
