# Realmwalkers

Realmwalkers is a contract-first novel-writing system where author-approved story contracts, scene prose, editorial review, and production assembly stay explicit and human-gated.

## Language

**Production Run**:
An editorial pass over one chapter that assembles scene prose into a final chapter candidate, records QA findings, and tracks repair work until the author approves or stops it.
_Avoid_: pipeline, batch, release run

**Production Run Facade**:
The single public production-run module interface used by routers, background workers, and tests to create, inspect, triage, repair, assemble, and approve a Production Run.
_Avoid_: lane router, production service, workflow API

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

> **Implementation status — import adoption & revision workflow (ADR-0028, verified 2026-07-17).** The four
> terms that follow — Import Adoption, Import Scene Evidence, Revision Request, and Chapter Workflow Lock —
> describe the **target design** of ADR-0028 and are **not yet implemented**. `ImportAdoption`,
> `ImportSceneEvidence`, and `RevisionRequest` (`shared/models.py:381`, `:420`, `:447`) have zero
> constructors anywhere in the codebase, and `chapter_lock.acquire_chapter_workflow_lock`
> (`shared/chapter_lock.py:60`) has zero callers outside its own definition. Treat these four as planned
> vocabulary, not enforced invariants, until the ADR is built.

**Import Adoption** _(planned)_:
Durable, leased, checkpointed work that turns one whole chapter's imported prose into a reviewed ChapterPacket, on demand. It owns adoption progress only (its lifecycle ends at `contract_proposed`), never mirroring ChapterPacket approval, ScenePacket approval, Job execution, or revision completion (ADR 0028).
_Avoid_: adopt job, import bypass, packetless draft

**Import Scene Evidence** _(planned)_:
An immutable, span-anchored LLM fact ledger extracted from one imported scene snapshot, keyed by `(scene_id, scene_version, prose_hash, extractor_schema_version)` and reusable across adoptions. It is evidence the ChapterPacket Author reads as `M#` sources; raw prose stays auditable but never enters the author prompt.
_Avoid_: scene summary, raw prose chunk, canon fact

**Revision Request** _(planned)_:
The durable record of an author's edit intent — immutable target `(scene_id, version)`, feedback, target pass, and origin — with a coarse lifecycle that outlives contract preparation until a revision Job is minted. At most one is active per target scene; its display phase is server-derived from the request, adoption, packets, and Job (ADR 0028).
_Avoid_: revise approval, redraft toast, queued job

**Chapter Workflow Lock** _(planned)_:
A per-chapter transaction-level advisory lock (`acquire_chapter_workflow_lock`) that serializes every authority-changing operation on a chapter — source-prose mutation, fingerprint-validate-and-mint, adoption compare-and-set publish, ChapterPacket propose/replace/approve/supersede, and request-resuming ScenePacket approval. It coordinates the cross-table invariant; it does not replace queue-claim row locks (ADR 0028).
_Avoid_: chapter row lock, global mutex, queue claim lock

**Claim Source Precedence**:
The enforced total order `LOCKED_CANON > DERIVED_FROM_MANUSCRIPT > DERIVED_FROM_OUTLINE > PLAUSIBLE_INFERENCE > UNRESOLVED` (with `FORBIDDEN` a separate surface prohibition, not a rank) that decides how conflicting packet claims resolve. A conflict the order cannot break becomes an approval-blocking open question; manuscript evidence never enters canon retrieval or overrides locked canon automatically (ADR 0029).
_Avoid_: claim label, source hint, canon promotion

**Editorial Convergence** _(proposed — pending author-blessed name)_:
The system's definition of "done" for unattended work on a scene: repeat produce → review → repair until the scene has no open Issue above a configured advisory severity, bounded by capped repair attempts. It is a stopping condition built on Issue resolution, deliberately NOT a Fidelity score (scoring is a deferred side project), and distinct from human approval.
_Avoid_: quality score, fidelity verdict, human sign-off, "no findings"

> **Implementation status — autonomy & authorization (ADR-0030 / ADR-0031, verified 2026-07-16).** The six
> terms that follow — Autonomy Control, Autonomy Epoch, Execution Authorization, Authorization Requirement,
> Operational Hold, and Approval Blocker — describe the **target design** of ADR-0030/0031 and are **not yet
> implemented**. In live code the machine-policy boundary is a single KV flag `autonomy_enabled` read per
> sweeper tick (`workers/sweeper.py`); there is no autonomy singleton, no epoch, and no Operational Hold
> state. Authorization is carried as **mutable** `RepairTask` fields (`requires_human_approval`,
> `human_approved_at`). A1b (ADR-0031 D16) has retired `HUMAN_REQUIRED` as an auto-approval ceiling rung
> — it is never autonomously approved and the sweeper no longer writes a false human stamp — but the full
> first-class *Authorization Requirement* axis (orthogonal to `authority_level`) is still A1c. Treat these six
> as planned vocabulary, not enforced invariants, until the ADRs are built. (`Job Book Ownership` above —
> ADR-0027 — **is** wired and is not affected by this note.)

**Autonomy Control** _(planned)_:
The singleton record owning the machine-policy safety boundary: `enabled`, `epoch`, `updated_at`. Read plainly at autonomous mint and at claim; locked and re-checked immediately before finalization, never across generation. It is orthogonal to the queue pause, which stops asynchronous claims of every authorization. Neither implies the other: unpausing the queue is not consent to autonomous spend, and disabling autonomy does not drain the queue (ADR 0030).
_Avoid_: kill switch, model override row, settings KV, queue pause

**Autonomy Epoch** _(planned)_:
The monotonic counter on Autonomy Control, incremented only when `enabled` changes. Work authorized by autonomous policy is stamped with the epoch it was minted under and must still match at claim and at finalization; a mismatch places the work on an Operational Hold instead of publishing it. An epoch is strictly stronger than a timestamp: it survives clock skew and cannot be satisfied by a call that merely started earlier.
_Avoid_: pause timestamp, generation counter, autonomy version, disabled_at

**Execution Authorization** _(planned)_:
The durable, immutable grant that authorizes one unit of background work to execute — `manual_command`, `autonomous_policy`, or `legacy_unclassified` — recorded as a grant event rather than a mutable field, so releasing held work *adds* a grant instead of overwriting the proof of how it was authorized. `manual_command` asserts a deliberate command through an explicit route, NOT an authenticated human identity (the system has none). Distinct from Authorization Requirement (what the work demands), from `authority_level` (blast radius), and from a Revision Request's `origin` (what created the intent).
_Avoid_: human command, human approval flag, origin, authority, actor

**Authorization Requirement** _(planned)_:
What a unit of repair work demands before it may execute: ceiling-gated, or an explicit manual grant. Orthogonal to `authority_level`, which states blast radius only — the two were conflated while `human_required` was a rung on the blast-radius ladder that a raised ceiling could silently negate.
_Avoid_: authority level, human required, ceiling, blast radius

**Decision Source**:
What produced an `Approval` row: `human_review`, `repair_system`, or `legacy_unclassified`. `Approval` is defined as the human's verdict and doubles as a training/export label, so quality metrics and any learning corpus read human decisions only. The repair system writes `Approval(REVISE)` rows as an operational instruction carrier; those are system decisions and must never be counted as editorial verdicts.
_Avoid_: approval reason, actor, execution authorization, approved_by

**Operational Hold** _(planned)_:
A nonterminal state for work that is retained but not executable, carrying a `hold_reason` (`autonomy_disabled`, `epoch_mismatch`, `legacy_authorization_unproven`). Held output is preserved, never published — no timeline advance, no supersede, no reviewable status. Release requires a fresh Execution Authorization under the current Autonomy Epoch and a re-evaluated predicate; re-enabling autonomy never auto-releases held work. Distinct from `quarantined`, which is integrity-terminal and ownerless.
_Avoid_: quarantined, autonomy held, paused, failed, blocked

**Approval Blocker** _(planned)_:
A durable, scene-scoped record that an unresolved scene-level open question is holding automated approval of a ScenePacket, carrying an explicit lifecycle and resolution state. The shared domain approval operation approves a scene or derives its beats only when that scene has no active Approval Blocker. Blocker state is never embedded in ScenePacket `body` and never live-read from `ChapterPacket.open_questions` (chapter-tier questions are not scene-scoped) (ADR 0031 D14).
_Avoid_: open_questions column, packet-body flag, derived chapter question, chapter open question
