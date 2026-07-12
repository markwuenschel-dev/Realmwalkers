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
