# Autonomous Contract Approval and Editorial Convergence

## Status

Accepted — architecture agreed via grilling 2026-07-13. Implementation not yet started; the first shippable slice is still to be scoped.

**Amended 2026-07-15** (author decisions D1/D2, evidence-closure audit at SHA `3246a8c`): added the binding **approval invariant** (no raw `status = APPROVED` write); corrected the **attempt-bound** claim, which previously asserted a runtime bound that does not exist — it is now stated as intended-but-unimplemented; and recorded the **substrate reality** under Open questions. Driver architecture decided in #219.

## Decision

For the **unattended (autonomous) path**, the system **auto-derives and auto-approves** a scene's story contract by policy, rather than requiring human approval at each gate. "Done" for unattended work is **Editorial Convergence**: repeat produce → review → repair until the scene has **no open Issue above a configured advisory severity**, bounded by capped repair attempts. The human moves from *gating every contract* to *reviewing converged output* (plus escalations).

The contract is **derived from the prose via ADR 0028 adoption's span-anchored evidence extractor** (`Import Scene Evidence`, prose-hash-keyed), never from a summary — because once no human reviews the contract, derivation fidelity is safety-critical: a drifted contract would silently corrupt the prose the converge loop rewrites to match it.

This **reverses the founding human-gated invariant** (`CONTEXT.md`: "author-approved story contracts … human-gated") and **supersedes ADR 0028's** "no ScenePacket is auto-derived or auto-approved" — for the autonomous path only.

**Auto-approval is not blind — it escalates to the human on genuine ambiguity, via two layers the author never hand-configures:**
- **Layer 1 (objective floor — ~~exists~~ DECLARED BUT UNWIRED, see Open questions):** the escalation line for canon conflict *is* **ADR 0029 Claim Source Precedence**. A conflict the precedence order resolves is auto-resolved silently; one it **cannot break already becomes an open question** — that is what the author sees. New prose contradicting `LOCKED_CANON` escalates; soft claims disagreeing settle by the order. The author specifies nothing; the order draws the line by claim strength.
- **Layer 2 (learned personal policy, new work):** anything above the objective floor — the author's own sensibility about what's worth flagging — is **learned from their rulings**, not declared. Each resolved escalation is a labeled example; the learn-from-edits substrate that today distills voice/dialogue rules (`PovProfile`, `RuleProposal`, `learning/distill.py`) is pointed at adjudication. The author never writes a flag-rule.

Sequencing: the system **starts at escalate-on-ambiguity** (Layer 1) and **matures toward agent auto-adjudication** as Layer 2 accumulates rulings. "Escalate-on-ambiguity" and "agent auto-adjudicates" are the same system at two ages, not competing choices.

> **Correction 2026-07-15 — this paragraph previously read "Layer 1, works day one".** It does not. `shared/claim_precedence.py` — the engine that draws the objective floor — has **zero importers** repo-wide, including tests; `conflict_needs_open_question` and `conflict_kind` have no callers. `enums.py:159` mentions the module only in a comment. **Layer 1 is a design, not a running gate**, and wiring it is unowned work. Any plan that treats the objective floor as available today is building on intent.

**Approval invariant (binding).** An automated approver **may not directly write `ScenePacket.status = APPROVED`**. Automated and human approval **must invoke the same domain-level approval operation**, which preserves the full approval invariant — including beat derivation and any required revision-contract state. A raw status write is forbidden.

This is not stylistic. Verified at SHA `3246a8c`: `derive_beats` (`workers/scene_packet/beats.py:108`) is the only creator of `Beat(scene_packet_id=…)`, and `approve_scene_packet` (`workers/scene_packet/__init__.py:111-112`) is exactly `status = APPROVED; derive_beats(...)`. A status write that skips it leaves no Beat, and `schedule_revision` then refuses with `revision_contract_required` (`workers/job_scheduler.py:81-87`) — the same 409 this ADR exists to escape. Auto-approval by raw write would therefore auto-produce the defect.

## Context

- Imported/injected prose lands as `Scene` rows with **no contract** (no outline / ChapterPacket / ScenePacket / Beat). Revise 409s (`revision_contract_required`) and the Packets tab greys out (Propose gated on `chapter.outline`).
- The autonomous **convergence machinery partly exists** in the repair loop (`production_repair.py`): it converges on Issue resolution (`no_new_issues = not remaining and not created_new_issues`, `target_issue_resolved = not remaining`). There is no convergence *driver* in that module — iteration comes from `sweeper.py` ticks (apply → revision Job → next tick verifies → `NEEDS_ANOTHER_REPAIR` re-queues at `production_repair.py:1350`).
- **Bounded repair attempts — intended invariant, NOT implemented.** An earlier revision of this ADR stated the loop is "bounded by `RepairAttempt.attempt_no`." **That was false and is corrected here.** The three states are distinct and must not be conflated:
  - **Intended invariant.** Autonomous repair is bounded: a repair cycle stops or parks deterministically at a cap (default **3 attempts per repair cycle**), exposing a terminal reason.
  - **Current implementation gap (verified at SHA `3246a8c`).** No such bound exists. `RepairAttempt.attempt_no` is written but **never compared against any cap** at any of its sites (`models.py:987`; `production_repair.py:576,577,601,789,790,795,861,1108`) — it is a sequence number only. The sole cap is `sweeper._attempts` (`sweeper.py:73`), which is **process-local** (resets on redeploy, `sweeper.py:71`), keyed by **`run_id`** rather than task, and **never consulted by `drain_queued_repair_tasks`** (`background_work.py:151-214`). Because a `NEEDS_ANOTHER_REPAIR` verdict re-queues the task (`production_repair.py:1350`) and the drain re-applies it without consulting any cap, **the drain path is unbounded today.**
  - **Required future enforcement.** The limit must be enforced in a **central repair-attempt policy**, not separately per worker; it must count **persisted** attempts rather than process-local retries; it must stop or park **deterministically** at the cap; it must **expose the terminal reason**; it must remain **idempotent across restart**; and it must be tested **below, at, and above** the cap.
- **But that loop enacts every fix via `schedule_revision`** (`production_repair.py:53`), which hard-requires an approved ScenePacket. So **there is no path to autonomous convergence on a contract-less scene** — the repair path *is* the contract-first guard, not an escape from it.
- Therefore autonomy is structurally **coupled to contracts**. The pain the author actually has is the **manual gates** (Propose → adjudicate → Approve → Derive → Approve), not the existence of the contract records. Removing the contracts (a considered alternative, (C) below) would delete the substrate the done-loop runs on.

## Alternatives considered

- **(C) Contract-free convergence loop** — reviewers flag → drafter-in-revise-mode reworks prose directly, bypassing `schedule_revision`; no packets ever. Rejected: reimplements convergence from scratch, abandons the working Issue / RepairTask / SceneFidelity ecosystem, and reviewers give a thinner signal with no contract to check against.
- **(B) Keep human contract approval, automate only post-approval** — rejected: not autonomous; the manual gates that block the author remain.
- **Outliner → Packet Author derivation** (prose → lossy outline → packet authored from the summary) — rejected: two lossy hops produce contract drift that, under auto-approval, silently corrupts the prose (the human approval gate that used to catch it is gone). Adoption's evidence extractor derives the contract straight from the prose instead.

## Consequences

- The human's role shifts from **gating contracts** to **reviewing converged output** and handling escalations. Human judgement is spent where it adds value (genuine story/canon ambiguity), not on rubber-stamping obvious packets.
- ADR 0028's adoption/evidence machinery becomes **more** central, not less — a faithfully-derived contract matters *more* under auto-approval, because no human catches derivation drift before the converge loop rewrites prose to match it.
- The "no auto-approve" guarantees in ADR 0028 remain in force for any **explicitly human-driven** path; this ADR carves out the autonomous path only.
- **Convergence produces a review-ready scene, not a published one.** The converged scene lands in the existing **Inbox**; the author remains the **final gate on output** and acts through the existing decision endpoint: *approve* (done), *revise-with-feedback* (re-enters the converge loop as fresh intent), or *hand-edit* (author's text becomes canonical). "Autonomous to finished product" means **the machine finishes the work; the author finishes the decision.** The human gate moved off contracts (low-value rubber-stamp) and onto output (where authorial judgement is irreplaceable) — it was not removed.
- **New work required, not just wiring:** (1) finish ADR 0028 adoption's runtime and flip its approval gates to policy auto-approval; (2) point the learn-from-edits substrate at adjudication (Layer 2); (3) let the converge loop run against an autonomously-approved contract. The from-scratch drafter is skipped for injected prose (`schedule_contract_first_draft_jobs(skip_drafted=True)`), so the drafter only ever runs in **revise** mode over the author's words.

## Open questions

**Scope.** What is the first shippable slice, given the full engine is larger than the original "enrichment-only v1" this grilling started from. The driver architecture is decided (#219).

**Substrate reality (verified at SHA `3246a8c`, 2026-07-15).** This ADR's "New work required, not just wiring" understates the starting position. The ADR-0028 substrate it builds on is **declared but inert** — not partially built:

- `ImportAdoption(`, `ImportSceneEvidence(`, `RevisionRequest(` have **zero constructors** anywhere in `src` or `tests`. Nothing writes or reads those three tables.
- `accept_revision_request` — which ADR 0028 says *all* revise/redraft paths go through — **does not exist** as a function.
- `workers/import_adoption.py`, named by `models.py:335` and ADR 0028:27, **does not exist**.
- `shared/chapter_lock.py` has **zero callers**; `ChapterWorkflowBusy` has no consumer.
- `shared/claim_precedence.py` — ADR 0029's engine, and this ADR's Layer-1 objective floor — has **zero importers**, including tests. `conflict_needs_open_question` and `conflict_kind` have no callers.
- `Job.revision_request_id` is declared, migrated, FK'd, indexed, and read — but has **no writer**, so it is always `NULL`. The branch documented as "LEGACY backward-compat only" (`workers/context/revision.py:30-37`) is consequently the **only live feedback path**, contradicting `models.py:407` and ADR 0028:33.

**Consequence for planning:** Layer 1 ("works day one") does not work day one — its engine is unwired. Any slice that assumes an adoption record, a revision request, a chapter lock, or precedence adjudication exists at runtime is assuming scaffolding is a system.

**Unresolved — live database state.** No claim in this ADR about deployed data is verified. No authorized live database was inspected. Index presence, table population, and cardinality are **unknown**; source migrations cannot prove an old deployment applied them. Any migration or backfill must run a fail-closed preflight rather than infer state from `migrations.py`.
