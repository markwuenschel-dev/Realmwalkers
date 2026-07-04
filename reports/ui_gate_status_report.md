# L8 — Authoritative draft-gate diagnostics end to end

Date: 2026-07-04 · Branch: `lane8/gate-diagnostics` (baseline `4b9b282`)
Mission: make the UI stop lying — no more "ready to draft" beside a disabled button, no disabled
action without a stated reason (Ch1 failure analysis §6).

## What changed

### Backend — `src/dominion/workers/draft_readiness.py`

- **`resolve_draft_gate(DraftGateInputs) -> (can_draft, disabled_reason)`** — a pure function over
  counts (no ORM/DB), so the gate ordering is unit-testable. Mutually consistent by construction:
  `can_draft is True` ⇔ `disabled_reason is None`. When False, the reason is ONE human sentence
  naming the FIRST failing gate in fixed pipeline order:

  1. chapter packet approved
  2. sequence/budget + structural contract faults
  3. scene packets — coverage, then stale, then QA `block_drafting`
  4. beats — derived, linked, no queue blockers
  5. active draft jobs (never double-queue)
  6. prose coverage (nothing left to draft → redraft is the path)
  7. provider rate limit (checked LAST so a real contract fault is never misreported as a 429)

- **Structural detectors** (pure, cheap — operate on rows the readiness query already loads; ONE
  new query total, the chapter sequence):
  - `sequence_budget_mismatch` — sequence scene count vs packet seed count; Σ scene-packet
    `word_budget.hard_max` vs sequence `hard_max_words` (the Ch1 §3 10,400-vs-7,200 arithmetic).
  - `scene_scope_bleed` — an approved beat linked to another scene's packet.
  - `duplicate_irreversible_beat` — >1 approved beat on one scene_no; the same (normalized)
    `irreversible_state_change` seeded into two scenes (Ch1 §2).
  - `canon_contract_leak` — a scene contract's `reader_must_learn`/`reader_may_learn` item matching
    the chapter packet's `forbidden_reveals`/`forbidden_knowledge`, or the same contract's own
    `must_remain_hidden` (Ch1 §4).

- `compute_draft_readiness` populates the pinned flat fields (counts only, no prose loads) and
  keeps every existing field. `draftable` is unchanged (legacy queueability; `can_draft` implies
  `draftable` but is stricter). `disabled_reason` now always comes from `resolve_draft_gate`.

### DECLARED SCHEMA CHANGE — `src/dominion/shared/schemas.py` (integrator: regenerate `openapi.json` + `frontend/src/desk/api/generated.ts`)

New model `StructuralBlockerOut { kind: str, message: str }` with kinds
`sequence_budget_mismatch | scene_scope_bleed | duplicate_irreversible_beat | canon_contract_leak`.

`DraftReadinessOut` gains (all defaulted — additive, non-breaking):

| field | type | meaning |
|---|---|---|
| `scene_packets_stale` | int | count of STALE scene packets |
| `scene_packet_qa_blocking` | int | packets with QA verdict `block_drafting` (rate-limited excluded) |
| `active_draft_jobs` | int | QUEUED/RUNNING draft jobs for the chapter |
| `missing_scene_drafts` | list[int] | scene_nos whose latest scene row has no non-empty prose |
| `structural_blockers` | list[StructuralBlockerOut] | deterministic contract faults (above) |
| `provider_rate_limited` | bool | any scene packet held in RATE_LIMITED |
| `can_draft` | bool | THE authoritative gate for draft actions and "ready" badges |

Existing fields (`chapter_packet_approved`, `scene_packets.*`, `beats`, `jobs`, `prose`,
`draftable`, `disabled_reason`, `blockers`) are untouched. Frontend code is written against the
new fields via the hand-maintained `DraftReadinessOut` in `frontend/src/desk/api/types.ts`, so it
compiles before codegen runs.

### Frontend

- `components/ScenePacketsPanel.tsx`
  - The "ready to draft scenes." headline and the **Draft scenes** button now bind to
    `readiness.can_draft` (never `draftable`) — a "ready" state cannot render while the button is
    disabled, and the disabled button's tooltip is always `disabled_reason`.
  - New `DraftGateDiagnostics` block, rendered ONLY while the action is disabled: collapsed it
    shows "Why is this disabled?" + the server's one-sentence reason; expanded it lists all 7
    gates with pass/fail chips (reusing the card Chip idiom) in backend order, plus structural
    blocker messages and the existing **Re-link beats** remediation. The three status axes stay
    distinct: `· contract`, `· Scene QA`, `· prose` labels mirror the per-card chips.
- `screens/ProductionScreen.tsx`
  - Start button already said **Assemble chapter** (verified; it only stitches existing prose) and
    the in-banner nav button already said **Draft scenes** — labels consistent across
    PacketsScreen/ScenePacketsPanel ("Draft scenes" = queues scene drafting; "Assemble chapter" =
    concatenates existing prose; no ambiguous "Draft chapter" remains anywhere in `frontend/src`).
  - New `AssemblyGateDiagnostics` expandable inside the assembly-gate banner: pass/fail rows for
    chapter packet, prose coverage (`assembly_ready` + missing scene list), active draft jobs, and
    provider rate limit.
- `components/telemetry/TelemetryDrawer.tsx` — the chapter "Draft not ready" panel binds to
  `can_draft` and leads with `disabled_reason` (previously it could stay silent when
  `draftable=true` masked a failing gate, and showed "0 blocker(s)" for non-blocker gates).
- Test mocks updated for the new required fields; new ProductionScreen test:
  "explains a blocked assembly with expandable per-gate diagnostics".

### Tests — `tests/test_draft_readiness_gates.py` (29 passed)

Pure, no DB: every gate failing alone yields its own reason; all passing yields
`(True, None)`; first-failing-gate ordering (packet ≻ structural ≻ stale ≻ jobs ≻ rate limit…);
a consistency sweep asserting `can_draft == (reason is None)` across all variants; structural
detectors covered, including `sequence_budget_mismatch` reproduced from the real
`tests/fixtures/ch1_bad_run/` numbers (6-vs-4 scenes, 10,400-vs-7,200 words).

## Shared-file note (lanes 3/6)

`draft_readiness.py` additions are separable: the pure-gate section (helpers + `DraftGateInputs` +
`resolve_draft_gate`) sits between `blocker_out` and `compute_draft_readiness`; inside
`compute_draft_readiness` the new code is the marked "structural blockers (recovery L8)" block,
the `qa_blocking` count, the `resolve_draft_gate` call replacing the old inline disabled_reason
chain, and the new kwargs in the returned `DraftReadinessOut`. Lane 3 (budget reconciliation) may
want to move `sequence_budget_blockers`'s inputs onto its reconciled numbers — the helper is pure,
so only the wiring block needs touching.

## Verification

- `pytest tests/test_draft_readiness_gates.py` — 29 passed.
- `ruff check` + `ruff format --check` + `pyright` clean on all touched backend files.
- Frontend written against existing idioms; integrator runs oxfmt/tsc/vitest + codegen
  (`export_openapi.py` + `pnpm codegen`) — `generated.ts` in this branch still carries the old
  `DraftReadinessOut` description block and must be regenerated.
