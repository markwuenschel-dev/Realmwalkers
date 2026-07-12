# Import Adoption and Durable Revision Requests

## Decision

An imported scene is **not** given a packetless drafting bypass. Instead:

- A durable, leased **Import Adoption** turns a chapter's imported prose into a reviewed **ChapterPacket** (a contract), one whole chapter at a time, on demand.
- A durable **Revision Request** records the author's edit intent — immutable target `(scene_id, version)`, feedback, target pass, origin — and survives until its contract is ready and a revision Job is minted.
- A **Job** remains executable worker work that produces or revises a **Scene**. There is no `adopt_chapter` job kind. Adoption produces a *ChapterPacket*, so it is a separate record with its own claim loop.

The author's revise action on an uncontracted imported scene now returns **`202` + `RevisionRequestOut`** and automatically starts one chapter adoption, replacing the previous "schedule or `409` rollback" behavior. Both human approval gates (ChapterPacket, then target ScenePacket) remain mandatory; no ScenePacket is auto-derived or auto-approved.

## Context

Imported prose (e.g. the seed prologue) exists as `Scene` rows with `prose_source="imported"` and **no** Beat/ScenePacket contract at any tier. The contract-first resolver (`workers/draft_queue.py`) structurally requires an approved, non-stale ScenePacket before a revise Job can be minted, so `schedule_revision` returned a `DraftQueueBlocker(reason="revision_contract_required")` and the endpoint raised a `409`, **rolling back** the pending `Approval` and `SceneStatus.REVISION_REQUESTED`. The author's intent was lost, the scene stayed opaque ("imported, can't revise yet"), and the imported prologue was permanently stuck.

Three properties were missing:

1. **Durable intent** — a place for "revise this scene" to live while the contract is being prepared, so a refresh or redeploy never drops it.
2. **A contract path for imported prose** — a way to turn existing prose into a reviewed ChapterPacket without silently promoting it to canon or truncating a large chapter into one prompt.
3. **Recovery** — a boot path that rebuilds the durable intent for legacy `revision_requested` scenes (the stuck prologue) without spending model tokens unbidden.

## Design

### Three domain records

- **ImportAdoption** — durable, leased, checkpointed work for **one whole chapter**. Own table + own claim loop (`workers/import_adoption.py`); it commits per-scene checkpoints between long model calls, so it cannot reuse the Job worker's transaction-held-through-generation model. Records `source_fingerprint`, `mode`, `lifecycle`, `error`, an immutable **evidence manifest** (the exact `ImportSceneEvidence` shard ids/hashes it consumed), and the resulting `chapter_packet_id`. Lifecycle: `awaiting_start | queued | running | contract_proposed | failed | invalidated | cancelled`. `contract_proposed` is terminal-success, reached atomically with a linked `ChapterPacket(status=proposed)`; a blocked/unusable packet → `failed`. One adoption serves **every** active request in its chapter.
- **ImportSceneEvidence** — one resumable **LLM extraction** per snapshot scene into a span-anchored fact ledger (present/referenced entities, POV, setting/time, events, asserted facts, state/inventory/relationship changes, reveals, withholds, entry/exit state, continuity anchors, ambiguities, canon conflicts). It is an **immutable source artifact** keyed by `(scene_id, scene_version, prose_hash, extractor_schema_version)`, not owned by a single adoption — so re-adoption reuses unchanged shards. Oversized scene → deterministic chunk-extract + bounded merge (shards retained); never raw-text truncation. Raw prose stays auditable in the Desk but is never placed in the author prompt.
- **RevisionRequest** — immutable target `(scene_id, version)` + `feedback` + `target_pass` + `origin`, with a mutable coarse `lifecycle = awaiting_contract | queued | running | completed | held | failed | superseded | cancelled`. At most **one active** request per `target_scene_id`, enforced by a partial unique index over the active states. The server returns a derived `display_phase` + `required_action` composed from the request + its adoption + ChapterPacket + target ScenePacket + Job.

### Job link

`Job` gains a nullable `revision_request_id` FK (added `NOT VALID` in `_EXTRA_DDL`). Feedback is **not** copied onto the Job — it lives immutably on the `RevisionRequest` beside its source `Approval`. The revision-context loader resolves feedback through `Job.revision_request_id`, never "latest revise Approval" (legacy jobs with no link fall back to the latest Approval only for backward compatibility).

### Two adoption modes

- **initial** (no approved ChapterPacket): snapshot every non-superseded scene, extract evidence for imported/uncontracted prose, reuse approved scene-contract projections for already-contracted scenes, propose the first chapter-wide ChapterPacket.
- **amendment** (an approved ChapterPacket exists but a newly imported scene has **no seed** — the one case normal re-derive cannot fix): copy-on-write from the current approved ChapterPacket + evidence for the new prose. On approval it **atomically becomes the sole active ChapterPacket**; the prior packet → superseded, affected ScenePackets → stale/re-derived. If the target already has a valid ScenePacket → no adoption; a merely-stale seed → normal re-derive.

### The gated chain

1. Revise on an uncontracted imported scene → **auto** start adoption.
2. Adoption finishes → **auto** proposes the ChapterPacket.
3. Human approves the ChapterPacket.
4. Human explicitly clicks **"Derive chapter scene contracts"** (chapter-wide, via the ScenePacket **facade**; Desk states the count then focuses the target).
5. Human approves the target ScenePacket.
6. In the same transaction: revalidate request + adoption snapshot, mint the revision Job, commit, then kick the drain.

No auto-derive or auto-approve after chapter approval — that remains an explicit, visible model-spend step (`display_phase = "Derive target scene contract"`). Approving a non-target proposed packet only resumes a request if that exact scene has one waiting.

### Invalidation & the chapter workflow lock

The adoption `source_fingerprint` is a hash over sorted `(scene_no, scene_id, version, prose_sha256)` for every snapshotted scene — **prose-hash based**, because the inbox hand-edit path mutates `scene.prose` in place (not every mutation is a new row). Any source-prose mutation (import overwrite, inbox hand-edit, delete, reparent, redraft/revert/repair) eagerly, in the same transaction, invalidates the adoption (`invalidated`), marks derived ScenePackets `STALE`, and leaves waiting requests durable as "Re-adopt needed"; a running Job is never cancelled mid-generation. An Activity event fires once per fingerprint transition. Correctness rests on a **lazy, fail-closed** recompute at mint time — the revision seam refuses to mint unless the current fingerprint matches the active adoption's snapshot *and* the target ScenePacket is `APPROVED`/non-stale.

All authority-changing operations acquire a per-chapter transaction-level advisory lock — `acquire_chapter_workflow_lock(session, chapter_id)` wrapping `pg_advisory_xact_lock(hashtextextended('dominion:chapter-workflow:' || chapter_id, 0))`. It guards: source-prose mutation; validate + mint; adoption's compare-and-set publish; ChapterPacket propose/replace/approve/supersede; and target-ScenePacket approval that resumes a request. Mandatory order: locate chapter (no decisions) → acquire lock → reload rows under row locks → recompute/validate → write + commit. Adoption never holds it across evidence/author model calls — only the short final publish transaction, where it rechecks the fingerprint so `invalidated` beats a late completion. A `lock_timeout` yields a retryable `chapter_workflow_busy` conflict (worker retries with jitter). The lock coordinates the cross-table invariant; it does not replace `FOR UPDATE SKIP LOCKED` queue claims or the ordinary row locks on changed rows.

### Response taxonomy

All revise/redraft paths (reviews decide, continuity resolve, ScenePacket-approval resume) go through one `accept_revision_request(...)` seam so status mapping cannot drift.

| Outcome | Response |
|---|---|
| New durable intent with any forward path (job, adoption, re-adoption, or paused queue) | `202 RevisionRequestOut` |
| Exact replay (same scene + source hash + pass + feedback) | `200 RevisionRequestOut` |
| Missing scene or chapter | `404` |
| Malformed body / unsupported pass / missing-empty source | `422` |
| State/authority/stale-client conflict | `409` typed `blockers[]` |

`202` covers: imported/no-contract, valid-contract, stale adoption/packet, paused queue, and conflict-discovered-during-adoption (an async human approval hold, never a sync blocker). Repeat semantics: identical replay → `200`; different intent while `awaiting_contract`/`queued` → supersede + cancel only an **unclaimed** Job → `202`; different intent while **running** → `409 revision_in_progress` (never silently swallow new feedback behind a disabled button). Hard `409` blockers: `scene_superseded` (return latest), `scene_changed` (expected prose hash/ETag mismatch), `revision_in_progress`, `ambiguous_active_scene_contract` (only active compatible duplicates count), `ownership_integrity_hold` (`Scene → Chapter → Book`). On any `404`/`409`/`422`: persist neither the `Approval` nor the `RevisionRequest` — the rollback guarantee stands. `expected_prose_hash` is a **required** acceptance/concurrency input.

### Durability & recovery

Adoption is durable like jobs: `FOR UPDATE SKIP LOCKED` claim, lease `claimed_by/at`, queue-pause support, idempotent source fingerprints, boot recovery of expired claims, and Activity events for start, failure, `contract_proposed` (approval-needed), queueing, and completion. Unpausing resumes **three** drains: Job, RepairTask, adoption.

Boot **reconciliation** for legacy `revision_requested` scenes with no active request runs in the lifespan/audit seam — **not** in `apply_lightweight_migrations` (which also runs in the test fixture and must not recreate intent or Activities). Safe boot order: migrate → reconcile + commit recovery records → start drains. Reconciliation idempotently recreates `RevisionRequest{origin=legacy_reconciliation, awaiting_contract}` from the latest revise `Approval`, links (not retries) any old failed Job as audit evidence, and creates the adoption in `awaiting_start` — **not worker-claimable**. A missing valid revise Approval creates neither record; it becomes a deduplicated integrity hold/Activity (intent is never fabricated). Legacy adoption spends only after an explicit "Start contract adoption" action (or a bounded one-shot operator command tied to the read-only report fingerprint) — an unpaused queue is **not** consent for that new historical spend. Fresh, user-initiated revisions still auto-start adoption.

## Alternatives considered

- **Packetless drafting bypass for imported prose** — rejected: silently converts prose to canon-strength and skips the review gates the whole system exists to enforce.
- **`adopt_chapter` Job kind** — rejected: a Job would no longer always produce a Scene, contaminating ADR 0027's `book_id`-ownership integrity probe and the direct-context resolver.
- **A universal `claim_next()` shared by Job and adoption** — rejected: it would force Jobs to commit their claim before drafting (changing proven worker behavior) or force adoptions to hold a transaction across long model calls (preventing durable checkpoints). The shared module owns only the control plane (pause, drain single-flight, boot recovery, Activity/error conventions).
- **`Job.revision_feedback` column** — rejected: it duplicates immutable intent that belongs on the `RevisionRequest`; the link suffices.
- **Boot auto-spends legacy adoption when the queue is unpaused** — rejected: a year-old revise click did not authorize the new per-scene extraction + contract-generation cost shape.
- **Store the fine UI phases on the request** — rejected: they would drift from the adoption/packet rows that actually drive the gates; the server derives them instead.

## Consequences

The stuck imported prologue becomes recoverable: reconciliation rebuilds its intent, and it drafts only after its generated ChapterPacket and target ScenePacket are explicitly approved. Adoption spend is on demand and bounded (unused imported chapters cost nothing). Imported prose is durable evidence, never silently canon and never truncated. See ADR 0029 for the claim-source precedence policy that governs how manuscript evidence competes with canon inside the proposed ChapterPacket.
