# Pipeline recovery — final integration summary

Branch: `pipeline-recovery-ch1-sequence-budget` · 65 files changed, +7,255 / −229.
Ten parallel lanes, each in an isolated worktree from baseline `4b9b282`, integrated by the
coordinator with three manual conflict resolutions (triage L5×L6 composition, drafter-contract
L1×L2 union, chapter-QA L4×L2 union) and three harness adapter alignments.

## Root causes fixed (each with the lane, the mechanism, and the proof)

1. **Entry-state chaining (L1)** — the sequence author stamped the global chapter entry into
   every scene seed and nothing rewrote it; separately, the drafter never received ANY opening
   state (`entry_state` was dropped by projections, `prior_exit_state` assembled but unused).
   Fix: deterministic `chain_scene_entry_states` post-pass on every derive + the drafter prompt
   now opens FROM the chained state. Proof: `tests/test_sequence_chaining.py` (8) + harness.
2. **Scene bleed / duplicate beats (L2)** — `beat_ownership` was authored but dead on arrival:
   never rendered into prompts, chapter QA's enforcement was a placeholder. Fix: new
   `workers/scene_scope.py` (patterns derived from the beat text itself), drafter MUST/MUST-NOT
   ownership blocks, chapter QA emits `scene_scope_bleed` / `duplicate_irreversible_beat`
   (block-severity when irreversible). Proof: 13 lane tests + harness on real fixture prose.
3. **Budget contradiction (L3)** — the planner emitted per-scene `hard_max = 1.6×target` and
   nothing ever compared the sum (10,400) to the chapter envelope (7,200). Fix: new
   `workers/budget_reconciliation.py` — chapter envelope is authoritative, scenes scale down
   floor-anchored proportionally (fixture → 7,199 ≤ 7,200); impossible envelopes yield ONE
   blocking `sequence_budget_mismatch` surfaced pre-LLM through draft readiness. Proof: 18 lane
   tests + harness.
4. **Canon leak (L4)** — the No-Eyes prohibition lived only in ruling free text while
   `surface_terms` listed "Neurochromatic Eyes" as the ALLOWED name; chapter QA's prose-vs-
   contract check was a placeholder comment. Fix: new `workers/canon_guards.py` derives
   prohibitions from the packet's own fields in 3 tiers; block findings gate `final_chapter`;
   scene QA prompts carry an explicit prohibited-terms block. Proof: 13 lane tests + the
   harness flags the exact missed sentence in the real draft.
5. **Repair swarm (L5)** — 24 issues → 10 scattered tasks from 3 root causes. Fix: new
   `workers/repair_triage.py` clusters accepted issues by root cause; ONE chapter-scoped task
   per structural cluster; prose polish deferred behind open structural work;
   `infra_rate_limit` never creates tasks. Proof: the fixture's real issue set collapses to
   exactly 3 root tasks + 8 deferred prose (7 lane tests).
6. **Order enforcement (L6)** — six violations traced, incl. assembly running against a
   sequence already marked `block_drafting`. Fix: new `workers/run_stages.py` + pinned stage
   strings; assembly refuses on missing prose (event + `waiting_for_scene_drafts`); structural
   QA findings park the run in `structural_repair_required`; drafting gate refuses on blocked
   sequence / stale packets / budget mismatch before LLM spend. Proof: 16 lane tests.
7. **429 misclassification (L7)** — 429s were minted as literary issues (pacing/combat) by
   reviewers and left runs looking pipeline-broken. Fix: `classify_job_failure` (any chained
   429 → `infra_rate_limit`, run parks on `provider_rate_limited`), escalation keeps a usable
   primary on fallback-429, retry telemetry (retries / requested tokens / rate-limit headers).
   Proof: 15 mocked-429 tests; full serial suite 298/298 in-lane.
8. **UI lying (L8)** — ready badges bound to a different field than the disable logic. Fix:
   pure `resolve_draft_gate` — `can_draft`/`disabled_reason` consistent by construction, naming
   the FIRST failing gate in pipeline order; expandable "Why is this disabled?" diagnostics;
   `DraftReadinessOut` + `StructuralBlockerOut` schema additions (codegen regenerated). Button
   labels: "Draft scenes" drafts, "Assemble chapter" stitches. Proof: 29 lane tests.
9. **Tab latency (L9)** — 669KB run detail refetched per visit; 2MB canon per load; a bonus
   root cause (unstable `onBookChange` re-arming bootstrap fan-outs every render). Fix: session
   caches keyed on `updated_at`/book, slim refetches after actions, once-per-session canon
   upgrade, `[desk:tab-load]` instrumentation. Proof: L9 tests pass in the integrated tree.
10. **Regression harness (L10)** — 8 always-on canaries pinning the fixture's bugs + 7
    acceptance tests over the pinned interfaces. **15/15 green post-integration.**

## Verification (integrated branch)

- Regression harness: **15/15** (zero skips — every lane's acceptance criteria live)
- Lane suites: chaining 8 · scope 13 · budget 18(+20 wiring) · canon 13 · triage 7 ·
  orchestration 16 · rate-limit 15 · readiness gates 29
- ruff check: clean · ruff format --check: clean (195 files) · pyright: 0 errors on all 19
  changed src files
- Frontend: tsc clean · oxlint 0 errors · oxfmt clean · vitest **179/179**
- Full backend pytest: see PR checks (DB-backed subset requires Postgres; CI runs it)

## Known limitations

- Guards are deterministic keyword/pattern scanners derived from contract text — they catch
  the observed failure classes (and near variants), not arbitrary paraphrase; LLM QA remains
  the semantic layer above them.
- In-chapter *timed* prohibitions (allowed after a reveal beat) are conservatively skipped by
  the canon guard rather than position-checked (documented follow-up in L4's report).
- The existing Ch1 scene packets/sequence PRE-DATE these fixes: they still carry un-chained
  entry states and unreconciled budgets. **Re-derive is required** (see below) — re-approve
  would freeze the broken contracts.

## Exact manual steps to re-run Chapter 1

1. Merge the PR; wait for the Railway deploy to go green.
2. Hard-refresh the Desk (Ctrl+Shift+R).
3. Packets tab → Chapter 1 → **delete/retire the existing scene packets** and **re-derive**
   (the derive now chains entry states, reconciles budgets to the 7,200 envelope, and injects
   owned/forbidden beats). Do NOT just re-approve the old packets.
4. Review + **Approve all** scene packets (the gate diagnostics show exactly what's missing
   if the button is disabled).
5. **Draft scenes** (scenes draft sequentially, each opening from the previous exit state).
6. Production tab → **Assemble chapter** (refuses with a clear reason if prose is missing).
7. Chapter QA runs the canon/scope/budget guards; triage clusters anything found into at most
   a few root repair tasks — or the run parks in `structural_repair_required` with one clear
   blocker. Use **Run JSON** to inspect the full state after each step.
