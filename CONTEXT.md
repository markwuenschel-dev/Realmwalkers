# Realmwalkers

Realmwalkers is a contract-first novel-writing system where author-approved story contracts, scene prose, editorial review, and production assembly stay explicit and human-gated.

## Language

**Production Run**:
An editorial pass over one chapter that assembles scene prose into a final chapter candidate, records QA findings, and tracks repair work until the author approves or stops it.
_Avoid_: pipeline, batch, release run

**Production Run Lifecycle Owner**:
`dominion.workers.production` — owns the ProductionRun lifecycle: it implements create, inspect, finalize, approve, cancel and resume itself, and is the seam `api.routers.production` uses to reach the sequence and repair lanes — triage, repair and verify are delegations into `production_repair`, not bodies here. That router imports neither `production_sequence` nor `production_repair`. It re-exports the lane operations its callers reach through it — as delegating functions, and as plain re-bindings for `production_repair`'s `human_verify_issue` and its exception types, which that router catches without importing the lane (#285). It re-exports no lane operation that no caller reaches through it, and no private (underscore) names. `production_delete`, `production_fidelity` and `production_support` are imported directly by that router and are not re-exported here. Tests import the lanes by name; sibling worker modules do both — some import a lane directly, others reach a lane operation through this module, which is what keeps those re-exports live.
_Avoid_: production run facade, lane router, production service, workflow API

**SceneFidelity Report**:
An immutable evaluation artifact that records how one exact Draft Attempt preserved or lost the declared ScenePacket fidelity requirements.
_Avoid_: fidelity critique, QA verdict

**Fidelity Critique**:
An immutable, strict-payload `Critique` projection of one actionable SceneFidelity Report finding, presented to the author and later Production Run triage.
_Avoid_: fidelity report, mutable issue, draft blocker

**Fidelity Post-Draft Policy**:
The declared consequence of post-draft SceneFidelity findings: `advisory` remains informational, while `export_required` may create production repair work and hold final chapter export. Every active Fidelity Requirement is structurally valid before drafting regardless of this policy.
_Avoid_: draft gate, QA verdict

**Fidelity Suggestion**:
A proposed SceneFidelity requirement that may be shown for author consideration but is not part of the active ScenePacket contract.
_Avoid_: soft requirement, automatic rule

**Fidelity Requirement**:
An active, author-approved typed mode-and-policy bundle in a ScenePacket, identified by a stable server-minted `requirement_id` and valid before packet approval.
_Avoid_: fidelity mode, requirement list item

**Fidelity Mode**:
One member of SceneFidelity's closed typed registry that owns a distinct class of scene contract and evaluation: relationship turn, intimacy blocking, combat blocking, spatial affordance, or reader movie.
_Avoid_: generic reviewer, quality mode

**Fidelity Clause**:
One atomic preservation claim inside a Fidelity Requirement, identified by a stable server-minted `clause_id`, declared with `standard` or `hard` enforcement, and used by evaluation evidence, repair work, and overrides.
_Avoid_: requirement, list position

**Clause Evaluation**:
The merged current result for one active hard Fidelity Clause: satisfied, lost, indeterminate, blocked by dependency, not evaluated, or adapter failed. A satisfied result cites positive prose evidence.
_Avoid_: absence of findings, generic QA verdict

**Satisfaction Criterion**:
The typed observable-evidence definition required by one hard Fidelity Clause and used to support a satisfied Clause Evaluation with one or more prose anchors.
_Avoid_: free-form quality goal, implicit expectation

**Fidelity Identity Decision**:
The author-directed choice to refine an existing Fidelity Requirement or Clause without changing its semantic identity, or replace it with a new server-minted identity and consciously rewired dependencies.
_Avoid_: inferred identity preservation, silent rewiring

**Fidelity Contract Version**:
The explicit version marker required on any ScenePacket that activates SceneFidelity requirements, allowing legacy packets to remain inert and future contract migrations to be unambiguous.
_Avoid_: inferred legacy schema, historical audit switch

**Fidelity Evidence Anchor**:
The exact prose span that grounds a SceneFidelity finding; for an omission it is the nearest expected-beat span or the relevant scene transition together with the full-prose hash.
_Avoid_: uncited absence, general impression

**Fidelity Dependency**:
An acyclic clause-to-clause prerequisite reference that supplies evaluation and drafter context without copying ownership or automatically failing the dependent clause.
_Avoid_: inherited failure, duplicated clause

**Fidelity Override**:
An author-only, written decision to accept one open Fidelity Critique for one evaluated artifact without deleting the evidence or changing the underlying requirement.
_Avoid_: dismissal, permanent exemption

**Fidelity Repair Preview**:
An immutable Artifact containing a bounded proposed prose patch for one Fidelity Critique; it does not become a Scene revision until the author accepts or edits it.
_Avoid_: draft attempt, automatic rewrite

**Current Fidelity Report**:
A SceneFidelity Report whose evaluated artifact, prose hash, ScenePacket identifier, and packet-contract fingerprint match the current author-visible scene draft and its active contract.
_Avoid_: latest report, recent report

**Fidelity Operational Hold**:
A Production Run hold caused by missing, stale, or failed required evaluation rather than a validated prose loss.
_Avoid_: repair issue, QA failure

**Fidelity Issue Lifecycle**:
The production ownership progression of a Fidelity Critique: an Issue is verified by fresh passing evidence, overridden by the author for one artifact, or superseded only by a newer current Issue for the same unresolved loss.
_Avoid_: clearing by staleness, dismissing evidence

**SceneFidelity Evaluator**:
The single public facade that validates a fidelity contract, coordinates bounded mode-specific evaluation, records one report, and passes validated findings to policy.
_Avoid_: fidelity agent swarm, parallel reviewer framework

**Fidelity Fixture**:
A versioned end-to-end example that declares a fidelity contract, exact prose, expected evidence, and expected policy behavior for regression and promotion testing.
_Avoid_: prompt sample, anecdotal test

**Run**:
A planning-request provenance envelope — one generation request's scope, gate mode, and token budget. It is provenance and telemetry grouping only, NOT the routing or book-scoping key for the jobs it spawned (a job carries its own `book_id`). Distinct from a Production Run.
_Avoid_: pipeline, batch, the job's owner, Production Run

**Job Book Ownership**:
The invariant that every Job belongs to exactly one book via its own authoritative, non-null `book_id`, independent of whether it has a Run. Book-scoped job queries key solely on `book_id` (ADR 0027).
_Avoid_: run-owned job, run_id routing, dual-key scope

**Integrity Hold**:
An ownerless or ownership-conflicted Job withheld from execution and from the normal failure controls (retry/clear): the quarantined live jobs plus any unresolved NULL-book terminal/conflict rows. Retained as evidence and surfaced to the operator; blocks the book_id NOT NULL promotion until resolved.
_Avoid_: failed job, dismissable error, transient failure

> **Implementation status — import adoption & revision workflow (ADR-0028, updated 2026-07-27).**
> All four terms below are LIVE on main. **Revision Request** (Slice 2) and **Import Scene Evidence**
> (Slice 3a′) landed first; **Import Adoption** and **Chapter Workflow Lock** landed with Slice 3b (PR #256).
> `workers/import_adoption.py` is the leased adoption worker.
>
> **ADR-0032 completes the lifecycle tail — the rollout is fully landed on main** (W0–W2 via #262,
> #264, #265; W3–W4 via #271). Treat every term below as an enforced invariant, not pending work.
> Adoption entry has exactly ONE writer, `shared/adoption_entry.ensure_import_adoption[_locked]`
> (entry only; the leased worker still owns its own claim/requeue lifecycle), with four callers: operator **Start** and
> **Re-author** (`routers/adoption.py`), the sync **Revise** command (`reviews.accept_revision_intent`),
> and **boot reconciliation** (`workers/boot_reconciliation.py`). "≤1 active adoption per chapter" is a
> partial-unique DB index, not merely a lock convention. `liveness_basis` decides survival when no request
> remains: `request_bound` rows are reverse-cancelled by `reconcile_adoption_demand_locked`,
> `operator_independent` rows never are.
>
> Chapter-lock coverage, verified at HEAD — `run_under_chapter_workflow` wraps: the adoption worker's
> publish, operator Start/Re-author (via the seam), scene APPROVE and the Revise command
> (`routers/reviews.py`), request-resuming ScenePacket approval + chapter-derive (`routers/scene_packets.py`),
> boot reconciliation, and — closing #259's residual 1 — the chapter-tier ChapterPacket transitions
> `update`/`approve`/`delete` (`routers/packets.py`). The propose/replace write is covered too, but by
> the lock PRIMITIVE inside `workers/packet._persist` — the single ChapterPacket insert/replace writer —
> because the 1–2 min author+QA calls above it must NOT hold the lock; `_persist` acquires it for the
> short delete+insert and the caller's commit releases it. A fitness guard
> (`tests/test_issue259_chapter_packet_writer_guard.py`) enumerates ChapterPacket writers by AST and
> fails an unlocked one, replacing an allowlist that could only re-check functions someone remembered
> to add. It is stronger than that allowlist but NOT total — it tracks which locals hold a ChapterPacket
> and so cannot see one reached via a cross-module helper, a subscript/attribute receiver, `setattr`,
> or `session.merge`; the file enumerates its own blind spots and carries a regression test proving it
> catches the pre-fix shape.
>
> **Remaining limits (honest boundary, not "every chapter mutation is serialized"):** the drafting
> drain claims jobs with `FOR UPDATE SKIP LOCKED` and does not take this lock, so a draft already
> running is not serialized against `delete_packet`'s job purge — and because `SET LOCAL lock_timeout`
> applies for the whole transaction, that purge can now time out on the draft's row lock; it is mapped
> to the same retryable 409, never a 500. `propose_packet`'s single-flight is still the process-local
> `background_work._inflight` set, so two API replicas can each start a propose for one chapter — they
> now serialize at `_persist` rather than racing the write, but the duplicate authoring spend still
> happens. A background propose that stays locked out past its bounded retries has no durable
> operator-facing signal (`progress` is cleared when the task ends); it logs
> `packet.propose_chapter_busy` and the operator re-triggers. #259's residual 2 (D13 precedence
> authority) is a design ruling, not lock coverage — see the note under **Claim Source Precedence**.

**Import Adoption** _(live — Slice 3b; entry unified by ADR-0032)_:
Durable, leased, checkpointed work that turns one whole chapter's imported prose into a reviewed ChapterPacket, on demand. It owns adoption progress only (its lifecycle ends at `contract_proposed`), never mirroring ChapterPacket approval, ScenePacket approval, Job execution, or revision completion (ADR 0028).
_Avoid_: adopt job, import bypass, packetless draft

**Amendment** _(live — #261 / ADR-0034)_:
A new ChapterPacket row, copy-on-write from the chapter's approved packet, admitted only when imported prose exists and some imported scene has **no seed** in `body["scene_seeds"]`. On human `approve-amendment` it takes the single authority slot in the same transaction its predecessor becomes `superseded`.
_Avoid_: re-author, edit approved contract, re-derive

**Chapter Packet Authority** _(live — #261)_:
The unique `status=approved` ChapterPacket for a chapter. `GET /chapters/{id}/packet` is newest-by-`created_at` (may be a proposed amendment). `GET /chapters/{id}/packet/authority` is this row.
_Avoid_: latest packet, current packet

**Import Scene Evidence** _(live — Slice 3a′)_:
An immutable, span-anchored LLM fact ledger extracted from one imported scene snapshot, keyed by `(scene_id, scene_version, prose_hash, extractor_schema_version)` and reusable across adoptions. It is evidence the ChapterPacket Author reads as `M#` sources; raw prose stays auditable but never enters the author prompt.
_Avoid_: scene summary, raw prose chunk, canon fact

**Revision Request** _(live — Slice 2)_:
The durable record of an author's edit intent — immutable target `(scene_id, version)`, feedback, target pass, and origin — with a coarse lifecycle that outlives contract preparation until a revision Job is minted. At most one is active per target scene; its display phase is server-derived from the request, adoption, packets, and Job (ADR 0028).
_Avoid_: revise approval, redraft toast, queued job

**Chapter Workflow Lock** _(planned — Slice 3b)_:
A per-chapter transaction-level advisory lock (`acquire_chapter_workflow_lock`) that serializes every authority-changing operation on a chapter — source-prose mutation, fingerprint-validate-and-mint, adoption compare-and-set publish, ChapterPacket propose/replace/approve/supersede, and request-resuming ScenePacket approval. It coordinates the cross-table invariant; it does not replace queue-claim row locks (ADR 0028).
_Avoid_: chapter row lock, global mutex, queue claim lock

**Claim Source Precedence**:
The enforced total order `LOCKED_CANON > DERIVED_FROM_MANUSCRIPT > DERIVED_FROM_OUTLINE > PLAUSIBLE_INFERENCE > UNRESOLVED` (with `FORBIDDEN` a separate surface prohibition, not a rank) that decides how conflicting packet claims resolve. A conflict the order cannot break becomes an approval-blocking open question; manuscript evidence never enters canon retrieval or overrides locked canon automatically (ADR 0029).
_Avoid_: claim label, source hint, canon promotion

> **#259 residual 2 / ADR-0031 D13 — status, verified 2026-07-27.** D13 ruled on `shared/claim_precedence.py`
> when it had *zero demonstrated importers*, defaulting to "deletion or replacement". That premise is now
> false and the default no longer applies: the module has a concrete caller and a unique responsibility
> (`workers/packet/evidence.py:15-18` gates conflict-candidate extraction on it), and ADR-0029's
> approval-block is wired end to end — `workers/packet/__init__.py:758-763` produces the conflict open
> questions, and `workers/packet/approval_policy.py:98,111` refuses approval while any remain. So the
> *gating* half of "precedence authority" is decided and enforced. What is genuinely still open is only
> whether that blocker folds into #229's shared authorization boundary or stands alone — a #229 decision,
> not a lock-coverage one. **No ruling has been recorded**; this note states where the question actually
> sits, it does not close it.

**Scene Packet Approval Source** _(live — ADR-0033 D5b)_:
How a ScenePacket came to be APPROVED: `manual_command` (a deliberate command through an explicit route — never an authenticated identity, the system has none), `autonomous_policy`, or `legacy_unclassified` (approved before the record existed). NULL until approval. Written only by the single approval seam, and no caller may omit it. `status = approved` cannot answer "did a human read this contract?"; this can, and the reviewer trust split depends on it. It is the scene-tier instance of the still-planned Execution Authorization, narrowed to one question with one writer.
_Avoid_: approved_by, decision source, execution authorization, human approval flag

**Repair Cycle Budget** _(live — ADR-0031 D7 / ADR-0033 D4)_:
The persisted count of AUTOMATIC apply attempts one repair cycle has spent, capped at three, with a terminal reason (`attempt_cap_reached` | `hard_failure`) recorded when it parks. Counted per cycle, not per attempt row — a chapter-scoped repair mints one attempt row per member scene. Reserved through the one seam both the unattended drain and the sweeper pass through; a manual apply neither spends it nor is blocked by it, and a manual apply on a parked cycle reopens it. It survives restart, which the process-local counter it replaced did not.
_Avoid_: retry count, attempt_no, sweeper max attempts, backoff

**Review-Ready** _(ADR-0033; replaces the retired `Editorial Convergence`)_:
The state a scene reaches when the machine is finished with it and it is waiting on the author. Reached one of two ways: the unattended produce → review → repair loop leaves **no open Issue above `info` severity** (`warn`, `repair` and `block` all keep the loop running; open means `proposed | accepted | repair_queued | repaired | escalated`), or the repair cycle **parks at the attempt cap**. It is a stopping condition built on Issue resolution, deliberately NOT a Fidelity score (scoring is a deferred side project), and distinct from approval: a scene can be Review-Ready and unapprovable (an Approval Blocker holds its contract), or approved and not Review-Ready.
_Avoid_: editorial convergence, quality score, fidelity verdict, human sign-off, done, "no findings"

> **Reviewer inputs under an auto-derived contract (ADR-0033 D5, LIVE).** A reviewer always honours a contract's *positive* fields (`scene_job`, `scene_type`, `required_beats`, `word_budget`) but honours its *suppression* fields (`forbidden_beats`, `reviewer_false_positive_traps`, `reviewer_instructions`) only when a **human** approved that ScenePacket — so a drifted contract cannot silence the reviewer that would expose the drift. Enforced in the contract *projection* (`context/reviewer_trust.py`), not in the reviewers, so a lane cannot forget it. Its prerequisite is now live: **Scene Packet Approval Source** below.

> **Implementation status — autonomy & authorization (ADR-0030 / ADR-0031, verified 2026-07-25).** Of the
> six terms that follow, **two are now LIVE** and four remain planned vocabulary.
>
> **LIVE — treat as enforced invariants.** *Approval Blocker* (A1c slices 1–2): the record, its active
> partial-unique index, `workers/scene_packet/blockers.py`, its routes, the fail-closed projection, and
> **two** producers — the manual route and an automatic `canon_conflict` source raised from the derive
> path. *Authorization Requirement* (A1c, ADR-0031 D16): a durable `repair_tasks.authorization_requirement`
> column (`ceiling_gated` | `manual_grant`), decided in exactly one place
> (`shared/authorization.py:authorize_repair`), read by the apply seam, the unattended drain and the
> sweeper. `requires_human_approval` is no longer a mutable field — it is a derived read-only projection
> and its physical column is dropped.
>
> **STILL PLANNED — vocabulary, not invariants.** *Autonomy Control*, *Autonomy Epoch*, *Execution
> Authorization*, and *Operational Hold*. In live code the machine-policy boundary is still a single KV
> flag `autonomy_enabled` read per sweeper tick (`workers/sweeper.py`); there is no autonomy singleton, no
> epoch, and no Operational Hold state. In particular **Execution Authorization is not built**: A1c makes
> the authorization *decision* explicit and single-sited, but the *proof* of how a given execution was
> authorized is still inferred from `human_approved_at` plus a run event — there is no append-only grant
> record. (`Job Book Ownership` above — ADR-0027 — **is** wired and is not affected by this note.)

**Autonomy Control** _(planned)_:
The singleton record owning the machine-policy safety boundary: `enabled`, `epoch`, `updated_at`. Read plainly at autonomous mint and at claim; locked and re-checked immediately before finalization, never across generation. It is orthogonal to the queue pause, which stops asynchronous claims of every authorization. Neither implies the other: unpausing the queue is not consent to autonomous spend, and disabling autonomy does not drain the queue (ADR 0030).
_Avoid_: kill switch, model override row, settings KV, queue pause

**Autonomy Epoch** _(planned)_:
The monotonic counter on Autonomy Control, incremented only when `enabled` changes. Work authorized by autonomous policy is stamped with the epoch it was minted under and must still match at claim and at finalization; a mismatch places the work on an Operational Hold instead of publishing it. An epoch is strictly stronger than a timestamp: it survives clock skew and cannot be satisfied by a call that merely started earlier.
_Avoid_: pause timestamp, generation counter, autonomy version, disabled_at

**Execution Authorization** _(planned)_:
The durable, immutable grant that authorizes one unit of background work to execute — `manual_command`, `autonomous_policy`, or `legacy_unclassified` — recorded as a grant event rather than a mutable field, so releasing held work *adds* a grant instead of overwriting the proof of how it was authorized. `manual_command` asserts a deliberate command through an explicit route, NOT an authenticated human identity (the system has none). Distinct from Authorization Requirement (what the work demands), from `authority_level` (blast radius), and from a Revision Request's `origin` (what created the intent).
_Avoid_: human command, human approval flag, origin, authority, actor

**Authorization Requirement** _(live — ADR-0031 D16 / A1c)_:
What a unit of repair work demands before it may execute: **ceiling-gated**, or an explicit **manual grant**. Orthogonal to `authority_level`, which states blast radius only — the two were conflated while `human_required` was a rung on the blast-radius ladder that a raised ceiling could silently negate. Manual-grant work needs a human grant at any ceiling, and no automated caller can supply one; an automated caller authorizes ceiling-gated work only by *declaring* a ceiling that covers its blast radius. Being autonomous is not itself a grant.
_Avoid_: authority level, human required, ceiling, blast radius, requires_human_approval

**Decision Source**:
What produced an `Approval` row: `human_review`, `repair_system`, or `legacy_unclassified`. `Approval` is defined as the human's verdict and doubles as a training/export label, so quality metrics and any learning corpus read human decisions only. The repair system writes `Approval(REVISE)` rows as an operational instruction carrier; those are system decisions and must never be counted as editorial verdicts.
_Avoid_: approval reason, actor, execution authorization, approved_by

**Operational Hold** _(planned)_:
A nonterminal state for work that is retained but not executable, carrying a `hold_reason` (`autonomy_disabled`, `epoch_mismatch`, `legacy_authorization_unproven`). Held output is preserved, never published — no timeline advance, no supersede, no reviewable status. Release requires a fresh Execution Authorization under the current Autonomy Epoch and a re-evaluated predicate; re-enabling autonomy never auto-releases held work. Distinct from `quarantined`, which is integrity-terminal and ownerless.
_Avoid_: quarantined, autonomy held, paused, failed, blocked

**Approval Blocker** _(live — ADR-0031 D14 / A1c)_:
A durable, scene-scoped record that an unresolved scene-level open question is holding approval of a ScenePacket, carrying an explicit lifecycle and resolution state. The shared domain approval operation approves a scene or derives its beats only when that scene has no active Approval Blocker; approval is never a resolution — closing one requires an explicit rationale and source. Raised from two sources: a `manual_command` route, and `canon_conflict` — raised automatically by the derive path when scene QA reports a canon-conflict finding, which is what makes the hold reachable without a human having already spotted the problem. Blocker state is never embedded in ScenePacket `body` and never live-read from `ChapterPacket.open_questions` (chapter-tier questions are not scene-scoped).
_Avoid_: open_questions column, packet-body flag, derived chapter question, chapter open question
