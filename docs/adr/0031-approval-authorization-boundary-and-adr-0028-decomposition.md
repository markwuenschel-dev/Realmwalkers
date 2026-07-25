# Approval/Authorization Boundary and ADR-0028 Decomposition

## Status

Accepted (planning only) — 2026-07-15. Author decisions **D6–D18**. Supersedes the #226–#228 ticket set, which is rejected. **No runtime implementation is authorized by this record.**

This is an **architecture correction record**: it exists because three successive planning passes encoded claims that primary source contradicts. Every statement below is either (a) verified at SHA `0f5f83c` with a citation personally opened, or (b) labelled an author decision, or (c) labelled unresolved. Nothing else is permitted to instruct implementation.

**Amended 2026-07-25 (build-time correction, A1c PR).** Verified-behavior facts **6, 9, and 10** are struck
through in place: each was pinned at `0f5f83c` and is false at HEAD `d664f87`. Fact 9's ADR-0028 layer is
live (constructors, `accept_revision_request`, `workers/import_adoption.py`, five `chapter_lock` callers);
fact 10's `Job.revision_request_id` has a writer chain; fact 6's authorization gate is what A1c replaced.
No decision is reopened — D11's remaining obligations and D9's grant-event record are explicitly still open.
The snapshot stays pinned; corrections are layered after it, per this record's own convention.

**Amended 2026-07-16** — author decisions **D14–D18** close the four blocking open questions (D14 resolves B-1, D15 resolves B-2's deployment-identity half, D16 resolves B-3, D17 resolves B-4) and refine D10's decomposition granularity (D18). The two new *factual* confirmations D15 rests on are verified at SHA `e3067ed` (`.env` carries no database URL; `docs/DEPLOY.md` names the EC2 target). The verified-behavior snapshot below stays pinned at `0f5f83c`; the new decisions are layered after it, not merged into it, and the original B-1…B-4 wording is retained struck-through under **Unresolved (blocking) → resolved** so the record still reads as the correction it was.

## Context — what was wrong

ADR 0030's driver plan was decomposed into #226/#227/#228. Bounded independent verification found the decomposition itself unsound:

- **#227's defect model was inverted.** It called a raw `ScenePacket.status = APPROVED` write "latent in any future auto-approver." It is **live**: `api/routers/chapters.py:393` is a router that writes it and never derives beats.
- **#226's boundary was wrong.** It framed authorization fields as "provenance, not gating" and claimed no inbound dependency. `human_approved` **is** the gate (`workers/production_repair.py:662`), and `human_approved_at` is both the apply-skip predicate (`workers/sweeper.py:229`) and the auto-verify selector (`:288`).
- **The escalation mechanism ratified in #217 does not work at the scene tier** — see D6 below.

The tickets were therefore not repairable in place: their boundaries were the defect.

## Verified current behavior (SHA `0f5f83c`; each citation personally opened)

1. **There is no scene-tier open-questions substrate.** `ScenePacket` columns are `id, book_id, chapter_id, chapter_packet_id, scene_seed_id, scene_no, status, qa_verdict, qa_warnings, body, sources, source_hash, stale_reason, created_at, updated_at` (`shared/models.py:309-329`). **No `open_questions`.** It exists only on `ChapterPacket` (`shared/models.py:292`), and `workers/scene_packet/author.py:85` states it is "a SIBLING column on ChapterPacket, not part of `body`."
2. **Scene-tier `can_approve` refuses only `BLOCKED` / `RATE_LIMITED`** and never reads open questions (`workers/scene_packet/approval_policy.py:99-110`). `STALE` is **approvable by design** — `:119-122`, "STALE stays approvable (re-approve IS the remedy)" — and `api/routers/chapters.py:392` depends on that.
3. **Chapter-tier `can_approve` does gate on open questions** (`workers/packet/approval_policy.py:96-97`). The two tiers have different semantics.
4. **Three literal `status = ScenePacketStatus.APPROVED` sites exist** (repo-wide grep): `api/routers/chapters.py:393` (router, gated, **no beat derivation**), `workers/scene_packet/__init__.py:111` and `:134` (the seam). A fourth path writes a **computed** enum: `api/routers/scene_packets.py:305`, ungated, but it **does** reconcile beats (`:313`).
5. **`derive_beats` is the only creator of `Beat(scene_packet_id=…)`** (`workers/scene_packet/beats.py:108`), and `approve_scene_packet` is exactly `status = APPROVED; derive_beats(...)` (`workers/scene_packet/__init__.py:111-112`). Without a Beat, `schedule_revision` refuses with `revision_contract_required` (`workers/job_scheduler.py:81-87`).
6. ~~**`human_approved` is an authorizer, not provenance** (`workers/production_repair.py:662`); `human_approved_at` gates the sweeper's apply loop (`workers/sweeper.py:229`) and selects its auto-verify set (`:288`).~~
   > **RESOLVED by A1c, 2026-07-25 — this fact described the defect D16 exists to remove, and the removal has landed.** The auto-verify selector was retired first (it keys off `RepairTask.status == RUNNING`, the real applied signal). A1c then replaced the gate: `apply_repair_task` decides once via `shared/authorization.py:authorize_repair`, reading the durable `RepairTask.authorization_requirement` plus the ceiling the caller **declares**; `human_approved_at` is a human audit stamp and a grant, no longer an apply-skip predicate, and the sweeper's skip now reads the requirement. `requires_human_approval` is a derived read-only projection and its physical column is dropped. **Left open by A1c:** D9's durable *Execution Authorization grant event* (an append-only record of how each execution was authorized) is still not built — A1c makes the decision explicit and single-sited, but the proof of authorization is still inferred from `human_approved_at` plus a run event, not held as a grant row.
7. **Nothing bounds repair attempts.** `RepairAttempt.attempt_no` is never compared to a cap at any site (`shared/models.py:987`; `workers/production_repair.py:576,577,601,789,790,795,861,1108`). `sweeper._attempts` is process-local (`workers/sweeper.py:71-73`), keyed by `run_id` (`:239`), and `drain_queued_repair_tasks` never consults it (`workers/background_work.py:151-214`).
8. **`JobKind` already declares mode explicitly** — `DRAFT | REVISE_FULL | REVISE_PASS` (`shared/enums.py:61-64`). The **job** contract is honest; the **scheduler command** contract is not: `skip_drafted` is a default parameter (`workers/draft_queue.py:290`) and `workers/job_scheduler.py:113-118` overrides it to `False`, minting a truthful `JobKind.DRAFT` over already-drafted prose.
9. ~~**The ADR-0028 layer is inert.** `ImportAdoption(`, `ImportSceneEvidence(`, `RevisionRequest(` have **zero constructors** in `src` or `tests`. `accept_revision_request` does not exist. `workers/import_adoption.py` does not exist. `shared/chapter_lock.py` has zero callers. `shared/claim_precedence.py` has zero importers including tests.~~
   > **STALE — corrected 2026-07-25 at HEAD `d664f87` (A1c PR).** Every clause of fact 9 is now false; ADR-0028 Slices 2/3a′/3b and ADR-0032 W0–W2 landed after this record was pinned to `0f5f83c`. Verified this session: `ImportAdoption(` is constructed at `shared/adoption_entry.py:344`, `ImportSceneEvidence(` at `workers/evidence_store.py:95`, `RevisionRequest(` at `workers/revision.py:229`; `accept_revision_request` is defined at `workers/revision.py:138`; `workers/import_adoption.py` exists; `shared/chapter_lock.py` has five importing modules (`api/routers/adoption.py`, `api/routers/reviews.py`, `api/routers/scene_packets.py`, `shared/adoption_entry.py`, `workers/import_adoption.py`). The `claim_precedence` clause was already known stale — it has a live production importer at `workers/packet/evidence.py:41`. **Consequence for D13/D18:** the "inert abstraction" premise no longer applies to this layer; D13's deletion default is spent, and no plan may still cite fact 9 as a reason to treat adoption as scaffolding.

10. ~~**`Job.revision_request_id` has no writer** (`workers/job_routing.py:106-122` is the only revise-Job constructor). It is read at `workers/context/revision.py:23,26` — an unreachable read, because the field is always `NULL`. Therefore `Approval.feedback` (`workers/context/revision.py:32`) is the **only demonstrated** feedback path.~~
    > **STALE — corrected 2026-07-25 at HEAD `d664f87` (A1c PR).** The field now has a writer chain: `workers/revision.py:264` passes `revision_request_id=request.id` into `schedule_revision`, which threads it through `workers/job_scheduler.py:110` to the revise-Job constructor at `workers/job_routing.py:124`. The read at `workers/context/revision.py:24,27` is therefore reachable, and `workers/worker.py:86,127,166` drives the request's status from the running Job. **D11 is affected and is NOT thereby discharged:** D11 forbids removing the `Approval.feedback` path until writer, reader, migration/backfill, dual-read interval, rollback, production preflight, and cutover evidence ALL exist and are verified. This correction establishes only the writer and the reader. The remaining five obligations are unverified here and stay open.

## Decisions

### D6 — Scene-tier approval and escalation

Unresolved scene-level open questions **block** automated approval. The shared domain approval operation must evaluate scene-tier blockers **before** it may approve or derive beats. No raw status write, router shortcut, worker shortcut, or permissive default may bypass it. The rule lives in **one** place — either the central approval policy extended to represent the blocker, or a single domain-level policy operation used by every caller. **Never duplicated per router or worker.**

**Blocking consequence (verified fact 1):** there is **no scene-tier open-questions field**. D6 cannot be implemented by "reading the blocker" — the blocker must first be **represented**. That representation is an open design question owned by Ticket 1 and is **unresolved**, not assumed. Candidate shapes (none chosen): a new `ScenePacket` column; a reserved key inside `ScenePacket.body`; derivation from the owning `ChapterPacket.open_questions` scoped to `scene_no`. Each has different migration and staleness consequences.

### D7 — Repair ceiling

**One persisted maximum of three repair attempts per repair cycle.** At the third failed attempt: do not enqueue a fourth automatic repair; park deterministically; persist the terminal reason; expose it to operators; remain idempotent across restart; require **explicit human action** to reopen a cycle. **No multi-tier automatic ladder in this milestone.** Any escalation beyond parking for human review is out of scope.

### D8 — Draft-versus-revise scheduling

**No default argument may serve as a safety invariant.** Scheduling mode must be explicit in the job **or command** contract. A repair/revision job explicitly declares revision mode; a new-draft job explicitly declares draft mode; missing or contradictory mode **fails closed**; revision workers may not silently create a new draft; draft workers may not consume a revision request.

**Scoping note (verified fact 8):** `Job.kind` already satisfies the *job* half. The gap is the **scheduler command** layer, where `skip_drafted` decides redraft-vs-revise implicitly. Ticket 3 owns the command contract, not a `JobKind` change.

### D9 — Foundational boundary before anything else

A new foundational ticket, **Shared approval and revision authorization boundary**, owns: the single domain approval operation; scene-tier blocker evaluation; the replacement for `human_approved` as an implicit authorizer; the auto-verification selector; explicit draft/revise mode; and the provenance downstream workers require. **Neither the revised #226 work nor any adoption work may proceed before this boundary is specified and approved.**

### D10 — ADR-0028 decomposition

ADR-0028 is **not** one implementation ticket. It splits into: (1) revision-request lifecycle and writer boundary; (2) import evidence and adoption persistence; (3) locking, precedence, and transaction ownership; (4) migration, backfill, cutover, rollback; (5) dead-code and obsolete-ADR cleanup after cutover. **Every ADR-0028 component gets an explicit implement / replace / delete ruling. "No decision" is a blocking state.** An inert abstraction is not preserved because an ADR names it. **Refined by D18** — the split is realized as vertical, dependency-ordered slices, each leaving a live writer/reader/verification path, not model-only layers.

### D11 — Current feedback path

`Approval.feedback` is the **current demonstrated path** and is **not** to be called legacy. `Job.revision_request_id` and the revision-request subsystem are **planned/unwired**. The current path may not be removed or bypassed until **all** exist and are verified: revision-request writer; reader/consumer; migration/backfill; dual-read or compatibility interval; rollback; production-data preflight; cutover acceptance evidence.

### D12 — Live database state

**Do not access a likely production database without explicit authorization.** A remote target is configured in the operator's local environment; its authorization was **not** established, so it was **not queried**. (The host is deliberately not recorded here — this record is versioned in the repository.) All cardinality claims are **unresolved** pending the Ticket 7 preflight, run against an explicitly authorized target.

**Deployment identity is itself unresolved:** the `README` and the operator's local configuration name **different hosting targets**. A preflight run against the wrong database would be worse than none, so the target must be identified before any query is run.

### D13 — Inert Layer 1 engine

`shared/claim_precedence.py` — zero demonstrated importers — **is not automatically part of the target architecture.** Ticket 6 owns a bounded decision record comparing: integrate via explicit production call sites; replace with the shared authorization boundary; delete as dead architecture. **Default recommendation is deletion or replacement** unless a concrete caller and a unique responsibility are demonstrated.

### D14 — Scene-tier blocker representation (resolves B-1)

Scene-level blockers are represented as **durable, scene-scoped Approval Blocker records** carrying an explicit lifecycle and resolution state — a normalized record, **not** a flag inside `ScenePacket.body` and **not** a live derivation from `ChapterPacket.open_questions`. The shared domain approval operation (D9) approves a scene or derives its beats **only when that scene has no active Approval Blocker.**

This closes B-1: D6 required a *representation* before the blocker could be enforced, and left three candidate shapes open; D14 chooses one and rejects the other two on stated grounds. A reserved key in `body` couples blocker lifecycle to prose edits and gives the blocker no independent resolution state. Live-reading `ChapterPacket.open_questions` reintroduces the tier mismatch of verified fact 3 — chapter-tier questions are not scene-scoped, and scene-tier `can_approve` never reads them today (verified fact 2). The normalized record is the only shape that gives a blocker its own lifecycle without borrowing the wrong tier's semantics. Building it remains Ticket 1's work; the *design* question B-1 named is now closed.

### D15 — Deployment identity and preflight target (resolves B-2's deployment-identity half)

The authoritative production data lives in the **PostgreSQL database on the EC2 instance** named in `docs/DEPLOY.md` (database `realmwalkers`). Verified at SHA `e3067ed`: `.env` no longer carries any database URL — the stale URLs D12 flagged have been removed, and they were never deployment evidence — and `docs/DEPLOY.md:16,29-33` names EC2 `i-018796c951839031d` with the `realmwalkers` Postgres as the single target (the `README` deploy badge is AWS, so the "different hosting targets" conflict D12 recorded is resolved by removing the stale side, not by choosing between two live ones). The Ticket 7 preflight **must** be read-only and target that authorized EC2 database; its results — not `migrations.py`, not the former `.env` — determine migration, backfill, and cutover handling.

**Scope of closure (honest boundary).** D15 resolves the *deployment-identity* conflict D12 left open and authorizes the preflight target. It does **not** resolve production **cardinality**: no authorized query has been run, so index presence, table population, and row counts remain unknown. B-2's cardinality half stays open — now unblocked, no longer a blocked design question.

### D16 — Authorization is separate from blast radius (resolves B-3)

`authority_level` classifies a repair's **blast radius** only. **Authorization** becomes a separate, explicit requirement — the *Authorization Requirement* already carried in `CONTEXT.md` (ceiling-gated, or an explicit manual grant). Ceiling-gated work may be automated within the configured ceiling; manual-grant work requires an explicit human grant **regardless of ceiling**. The `human_required` semantics migrate to **manual-grant behavior**, and `human_required` is retired as a blast-radius rung that a raised ceiling could silently negate — the conflation B-3 named.

This closes B-3. The `sweeper.py:59-60` comment vs `:167-171` code discrepancy B-3 pointed at is no longer an open *decision*: D16 fixes the intended branch (manual-grant work is human-gated by requirement, not by ladder position), so reconciling the two heads is now bounded implementation cleanup, not an unresolved policy choice.

### D17 — Manual replacement now; autonomous replacement later (resolves B-4)

A driver **must not** silently use `JobKind.DRAFT` to replace existing prose — the live path verified fact 8 exposes and ADR 0030's RETRACTED note documents. Initial drafts remain valid **only** for scenes with no prior prose; normal work over existing prose uses **revision**. An explicit **manual replacement mode** is introduced now for intentional full rewrites. **Fully autonomous replacement drafts remain the intended later capability** but require a separate policy decision and their own proof obligations before activation.

This closes B-4: the answer to "may an autonomous driver schedule `JobKind.DRAFT` at all?" is **not in this milestone**. Replacement is manual and explicit first; autonomous replacement is deferred behind a distinct decision.

### D18 — ADR-0028 decomposition granularity (refines D10)

D10's five-way split is realized as **several vertical, dependency-ordered slices, each leaving a live writer/reader/verification path** — not a single adoption mega-ticket and not inert model-only layers. Dependency order: (1) shared approval and blocker boundary (D9, D14); (2) revision-request lifecycle and explicit draft/revise scheduling (D8, D17); (3) import evidence / adoption behavior with required transaction ownership; (4) production migration/backfill/cutover, gated on the D15 preflight; (5) obsolete-layer cleanup (D10's dead-code ruling, D13). Each slice must demonstrate its own writer, reader, and verification before the next depends on it — the operational form of D13's rule that an inert abstraction is not preserved because an ADR names it.

## Consequences

- ADR 0030's "Layer 1 (objective floor) works day one" is false and already corrected in that ADR; D13 now questions whether Layer 1 belongs in the target at all, and D18 makes that concrete — Layer 1 earns a slice with a live writer/reader/verification path or it is deleted/replaced.
- ADR 0030's escalate-on-ambiguity sequencing depends on a scene-tier blocker whose **representation is now decided (D14: Approval Blocker records)** but **not yet built**. The autonomous path still cannot run before Ticket 1 delivers that substrate.
- #226/#227/#228 are **superseded** and non-actionable.
- **2026-07-16:** B-1, B-3, and B-4 are fully resolved (D14, D16, D17); B-2's deployment-identity half is resolved (D15) and only production **cardinality** remains, now unblocked against the authorized EC2 target; D10's decomposition is refined into vertical, dependency-ordered slices (D18).

## Unresolved (blocking) → resolved

All four blocking items are closed by the 2026-07-16 amendment (D14–D17). One leaves a non-blocking remainder, noted below. The original wording is kept struck-through so the record still shows what was open.

- ~~**B-1 (D6).** How scene-tier blockers are represented. No substrate exists. Owned by Ticket 1.~~ **RESOLVED by D14** — durable, scene-scoped Approval Blocker records. The *design* is closed; building the record stays Ticket 1's work.
- ~~**B-2 (D12).** All production cardinality. No authorized DB inspected.~~ **Deployment identity RESOLVED by D15** (authorized target is the EC2 `realmwalkers` Postgres). **Cardinality still open** — unknown until the read-only Ticket 7 preflight runs against that target. No longer a blocked *design* question; a pending measurement.
- ~~**B-3.** The ceiling ladder's intended branch (`sweeper.py:59-60` comment vs `:167-171` code) is undecided. D7 makes the *repair-cycle* cap explicit but does not rule on `RepairAuthorityLevel`'s `human_required` rung, which `workers/sweeper.py:168-170` documents as a deliberate opt-in.~~ **RESOLVED by D16** — `human_required` is retired as a blast-radius rung and re-expressed as an Authorization Requirement (manual-grant), orthogonal to `authority_level`. The `sweeper.py` two-head discrepancy is now implementation cleanup, not an open decision.
- ~~**B-4 (D8).** Whether an autonomous driver may schedule `JobKind.DRAFT` at all, given a draft job supersedes existing prose (`workers/pipeline.py:318`).~~ **RESOLVED by D17** — not in this milestone. Replacement over existing prose is manual and explicit; autonomous replacement is deferred behind a separate policy decision and proof obligations.
