# ADR-0033: Convergence — the stopping condition for unattended work

**Status:** proposed · **implementation_authorized:** false · **Decision owner:** mark
**Charts:** issue #220 (`wayfinder:grilling`, "Convergence, concretely"), under map #213.
**Refines** ADR-0030's `Editorial Convergence` and the `CONTEXT.md` glossary entry of that name.

**Revision history**
- **v2 (2026-07-25) — F2 CLOSED by author ruling: `S = Severity.INFO`.** D2 rewritten from a fork into a
  decision, with the accepted cost stated (advisory `warn` findings now drive unattended rewriting, so the
  attempt cap becomes load-bearing rather than a backstop). D2a added: the open-status set resolved
  **factually** from each contested status's only write site, not ruled — and `_OPEN_FIDELITY_STATUSES` is
  explicitly NOT folded into it, because it answers a different question. F1 and F3 remain open, so
  `implementation_authorized` stays false.
- v1 (2026-07-25) — first draft, authored during the ADR-0031 A1c build. Records what is **settled** and
  isolates **three blocking items** (F1 name, F2 threshold, F3 reviewer set) that are the author's to rule
  on. `implementation_authorized` stays **false** until F1–F3 close: a driver with an unpinned stopping
  condition has no mandate, and #220's whole question is precisely those three.

> **Why this is not yet authorized.** ADR-0031 D10 makes "no decision" a blocking state, so the open items
> below are recorded rather than defaulted. **F2 is now closed** (D2). Two remain: **F1** is a naming
> decision and is author-only; **F3** has a verified factual answer for *which* reviewers run, but a
> genuine design fork on whether they may trust a contract no human read — see D5. F3 became more
> consequential, not less, when F2 chose `info`: every reviewer now drives the loop.

## Decision (the settled core)

**Convergence is a stopping condition on open Issues, evaluated per repair cycle, bounded by a persisted
attempt cap.** It is deliberately **not** a score, **not** a QA verdict, and **not** human approval. It
says only: *the machine has nothing further it knows how to fix here.* The human still decides whether the
result is good — ADR-0030's "the machine finishes the work; the author finishes the decision".

Three things it is measured against, kept strictly apart because they gate different objects:

| Gate | Object it bounds | Effect when it trips | Owner |
|---|---|---|---|
| **Threshold S** = `info` | the prose, via open Issues | the converge loop keeps running | this ADR, D2 · **settled** |
| **Attempt cap** | the loop itself | the cycle parks, terminally, for a human | D7 / T2 (#230) |
| **Approval Blocker** | one ScenePacket's *contract* | approval is refused until resolved | ADR-0031 D14, live |

**S bounds convergence; a blocker bounds approval.** A scene can be converged and unapprovable (S is met,
an active blocker holds the contract), or approved and unconverged (contract fine, prose Issues remain).
They are not a hierarchy and neither implies the other.

## Context (verified at HEAD `d664f87`; every citation opened this session)

1. **There is no convergence function.** Convergence is emergent across `sweeper.py` ticks: apply →
   revision Job → next tick verifies → `NEEDS_ANOTHER_REPAIR` re-queues (`production_repair.py:1350`).
2. **The verification stopping condition is severity-BLIND.** `production_repair.py:940` reads
   `no_new_issues = not remaining and not created_new_issues`. **Any** new critique — including `info` —
   prevents ACCEPT. No threshold is applied there; the effective S at verification is "nothing at all".
3. **Severity is consulted at verification for exactly one thing:** choosing `ESCALATE_TO_HUMAN` over
   `NEEDS_ANOTHER_REPAIR`, via `any(is_blocking(issue.severity) ...)` (`production_repair.py:947`) —
   `is_blocking` is `block` only (`shared/severity.py:50`). `RepairVerificationVerdict.REJECT` is defined
   (`enums.py:407`) and **assigned nowhere**; the live loop has three outcomes, and
   `NEEDS_ANOTHER_REPAIR` is the `else` branch.
4. **But TRIAGE already applies a threshold, and it is a different one.**
   `production_repair.py:362-374`: `elif issue.severity == "info": ... REJECT` — *"Info-level notes stay
   advisory and do not create repair work; warn/repair/block are accepted."* So an `info` Issue never
   becomes repair work, **yet an `info` critique at verification still blocks ACCEPT and re-queues the
   task** (fact 2). The loop can therefore be kept alive indefinitely by findings triage has already
   ruled non-actionable. (Also worth fixing when S lands: that compare is a raw `== "info"` and bypasses
   `shared/severity.normalize_severity`.)
5. **Reviewers are mostly, but NOT entirely, clamped to advisory.** `advisory_severity`
   (`workers/reviewers/base.py:28`) returns `WARN` or `INFO` and its docstring says *"Reviewers never emit
   BLOCK"* — but it is called by only six of the seven: `lane.py:103` (combat/sensory/dialogue),
   `pacing.py:66`, `state_drift.py:72`, `voice.py:68`. **Continuity bypasses it**: `continuity.py:172`
   emits `Severity.BLOCK` for a hard-number ledger contradiction (`:215` emits `WARN` for the knowledge
   check). The pipeline also writes non-reviewer BLOCK critiques — the length guard (`pipeline.py:217`)
   and budget-exceeded (`pipeline.py:313`). A `Flag` (`base.py:35`) carries `reviewer, severity, note,
   payload` and nothing else.
6. **The reviewer set is deterministic and already exists.** `router.reviewers_for(tags)`
   (`workers/router.py:46-51`) = `ALWAYS_REVIEWERS` — continuity, voice, pacing, state_drift (`:28-33`) —
   plus tag-gated combat / sensory / dialogue (`:37`), where the tags come from the Beat row
   (`context/assemble.py:41`), not `scene_type`. Selection is a lookup table, never an LLM
   (`router.py:1-5`). One call site: `workers/pipeline.py:249`, reached for **both** draft and revision
   jobs — so a repair's revision re-runs the same set. `ReviewerKind` (`enums.py:96`) is **not** the live
   vocabulary; nothing dispatches on it.
7. **Flag → Issue is already one path.** Flags persist as `Critique` rows (`pipeline.py:290-302`);
   `workers/production.py:395` and `production_repair.py:918` call `support.create_issue` with
   `validator=critique.reviewer`, `issue_kind = payload["kind"] or reviewer`,
   `severity = str(critique.severity)` (verbatim passthrough, no clamp), deduped by
   `support.issue_signature`. `create_issue` (`production_support.py:246`) is the **only** `Issue(`
   constructor in `src`.
8. **The severity bands are therefore split by producer, not by importance:**

   | Producer | Severity it can write on an Issue | Citation |
   |---|---|---|
   | voice, pacing, state_drift, combat, sensory, dialogue | `info` \| `warn` only | `reviewers/base.py:28` |
   | continuity | `warn`, and `block` for the hard-number check | `continuity.py:215`, `:172` |
   | `scene_fidelity` | `repair` | `production_fidelity.py:198` |
   | `chapter_assembly` (missing scene) | `block` | `production.py:434` |
   | `scene_scope` | `block` if irreversible else `repair` | `production_sequence.py:976`, `scene_scope.py:347` |

   **`repair` never reaches an Issue from a reviewer.** This is the fact F2 turns on: under `S = warn`
   ("no open Issue *above* warn"), six of the seven reviewers become invisible to the stopping condition
   — their findings could never keep the loop running. Only continuity's hard-number `block` would.
9. **There is no shared "is this Issue open" predicate**, and the three local definitions disagree:
   `production_sequence.py:904-910` (includes `REPAIRED`, omits `MERGED`), `production_fidelity.py:67-75`
   (includes `MERGED`, omits `REPAIRED`), `pipeline_status.py:457` (`PROPOSED | ESCALATED` only). The
   analogous predicate *does* exist for repair tasks (`production_repair.py:95-109`).
10. **Nothing bounds attempts.** `RepairAttempt.attempt_no` is read only for `max()+1` and `order_by`
    (`production_repair.py:557,582,801,807,1032,1172`) — never compared to a cap. `sweeper._attempts`
    (`sweeper.py:80`) is process-local, keyed by `run_id`, and `drain_queued_repair_tasks` never consults
    it. `RepairTask` has **no** terminal-reason column; park reasons are recoverable only by scanning
    `AgentEvent` rows, and the conflict park (`production_repair.py:737-741`) writes no event of its own.
11. **The scene-tier escalation channel is now live.** A1c's ApprovalBlocker has an automatic producer:
   `scene_packet/blockers.automatic_hold_for_qa` raises source `canon_conflict` from the derive path when
   scene QA reports a `risk_scorer.CANON_CONFLICT_KINDS` finding, and the one approval seam
   (`scene_packet/__init__.py:138`) refuses while it is active.

## Design

### D1 — Convergence is Issue-resolution, not scoring — [SETTLED]

Carried unchanged from ADR-0030 and #213's "Out of scope": fidelity **scoring** is a separate side
project and is not the done-signal. The unit is the **repair cycle**, not the `RepairAttempt` row — a
chapter-scoped repair mints one attempt per member scene (`production_repair.py:576-601`), so counting
rows would exhaust a cap of three on a three-scene chapter's first action.

### D2 — Threshold S — [**SETTLED** · author ruling 2026-07-25 · closes F2]

> **S = `Severity.INFO`.** A scene is converged when it has **no open Issue with a severity above
> `info`** — `warn`, `repair` and `block` keep the loop running; `info` does not. Pinned to the enum
> member (`shared/enums.py:84`), not to prose.

Grounds recorded with the ruling:

- **It makes the two gates the system already has agree.** Triage rejects exactly `info` as
  non-actionable — *"Info-level notes stay advisory and do not create repair work"*
  (`production_repair.py:367-370`) — while verification is severity-blind and re-queues on any new
  critique including `info` (`:940`). S = `info` closes that asymmetry: a finding triage refuses to act
  on can no longer keep the task alive forever (fact 4).
- **It keeps all seven reviewers in the loop.** Six of them cannot exceed `warn` (fact 8), so any higher
  S would have retired voice, pacing, state-drift, combat, sensory and dialogue from the stopping
  condition and quietly demoted them to a human-facing signal.

**Accepted cost, stated so it is not rediscovered as a surprise.** Reviewers are advisory by design
(DESIGN §9), and S = `info` lets a `warn` drive unattended rewriting — advice becomes instruction. A
scene whose reviewers keep producing fresh `warn` findings will therefore be ended by the **attempt cap**
(D4), not by the threshold. That makes D4 load-bearing rather than a backstop, and it makes the cap's
terminal reason the operator's main signal for "this scene argued with itself until it ran out of
attempts". Rejected alternatives: `warn` (converged would equal exportable, but at the cost above);
`repair` (weaker still, and it makes `repair`-severity `scene_fidelity` findings non-blocking, which
contradicts ADR-0020's fidelity-issue lifecycle).

#### D2a — Which Issue statuses count as OPEN — [SETTLED · resolved factually, not ruled]

A threshold cannot be evaluated without this set, and fact 9 records that no shared predicate exists and
three local definitions disagree. Both contested members resolve from their **only** write site, so this
was a factual question, not a fork:

| Status | Convergence | Why |
|---|---|---|
| `PROPOSED`, `ACCEPTED`, `REPAIR_QUEUED`, `ESCALATED` | **open** | undecided, or decided and unfinished |
| `REPAIRED` | **open** | its one writer's own comment (`production_fidelity.py:396`): *"The source Issue's evidence is now stale; mark it REPAIRED (fresh evaluation VERIFIES or re-opens it)."* Awaiting evaluation is not resolved. |
| `MERGED` | **closed** | its one writer (`production_repair.py:1385-1394`) folds the claim into a target Issue's repair task via `_append_merged_issue_to_task`. The target carries the work; counting both double-counts one defect. |
| `REJECTED`, `VERIFIED`, `FALSE_POSITIVE`, `OVERRIDDEN`, `SUPERSEDED` | **closed** | terminal by construction |

So `CONVERGENCE_OPEN = {PROPOSED, ACCEPTED, REPAIR_QUEUED, REPAIRED, ESCALATED}` — which is exactly
`production_sequence.py:904-910`'s existing set. It belongs in one shared frozenset with an explicit
complement, modelled on the repair-task partition (`production_repair.py:95-109`) and its test.

**`_OPEN_FIDELITY_STATUSES` (`production_fidelity.py:67-75`) is NOT thereby wrong and must not be
merged into it.** It answers a different question — *is this clause's defect already tracked?*
(`:119`, a dedupe guard) — for which a `MERGED` issue correctly still counts. "Is there work left" and
"is this already tracked" legitimately disagree on `MERGED`. Two named sets, not one; the divergence
fact 9 records is only a defect where the same question is answered twice.

### D3 — S versus an Approval Blocker — [SETTLED]

Stated in the Decision table above. Restated as an invariant so no implementation collapses them: **the
converge loop MUST NOT consult ApprovalBlocker state, and the approval seam MUST NOT consult S.** They
operate on different objects (prose vs contract) and have different remedies (repair vs a human ruling
with a rationale). A blocker that also stopped convergence would leave the prose frozen mid-repair while
waiting on a contract question; an S that also permitted approval would approve a contract nobody ruled on.

### D4 — The cap — [SETTLED · verbatim from ADR-0031 D7; **define only**]

> **One persisted maximum of three repair attempts per repair cycle.** At the third failed attempt: do not
> enqueue a fourth automatic repair; park deterministically; persist the terminal reason; expose it to
> operators; remain idempotent across restart; require **explicit human action** to reopen a cycle. **No
> multi-tier automatic ladder in this milestone.** Any escalation beyond parking for human review is out
> of scope.

Enforcement is **T2 / #230** and is a non-goal here. This ADR only fixes that the cap is part of the
definition of convergence: an unconverged cycle that hits the cap is *parked*, never *converged*. The two
terminal states must be distinguishable in the record — fact 10 says no column carries a terminal reason
today, so T2 adds one.

### D5 — Reviewer set against an auto-derived contract — [OPEN · **F3**, blocking]

*Which* reviewers run is **not** an open question: fact 6 answers it. `reviewers_for(tags)` is
deterministic, already implemented, and needs no autonomy-specific variant — continuity, voice, pacing and
state-drift always; combat, sensory, dialogue when the beat carries the tag.

The real fork is what an **auto-derived** contract does to their inputs. Every lane reviewer reads
`ctx.reviewer_contract` — `scene_job`, `required_beats`, `forbidden_beats`,
`reviewer_false_positive_traps`, `reviewer_instructions` (`reviewers/lane.py:34-59`). Under ADR-0030 those
fields are written by the ScenePacket author from evidence, with **no human reading them**. So:

- **(a) Trust the derived contract.** Reviewers consume it exactly as today. Simple; but a drifted
  contract's `reviewer_false_positive_traps` silently suppresses the very findings that would expose the
  drift, and `forbidden_beats` tells reviewers not to ask for things the human never approved.
- **(b) Run lanes contract-blind under autonomy.** Reviewers see prose plus scene job only, ignoring the
  suppression fields until a human has read the contract once. Loses precision (more false positives),
  gains the property that a bad derivation cannot silence its own detector.
- **(c) Split the contract by trust.** Positive fields (`scene_job`, `required_beats`) are honoured;
  *suppression* fields (`false_positive_traps`, `forbidden_beats`) are honoured only on a human-approved
  contract. More machinery; keeps precision where it is safe.

**Flag → Issue needs no new design** (fact 7): the path is live, single-sited, and signature-deduped.

**F2's ruling raised the stakes here.** With `S = info` (D2), every reviewer's `warn` findings keep the
converge loop running — so a `reviewer_false_positive_traps` entry in an auto-derived contract does not
merely hide a note from a human, it *ends the loop early* by suppressing the findings that would have kept
it going. Under the rejected `S = warn` this fork would have shrunk to continuity alone; under `info` it
applies to all seven lanes. F3 is now the last substantive item before a driver can be authorized.

### D6 — Name — [OPEN · **F1**, blocking, author-only]

`CONTEXT.md` carries **Editorial Convergence** _(proposed — pending author-blessed name)_. Blessing or
replacing it is the author's call and is not made here. For the record, the two things the name has to
survive: it is not "editorial" in the human sense (no editor is involved), and it is not "done" (the human
gate is still ahead of it). Candidates the author may pick from or ignore: keep **Editorial Convergence**;
**Machine Convergence**; **Repair Convergence**; **Converged (review-ready)**. The `_(proposed)_` tag comes
off `CONTEXT.md` when, and only when, this is ruled.

## Non-goals

T2/#230 cap **enforcement**; T3/#231 scheduler contract; T4 revision lifecycle; T5 adoption; T6 locking;
T7 migration; Layer 2 adjudication; autonomous replacement drafts (ADR-0031 D17); fidelity scoring;
the driver itself (#228). This ADR defines the stopping condition and nothing else.

## Consequences

- Until F1 and F3 close, **no driver may be authorized**: `implementation_authorized: false` here is the
  reason, and it is deliberate rather than an oversight.
- `S = info` is a **narrowing** of today's verification behaviour (fact 2 — today any single `info`
  critique keeps the loop alive), so the first implementation must ship with the before/after visible in
  a test at each band, not only at `info`.
- **`S = info` makes D4's attempt cap load-bearing.** A scene whose reviewers keep producing fresh `warn`
  findings is ended by the cap, not by the threshold — so T2/#230's persisted terminal reason stops being
  an operator nicety and becomes the primary signal for "this scene never settled". T2 should be
  sequenced before, not after, any driver that runs unattended.
- The three-gate separation in D3 constrains T2: the attempt cap parks a *cycle*; it must not resolve an
  ApprovalBlocker, and resolving a blocker must not reset an attempt budget.
- ADR-0030's "Layer 1 (objective floor)" is now half-live and this ADR does not complete it: the scene
  tier escalates a *reported* canon conflict (fact 11) but still does not consult `ClaimSource` ranks.
  Convergence does not depend on that gap, but auto-approval does.
