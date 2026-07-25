# ADR-0033: Convergence — the stopping condition for unattended work

**Status:** accepted · **implementation_authorized:** true · **Decision owner:** mark
**Charts:** issue #220 (`wayfinder:grilling`, "Convergence, concretely"), under map #213.
**Refines and renames** ADR-0030's `Editorial Convergence`, which is retired in favour of `Review-Ready` (D6).

**Revision history**
- **v5 (2026-07-25) — the two carry-forward gaps are BUILT, not just recorded.** D4's cap is enforced
  (T2/#230: `shared/repair_budget.py`, a persisted per-cycle counter + terminal reason, reserved through
  the one seam both the drain and the sweeper pass through). D5b's prerequisite exists
  (`ScenePacket.approval_source`, written by the single approval seam), and D5 itself is live — the
  suppression fields are stripped in the contract PROJECTION, so no reviewer has to remember the rule.
  Both sections below are updated from "must be built" to "built"; no decision is reopened.
- **v4 (2026-07-25) — F1 CLOSED: the term is `Review-Ready`.** `Editorial Convergence` is retired. All
  three blocking items are now ruled, so **`implementation_authorized` flips to true** and the status
  moves to accepted. A driver has a mandate: a pinned stopping condition (D2), a pinned open-set (D2a),
  the D7 cap (D4), a settled reviewer contract (D5) with its prerequisite named (D5b), and a blessed name.
- **v3 (2026-07-25) — F3 CLOSED by author ruling: split the reviewer contract by trust (option c).** D5
  rewritten as a decision; D5a fixes the field-by-field split (`reviewer_instructions` treated as
  suppression, recorded as a derivation from the ruling rather than a separate choice); **D5b records a
  prerequisite that did not surface during the fork** — scene-packet approval provenance does not exist,
  so (c) is a schema-and-seam change, not a reviewer flag; D5c records how it composes with the A1c
  canon-conflict blocker. **F1 (the name) is the only item left**, so `implementation_authorized` stays
  false.
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
> below were recorded rather than defaulted. **All three are now closed by author ruling** — F2 (D2,
> `S = info`), F3 (D5, split the reviewer contract by trust), F1 (D6, `Review-Ready`). This record no
> longer blocks anything; `implementation_authorized` is **true**.

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

### D4 — The cap — [SETTLED · verbatim from ADR-0031 D7 · **ENFORCED 2026-07-25**]

> **One persisted maximum of three repair attempts per repair cycle.** At the third failed attempt: do not
> enqueue a fourth automatic repair; park deterministically; persist the terminal reason; expose it to
> operators; remain idempotent across restart; require **explicit human action** to reopen a cycle. **No
> multi-tier automatic ladder in this milestone.** Any escalation beyond parking for human review is out
> of scope.

~~Enforcement is **T2 / #230** and is a non-goal here.~~ **Enforcement landed 2026-07-25.**
`shared/repair_budget.py` holds the policy; `RepairTask.repair_cycle_attempts` holds the persisted
budget and `RepairTask.terminal_reason` the terminal state, so fact 10 is now stale in both halves. The
reservation happens inside `apply_repair_task`, which is the single seam **both** the unattended drain and
the sweeper pass through — a cap either worker could bypass is not a cap, and that was precisely the
drain's bug. A manual apply neither consumes the budget nor is blocked by it; a manual apply on a *parked*
cycle reopens it. #230's seven acceptance tests are `tests/test_repair_attempt_cap.py`.

This ADR still fixes the definitional half: an unconverged cycle that hits the cap is *parked*, never
*converged*, and the two terminal states are distinguishable in the record
(`attempt_cap_reached` vs `hard_failure`).

### D5 — Reviewer set against an auto-derived contract — [**SETTLED** · author ruling 2026-07-25 · closes F3]

*Which* reviewers run was never open: fact 6 answers it. `reviewers_for(tags)` is deterministic, already
implemented, and needs no autonomy-specific variant — continuity, voice, pacing and state-drift always;
combat, sensory, dialogue when the beat carries the tag. **Flag → Issue needs no new design** either
(fact 7): live, single-sited, signature-deduped.

The fork was what an **auto-derived** contract does to their *inputs*, and it is ruled:

> **Split the reviewer contract by trust (option c).** A reviewer always honours the contract's
> **positive** fields. It honours the contract's **suppression** fields only when the owning ScenePacket
> was approved by a human. An auto-approved contract cannot tell a reviewer what not to look at.

Rejected: **(a) trust it** — a drifted contract's `reviewer_false_positive_traps` silently suppresses the
very findings that would expose the drift; the detector is silenced by the thing it was meant to detect.
**(b) contract-blind under autonomy** — safe, but it discards precision everywhere instead of only where
trust is missing, and under `S = info` (D2) extra false positives are paid for in attempts, which are the
scarce resource.

#### D5a — The split, field by field

`reviewers/lane.py:34-59` reads exactly seven keys off `ctx.reviewer_contract`:

| Key | Class | Honoured when |
|---|---|---|
| `scene_job`, `scene_type`, `required_beats`, `word_budget` | **positive** — tell a reviewer what to look FOR | always |
| `forbidden_beats` — *"do not ask for these"* (`lane.py:47`) | **suppression** | human-approved contract only |
| `reviewer_false_positive_traps` — *"do not flag"* (`lane.py:53`) | **suppression** | human-approved contract only |
| `reviewer_instructions[lane]` — free text per lane (`lane.py:50`) | **unclassifiable → treated as suppression** | human-approved contract only |

`reviewer_instructions` is the one judgement call, and it is a **derivation from the ruling, not a
separate decision**: the field is free text, so its content *can* suppress ("don't flag the stamina
tracking"), and the ruling's principle is that a derivation must not be able to silence its own detector.
Anything that can suppress and cannot be verified is withheld. Recorded explicitly because a reasonable
reader could put it in the positive column; if the author wants it trusted, that is a one-row change here.

#### D5b — Prerequisite: scene-packet approval provenance — [**BUILT 2026-07-25**]

**(c) could not be built as a branch in the reviewer.** It needed a fact the system did not record. As
verified when this ADR was written (all three now addressed — see the closing note):

- `ScenePacket` has **no approval-provenance column**. Its full column list is `id, book_id, chapter_id,
  chapter_packet_id, scene_seed_id, scene_no, status, qa_verdict, qa_warnings, body, sources, source_hash,
  stale_reason, source_scene_id, created_at, updated_at` (`shared/models.py`). `status = approved` is the
  same value whether a human or a policy set it.
- The single approval seam `_apply_approval_locked` (`scene_packet/__init__.py:125-140`) writes exactly
  `status = APPROVED` and `stale_reason = None`. It records nothing about **how** approval happened.
- `Decision Source` (`CONTEXT.md`) answers this question for `Approval` rows on **Scenes**, not for
  ScenePackets. ADR-0031 D9's *Execution Authorization* grant record — the general form of it — is still
  unbuilt (`CONTEXT.md` autonomy status block).

**Built 2026-07-25.** `ScenePacket.approval_source` (`enums.ScenePacketApprovalSource`:
`manual_command | autonomous_policy | legacy_unclassified`, NULL until approved) is written by the single
approval seam, and `source` has **no default** on either the seam or the facade — the same discipline
`apply_repair_task(autonomous=...)` uses, so a future autonomous approver cannot inherit human provenance
by omission. Already-approved rows backfill to `legacy_unclassified`, which the split treats as untrusted:
unproven provenance is not human provenance. **The A1c work made this cheap rather than ambiguous** —
exactly one `ScenePacketStatus.APPROVED` writer repo-wide, so the provenance write had exactly one home;
before A1c it would have had three.

**The split is enforced in the PROJECTION, not in the reviewers** (`context/reviewer_trust.py`, applied at
`context/contracts.py`). The reviewers are seven independent modules and an eighth will be added; a rule
each must remember is a rule one will forget, and the failure would be silent — a reviewer that wrongly
trusts a trap simply reports nothing. Filtering where the contract is built means an untrusted suppression
field never reaches a reviewer's prompt, so `reviewers/lane.py` is unchanged by design. Tests:
`tests/test_reviewer_trust.py`.

#### D5c — This closes a loop with the Approval Blocker

The two A1c mechanisms compose, and the composition is the intended operator story:

```
auto-derived contract  ──▶  QA reports a canon conflict  ──▶  canon_conflict ApprovalBlocker
                                                                      │
                                    human rules on it + approves ◀────┘
                                                                      │
                        contract is now human-approved ──▶ suppression fields become trusted
```

Until a human rules, the contract cannot suppress a reviewer *and* cannot be approved. After they rule,
it can do both. Provenance is therefore not a new gate the author has to service — it is a consequence of
the ruling they already had to make.

### D6 — Name — [**SETTLED** · author ruling 2026-07-25 · closes F1]

> **The term is `Review-Ready`.** It replaces `Editorial Convergence`, which is retired. A scene is
> Review-Ready when it has no open Issue above `info` (D2) or when its repair cycle has parked at the
> attempt cap (D4) — either way, the machine is finished and the scene is waiting on the author.

The name says what the state is *for* rather than what produced it, which is the correct emphasis:
ADR-0030's whole point is "the machine finishes the work; the author finishes the decision". It also
avoids the two things the retired name got wrong — no editor is involved, and "convergence" oversold the
cap-exhausted case as if quality had been reached.

`Review-Ready` is the blessed glossary term in `CONTEXT.md`. "Convergence" survives only as the informal
name of the *loop* that produces the state (the converge loop), never as the name of the state itself.

## Non-goals

T2/#230 cap **enforcement**; T3/#231 scheduler contract; T4 revision lifecycle; T5 adoption; T6 locking;
T7 migration; Layer 2 adjudication; autonomous replacement drafts (ADR-0031 D17); fidelity scoring;
the driver itself (#228). This ADR defines the stopping condition and nothing else.

## Consequences

- **A driver is now authorized by this record** (`implementation_authorized: true`). What it may rely on:
  the stopping condition (D2) and its open-set (D2a), the three-gate separation (D3), the D7 cap as a
  definition (D4 — enforcement is still T2/#230), and the reviewer-contract trust split (D5).
- ~~**D5 has a build prerequisite (D5b): scene-packet approval provenance.**~~ **Built 2026-07-25** —
  `ScenePacket.approval_source`, written by the single approval seam, with the trust split enforced in the
  contract projection. It is the scene-tier instance of ADR-0031 D9's still-unbuilt Execution
  Authorization, narrowed to one question with exactly one writer; the general grant record remains open.
- `S = info` is a **narrowing** of today's verification behaviour (fact 2 — today any single `info`
  critique keeps the loop alive), so the first implementation must ship with the before/after visible in
  a test at each band, not only at `info`.
- **`S = info` makes D4's attempt cap load-bearing.** A scene whose reviewers keep producing fresh `warn`
  findings is ended by the cap, not by the threshold — so the persisted terminal reason is the primary
  signal for "this scene never settled", not an operator nicety. ~~T2 should be sequenced before, not
  after, any driver that runs unattended.~~ **T2 landed 2026-07-25**, ahead of any driver, as required.
- The three-gate separation in D3 constrains T2: the attempt cap parks a *cycle*; it must not resolve an
  ApprovalBlocker, and resolving a blocker must not reset an attempt budget.
- ADR-0030's "Layer 1 (objective floor)" is now half-live and this ADR does not complete it: the scene
  tier escalates a *reported* canon conflict (fact 11) but still does not consult `ClaimSource` ranks.
  Convergence does not depend on that gap, but auto-approval does.
