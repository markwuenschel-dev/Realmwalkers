# ADR-0033: Convergence — the stopping condition for unattended work

**Status:** proposed · **implementation_authorized:** false · **Decision owner:** mark
**Charts:** issue #220 (`wayfinder:grilling`, "Convergence, concretely"), under map #213.
**Refines** ADR-0030's `Editorial Convergence` and the `CONTEXT.md` glossary entry of that name.

**Revision history**
- v1 (2026-07-25) — first draft, authored during the ADR-0031 A1c build. Records what is **settled** and
  isolates **three blocking items** (F1 name, F2 threshold, F3 reviewer set) that are the author's to rule
  on. `implementation_authorized` stays **false** until F1–F3 close: a driver with an unpinned stopping
  condition has no mandate, and #220's whole question is precisely those three.

> **Why this is not yet authorized.** ADR-0031 D10 makes "no decision" a blocking state, so the open items
> below are recorded rather than defaulted. Two of the three are not mine to settle at all: F1 is a naming
> decision (author-only), and F2 changes when a human is interrupted. F3 has a verified factual answer for
> *which* reviewers run, but a genuine design fork on *what to do about them* — see D5.

## Decision (the settled core)

**Convergence is a stopping condition on open Issues, evaluated per repair cycle, bounded by a persisted
attempt cap.** It is deliberately **not** a score, **not** a QA verdict, and **not** human approval. It
says only: *the machine has nothing further it knows how to fix here.* The human still decides whether the
result is good — ADR-0030's "the machine finishes the work; the author finishes the decision".

Three things it is measured against, kept strictly apart because they gate different objects:

| Gate | Object it bounds | Effect when it trips | Owner |
|---|---|---|---|
| **Threshold S** | the prose, via open Issues | the converge loop keeps running | this ADR (F2) |
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

### D2 — Threshold S — [OPEN · **F2**, blocking]

S must be pinned to a member of `shared.enums.Severity` (`info | warn | repair | block`) — prose like
"a configured advisory severity" is what #220 exists to remove. Candidates, with grounds:

- **S = `info`** — converged ⟺ no open Issue above `info`. `warn` and up keep the loop running; `info`
  noise does not. **This is the value that makes triage and verification agree**: triage already rejects
  exactly `info` as non-actionable (fact 4), so pinning S here closes the live asymmetry in which a
  finding triage refuses to act on can still re-queue the task forever. Keeps all seven reviewers in the
  loop. **Cost:** reviewers are advisory by design (DESIGN §9); letting `warn` drive unattended rewriting
  promotes advice into instruction, and the attempt cap becomes the main thing that ends a noisy scene.
- **S = `warn`** — converged ⟺ no open Issue above `warn` ⟺ nothing that blocks final export
  (`EXPORT_BLOCKING = {block, repair}`, `shared/severity.py:26`). One vocabulary, no new constant, and
  "converged" would mean exactly "exportable". **Cost, from fact 8: six of the seven reviewers stop
  mattering** — voice, pacing, state-drift, combat, sensory and dialogue cannot exceed `warn`, so a scene
  with twenty open warnings from them converges on the first pass. Only continuity's hard-number `block`,
  `scene_fidelity`, `scene_scope` and `chapter_assembly` would decide convergence.
- **S = `repair`** — converged ⟺ only `block` keeps the loop running. Strictly weaker than `warn`;
  recorded for completeness, and it makes `repair`-severity `scene_fidelity` findings non-blocking,
  which contradicts ADR-0020's fidelity-issue lifecycle. Not recommended.

**Also unresolved inside F2:** *which Issue statuses count as OPEN.* Fact 9 — no shared predicate, and
three local definitions that disagree. A threshold cannot be evaluated without that set, and shipping S
against any one of the three would silently adopt its disagreement. Candidate: one shared frozenset,
open = everything except `REJECTED | VERIFIED | FALSE_POSITIVE | OVERRIDDEN | SUPERSEDED | MERGED`, with
the three call sites migrated onto it (the repair-task partition at `production_repair.py:95-109` is the
model, including its test).

**My lean:** `S = info`, because it is the only candidate that makes the two gates the system already has
agree with each other, and because fact 8 makes `warn` a much larger change than it reads as — `warn`
does not "raise the bar" so much as remove six of the seven reviewers from the stopping condition.
**Counter-argument to weigh:** `warn` is the only candidate where "converged" and "exportable" are the
same sentence, and a loop that reruns on advisory prose opinions is exactly the "free-running quality
loop" #222 records the author as not wanting. If `warn` wins, the honest consequence is that the advisory
reviewers are a **human-facing** signal only, and that should be said out loud rather than discovered.

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
What F3 must also settle is the **severity consequence** — because six of the seven reviewers cannot
exceed `warn` (fact 5), F3's answer mostly only matters if F2 chooses `S = info`. Under `S = warn` those
six have no effect on convergence at all and this fork shrinks to continuity alone. **F2 and F3 are
therefore coupled, and F2 goes first.**

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

- Until F1–F3 close, **no driver may be authorized**: `implementation_authorized: false` here is the
  reason, and it is deliberate rather than an oversight.
- Whatever S is chosen, it is a **narrowing** of today's behaviour (fact 2 — today any single `info`
  critique keeps the loop alive), so the first implementation must ship with the before/after visible in
  a test at each band, not only at the chosen one.
- The three-gate separation in D3 constrains T2: the attempt cap parks a *cycle*; it must not resolve an
  ApprovalBlocker, and resolving a blocker must not reset an attempt budget.
- ADR-0030's "Layer 1 (objective floor)" is now half-live and this ADR does not complete it: the scene
  tier escalates a *reported* canon conflict (fact 11) but still does not consult `ClaimSource` ranks.
  Convergence does not depend on that gap, but auto-approval does.
