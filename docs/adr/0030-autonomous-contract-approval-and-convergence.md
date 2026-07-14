# Autonomous Contract Approval and Editorial Convergence

## Status

Accepted — architecture agreed via grilling 2026-07-13. Implementation not yet started; the first shippable slice is still to be scoped.

## Decision

For the **unattended (autonomous) path**, the system **auto-derives and auto-approves** a scene's story contract by policy, rather than requiring human approval at each gate. "Done" for unattended work is **Editorial Convergence**: repeat produce → review → repair until the scene has **no open Issue above a configured advisory severity**, bounded by capped repair attempts. The human moves from *gating every contract* to *reviewing converged output* (plus escalations).

The contract is **derived from the prose via ADR 0028 adoption's span-anchored evidence extractor** (`Import Scene Evidence`, prose-hash-keyed), never from a summary — because once no human reviews the contract, derivation fidelity is safety-critical: a drifted contract would silently corrupt the prose the converge loop rewrites to match it.

This **reverses the founding human-gated invariant** (`CONTEXT.md`: "author-approved story contracts … human-gated") and **supersedes ADR 0028's** "no ScenePacket is auto-derived or auto-approved" — for the autonomous path only.

**Auto-approval is not blind — it escalates to the human on genuine ambiguity, via two layers the author never hand-configures:**
- **Layer 1 (objective floor, exists):** the escalation line for canon conflict *is* **ADR 0029 Claim Source Precedence**. A conflict the precedence order resolves is auto-resolved silently; one it **cannot break already becomes an open question** — that is what the author sees. New prose contradicting `LOCKED_CANON` escalates; soft claims disagreeing settle by the order. The author specifies nothing; the order draws the line by claim strength.
- **Layer 2 (learned personal policy, new work):** anything above the objective floor — the author's own sensibility about what's worth flagging — is **learned from their rulings**, not declared. Each resolved escalation is a labeled example; the learn-from-edits substrate that today distills voice/dialogue rules (`PovProfile`, `RuleProposal`, `learning/distill.py`) is pointed at adjudication. The author never writes a flag-rule.

Sequencing: the system **starts at escalate-on-ambiguity** (Layer 1, works day one) and **matures toward agent auto-adjudication** as Layer 2 accumulates rulings. "Escalate-on-ambiguity" and "agent auto-adjudicates" are the same system at two ages, not competing choices.

## Context

- Imported/injected prose lands as `Scene` rows with **no contract** (no outline / ChapterPacket / ScenePacket / Beat). Revise 409s (`revision_contract_required`) and the Packets tab greys out (Propose gated on `chapter.outline`).
- The autonomous **convergence machinery already exists** in the repair loop (`production_repair.py`): it converges on Issue resolution (`no_new_issues = not remaining and not created_new_issues`, `target_issue_resolved = not remaining`), bounded by `RepairAttempt.attempt_no`.
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

None outstanding on the architecture. The remaining decision is **scope**: what is the first shippable slice, given the full engine is larger than the original "enrichment-only v1" this grilling started from.
