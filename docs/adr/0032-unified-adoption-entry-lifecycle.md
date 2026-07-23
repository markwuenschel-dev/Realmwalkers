# ADR-0032: Unified Adoption-Entry Lifecycle

**Status:** accepted · **implementation_authorized:** false · **Decision owner:** mark
**Charted as:** wayfinder map #258, ticket #260 (`/expanded-grill-with-docs`).

**Refines and supersedes ADR-0028's _tail_ decisions** for: automatic adoption entry on revise, legacy
reconciliation, consent & liveness, active-adoption uniqueness, reverse cancellation, and command-response
semantics. It does **not** supersede ADR-0028's **landed** architecture — evidence extraction, the leased
adoption worker, publish/compare-and-set, derive-binding, and request resume all stand. References ADR-0029
(claim-source precedence) unchanged. Out of scope: amendment mode (#261), chapter-tier ChapterPacket lock
coverage (#259), the ADR-0031 authorization axis, autonomy, cost ceilings, fidelity scoring.

## Decision

Auto-start-on-revise and legacy reconciliation are **two entry paths into one canonical adoption-entry
lifecycle**, not two peer slices. There is exactly **one writer** of the active adoption states
(`awaiting_start`/`queued`/`running`); all four callers route through it; and the "at most one active
adoption per chapter" invariant is a **database structural guarantee**, not merely a lock convention.

- **One mutation primitive**, two API layers: `_ensure_import_adoption_locked(...)` (module-private,
  assumes the chapter workflow lock is held, **never commits**) and `ensure_import_adoption(...)` (standalone
  wrapper that acquires `run_under_chapter_workflow`).
- **Consent** is an explicit input (`spend_consent → queued`, `deferred_consent → awaiting_start`), never a
  boolean.
- **Liveness** is a persisted, orthogonal axis on the adoption (`request_bound | operator_independent`) that
  answers exactly one question: _may this adoption remain alive without a `RevisionRequest`?_
- The sync revise entry couples request acceptance and adoption entry **atomically** under one chapter
  transaction; adoption remains a **chapter-shared** resource, not a child owned by a request.
- The revise action's HTTP response becomes a **typed command envelope** carrying two orthogonal facts;
  `200` is reserved for a genuinely inert replay, `202` for any accepted forward work.

## Context (verified at HEAD)

Slice 3b shipped **explicit operator Start only**. The consequences the unification must resolve:

- The mint/promote logic lives **inline** in `start_contract_adoption._body()` (`api/routers/adoption.py:137-179`).
  Replicating it in the revise seam or a boot reconciler would create multiple lifecycle writers.
- `accept_revision_request` (`workers/revision.py:138`) — the sole `RevisionRequest` constructor
  (`revision.py:229`) — lands the request at `awaiting_contract` and does **not** touch adoption. Auto-start
  wires here.
- **Four** callers construct or mint adoptions today, not three: operator **Start** (`adoption.py:170`),
  operator **Re-author** (`adoption.py:261`, force-token + `reauthor_of_adoption_id` lineage), and — after
  this ADR — the **sync** revise entry and boot **reconciliation**.
- The "≤1 active adoption per chapter" invariant is enforced **only** by a read-check under the chapter lock
  (`adoption.py:159`); the sole unique index is on `force_author_token` (`shared/migrations.py:320`). The
  chapter lock is a coordination protocol — a caller bypassing it bypasses the guarantee (`shared/chapter_lock.py`).
- No reconciliation writer exists; `awaiting_start` is read (promoted by Start, `adoption.py:163`) but never
  written. The two direct HTTP revise surfaces (`api/routers/reviews.py:148`, `:262`) return ad-hoc dicts and
  derive `200/202` solely from `AcceptResult.replayed`.
- `ImportAdoptionStatus.CANCELLED` is already documented as "an `awaiting_start`/`queued` adoption with no
  remaining active requests" (`shared/enums.py:399`) — a reverse rule nothing enforces.

## Design

### D1 — The one writer (two layers, four callers)

```
_ensure_import_adoption_locked(session, *, chapter_id, mode, consent, liveness_basis,
                               force_author_token=None, reauthor_of=None) -> (adoption, entry_effect)
    # assumes run_under_chapter_workflow is held; NEVER commits.
    # evidence-only validation · active-row reuse · awaiting_start promotion · fingerprint capture · insert
ensure_import_adoption(session, ...same...)   # standalone wrapper; acquires run_under_chapter_workflow
```

Callers:

| caller | seam entry | consent | liveness_basis |
|---|---|---|---|
| sync revise (`revision.py`) | the **locked primitive**, inside its own atomic txn | `spend_consent` | `request_bound` |
| operator Start (`adoption.py:127`) | the **wrapper** | `spend_consent` | `operator_independent` |
| operator Re-author (`adoption.py:199`) | the **wrapper** | `spend_consent` | `operator_independent` |
| boot reconciliation | the **wrapper** | `deferred_consent` | `request_bound` |

An **AST-aware production-source test** asserts that no `ImportAdoption(...)` constructor exists under
`src/dominion` except the ORM class declaration and the one inside the primitive. (Not a substring grep.)

### D2 — Consent (`spend_consent`/`deferred_consent`) and liveness (`request_bound`/`operator_independent`) are orthogonal

Consent decides the **initial status** at entry; liveness decides **survival** when no request remains.
An operator command supplies `spend_consent` **and** upgrades liveness **monotonically** to
`operator_independent`: if operator Start (or Re-author) touches an existing `request_bound` adoption — even
one already `queued` — it promotes/keeps it `queued`, sets the operator lineage, and upgrades its basis. It
never mints a second active row.

### D3 — Active-adoption uniqueness is a structural invariant

```sql
CREATE UNIQUE INDEX uq_active_import_adoption_per_chapter
  ON import_adoptions (chapter_id)
  WHERE status IN ('awaiting_start', 'queued', 'running');
```

Terminal states (`contract_proposed`, `failed`, `invalidated`, `cancelled`) never block a later adoption.
Collision handling in the primitive is **reread-and-reconcile inside a savepoint**, catching only this named
constraint — never a blanket `409`:

- compatible existing row → return it idempotently;
- existing `awaiting_start` + `spend_consent` → promote to `queued`;
- existing `queued`/`running` → return it (a new request **joins** it — see D8);
- genuinely incompatible state → typed conflict.

The index is duplication-prevention only; it does **not** make lock-bypass structurally impossible. Lock
discipline is still enforced by the single writer, caller tests, and lock assertions.

### D4 — Atomic command coupling at the sync entry; adoption stays chapter-shared

`accept_revision_request` coordinates both domains under **one** chapter transaction via the D1 seam; it does
**not** construct adoption rows itself. Ownership: the revision domain owns durable author intent; the
adoption domain owns chapter contract reconstruction; the acceptance command atomically coordinates.
`RevisionRequest.import_adoption_id` is a **soft serving link** (one adoption serves every active request in
its chapter).

```
run_under_chapter_workflow(chapter_id):
    classify → resolve replay/replacement → persist Approval + RevisionRequest
    try contract-backed revision-Job mint
    if the request still needs a contract:
        ensure/promote chapter adoption via the locked primitive (spend_consent, request_bound)
        link request.import_adoption_id
    commit-all-or-rollback-all
```

Structure: public `accept_revision_request(...)` owns the lock; internal
`_accept_revision_request_locked(...)` is the body for a caller already inside the chapter transaction. **No
`lock_already_held` flag.** The two HTTP surfaces (`reviews.py:148`, `:262`) both acquire the chapter lock for
the whole authority-changing transaction — before any `scene.status`/`Approval` mutation, per the lock
contract.

### D5 — Exact-replay carries consent

A fresh Revise supplies spend consent even when an identical request was previously restored by
reconciliation. On an exact replay the command still runs the D1 seam:

- replay + `awaiting_start` adoption → **promote** to `queued`;
- replay + no adoption → **create** `queued`;
- replay + `queued`/`running` adoption → **inert** (no state change).

Otherwise a reconciliation-recovered request would stay stuck behind operator Start after the author
re-clicked Revise.

### D6 — F3: the Revise action _is_ spend consent

A fresh imported-scene Revise queues adoption with **no second confirmation and no sync-only cost ceiling**.
The 30-scene surprise-cost concern is a **transparency** problem, addressed by one-click UI disclosure
("Revising this imported scene prepares a chapter contract from all N imported scenes"), never a second modal.
Any future adoption budget belongs uniformly in the worker claim/execution layer, not a per-caller guard.
"Unconditional" still means: the chapter qualifies for the mode; exact-replay mints no duplicate;
`awaiting_start` is promoted; `queued`/`running` is reused/joined; a contracted chapter starts no initial
adoption; pause blocks claim/drain, not durable queuing.

### D7 — Reconciliation entry (boot)

Scan predicate:

```
Scene.status == revision_requested
AND no active RevisionRequest for the scene
AND the LATEST Approval OVERALL for this scene row (ORDER BY created_at DESC, id DESC) has
      decision == REVISE AND scene_id == scene.id AND version == scene.version
```

Query latest-**overall**, then test that it is REVISE — never "latest REVISE" (which can skip a later
APPROVE/DENY and resurrect replaced intent). Fail closed on **identity drift**, not elapsed time: an old stuck
scene reconciles iff it is still the same current row + version.

Legacy `Approval` carries **no prose hash** (it records scene/version/decision/pass/feedback; only
`RevisionRequest` carries `target_prose_hash`). Reconciliation therefore:

- pins the **current** prose hash onto the reconstructed request;
- sets `origin = legacy_reconciliation`, preserves the source `approval_id`;
- records that intent was re-anchored from **version-level** provenance, not a validated historical snapshot.

It mints the adoption via the seam with `deferred_consent` + `request_bound` (`awaiting_start` — not
worker-claimable; an unpaused queue is not consent for historical spend). Runs in the lifespan seam,
**never** in `apply_lightweight_migrations` (which also runs in the test fixture).

### D8 — The integrity hold (no valid current-row REVISE)

The **canonical hold state is derived**, never owned by a row:

```
active hold  ⇔  Scene.status == revision_requested  AND  no active RevisionRequest
                AND  no valid current-row REVISE Approval
```

The operator-visible surface is an `Activity` **projection** of that condition (via `record_activity`, **not**
`safe_record_activity` — a swallowed failure would let the pass report success without its required
diagnostic):

```
kind = integrity_hold · source = reconciliation
payload.hold_code   = legacy_revision_intent_missing
payload.reason_code = missing_approval | latest_decision_not_revise | scene_version_mismatch
payload.scene_id · payload.prose_hash
payload.dedup_key   = deterministic(hold_code, scene_id, prose_hash)
```

The existence-check and event insert happen in the **same chapter-locked transaction**, deduped by
`(hold_code, scene_id, current_prose_hash)` → one hold per unresolved prose snapshot. A hand-edit changes the
hash → a new diagnostic state; the old event remains history. No dedicated hold table. Activity retention may
later delete the event while the underlying inconsistency persists; **re-emission on a future boot is
acceptable and truthful** — this ADR promises no permanent event retention.

### D9 — Reverse cancellation (request-bound only, under the lock)

The live trigger is the explicit request-cancel path (`revision.py:387`); `SUPERSEDED` always installs a
replacement, and by `COMPLETED`/`FAILED` (`worker.py:127/166`) the adoption is already `contract_proposed`.
The whole cancel transaction acquires the chapter lock, then:

```
if chapter has 0 active RevisionRequests
   and adoption.liveness_basis == request_bound
   and adoption.status in {awaiting_start, queued}:
       adoption.status = cancelled
```

Never cancel an `operator_independent`, `running`, or terminal adoption. Without the persisted liveness axis
this is unsafe — it would either strand request-orphan spend or cancel an adoption an operator started for its
own sake.

### D10 — Running adoptions finish

A claimed/extracting adoption is **never** interrupted mid-model-call (mirrors ADR-0028:53's running-Job
rule). `running → contract_proposed`; no request is resumed; the proposal stays reviewable/reusable; later
work may reuse or supersede it. Observed via an `adoption_completed_without_active_requests` flag — a flag, not
a new status. No cleanup lifecycle is defined here (deferred).

### D11 — Command-response contract

The durable GET resource `RevisionRequestOut` is **unchanged** — invocation-specific facts cannot be
reconstructed on a later GET and do not belong on it. The revise action returns a new envelope:

```
RevisionAcceptanceOut
  request: RevisionRequestOut
  request_disposition: created | replaced | replayed
  forward_effect:      none | revision_job_queued | adoption_created | adoption_promoted | adoption_joined
```

`adoption_joined` = a second scene's request attaches to already-`queued`/`running` adoption work without
creating or promoting it. HTTP:

```
200  iff  request_disposition == replayed  AND  forward_effect == none
202  for every other accepted result
```

Both HTTP surfaces (`reviews.py:148`, `:262`) adopt the typed contract in the same wave; OpenAPI + the FE
client are regenerated (`codegen:check`). No compatibility shim.

### D12 — Observability (one entry vocabulary)

A revision-triggered auto-start may itself be a promotion, so auto-start / reconciliation / promotion are
**not** separate kinds. One event:

```
adoption_entry_transition
  action: created | promoted
  trigger: revision | operator_start | reconciliation | reauthor
  consent: spend | deferred
  from_status · to_status · liveness_basis · adoption_id · request_id?
```

Plus `adoption_reverse_cancelled`, `adoption_completed_without_active_requests`, and the `integrity_hold`
event (D8). **Nothing is emitted for a completely inert reuse** — the command response already reports it.

### D13 — Migration & rollout (waves; each internally safe + independently deployable)

The lightweight migration system runs column-adds, backfills, custom reconciliation, and indexes within the
boot provisioner's DB transaction; the duplicate preflight is a dedicated migration function invoked **before**
the unique-index DDL.

```
W0  Guarded schema.
      add import_adoptions.liveness_basis with a TEMPORARY db default 'operator_independent'; backfill existing rows.
      duplicate preflight:  SELECT chapter_id, count(*) FROM import_adoptions
                            WHERE status IN ('awaiting_start','queued','running')
                            GROUP BY chapter_id HAVING count(*) > 1;
        nonzero → FAIL CLOSED + operator report (chapter_id + each conflicting adoption's id/status/basis/timestamps);
        delete nothing, pick no winner.
      then CREATE UNIQUE INDEX uq_active_import_adoption_per_chapter (partial, active states).

W1  Extract the canonical seam; route existing Start + Re-author through it; add the AST constructor guard;
      assign/upgrade liveness_basis explicitly; THEN drop the temp default, make the column NOT NULL, enforce
      the two permitted values. (Prevents an older revision minting null-basis rows between W0 and W1.)

W2  Chapter-lock the request-cancellation transaction; add request-bound reverse cancellation; preserve
      running and operator-independent work. (Must precede any minter of request-bound work.)

W3  Sync auto-start; consent-on-replay; the typed RevisionAcceptanceOut on both HTTP surfaces; OpenAPI + FE regen.

W4  Boot reconciliation inserted AHEAD of the drain-resume block (currently main.py:67; drains kick at :102-106
      before the integrity probe at :110). Current-row Approval reconstruction; snapshot-keyed integrity events.
```

Backfilling existing rows to `operator_independent` is a **conservative compatibility choice**, not a claim
that every deployed row was operator-created — legacy rows lack trustworthy liveness provenance, and
auto-cancelling them would be unsafe. **Rollback:** drop the partial-unique index (accept the weaker
lock+read invariant, a known regression); keep the additive column (old code ignores it); no destructive
column rollback.

### D14 — Invalid states & acceptance

| Invalid state | Guard / proof surface |
|---|---|
| Two active adoptions / chapter | partial-unique index rejects the 2nd (DB-level test) |
| Queued adoption, zero active requests, **`basis=request_bound`** | reverse-cancel (D9); valid when `operator_independent` |
| `awaiting_start→queued` without spend consent | consent input explicit; only Start/Re-author/fresh-or-replayed Revise supply it |
| Imported/uncontracted RevisionRequest committed without its adoption entry | one atomic transaction (D4); injected mid-sequence failure rolls back both |
| Adoption minted outside the chapter lock | single writer + caller tests + lock assertions (index prevents duplication only) |
| Late worker publish over an invalidated fingerprint | existing publish revalidation (`import_adoption.py:485`) |
| Reconciliation selects an old REVISE while ignoring a later decision | select latest Approval overall, then test decision (D7) |
| Last request cancels an operator-started adoption | `liveness_basis` prevents it (D9) |

Additional acceptance obligations:
- Two active requests in one chapter share **one** adoption and both link to it.
- Two concurrent reconciliation scanners produce **one** reconstructed request/adoption and **at most one**
  hold event for the same snapshot.
- Dirty migration data refuses index creation with the conflicting identities visible.
- Re-author encountering an unclaimed `awaiting_start` adoption promotes that row **in place** to `queued`,
  sets the force token + lineage, upgrades basis to `operator_independent`; never mints a second active row.
- The AST constructor guard permits only the ORM declaration + the seam.
- A boot-ordering test proves reconciliation commits **before** the repair/draft drains are kicked.

## Alternatives considered

- **Two peer slices (auto-start, reconciliation) with independent writers** — rejected: two writers of
  `awaiting_start`/`queued` is the shadow lifecycle this ADR exists to prevent.
- **Lock + read-check as the sole uniqueness guard** — rejected: only as strong as every future caller's lock
  discipline; the partial-unique index is the durable structural backstop.
- **`RevisionRequest` owns the adoption (child relation)** — rejected: adoption is chapter-shared; one adoption
  serves every active request. The link is soft.
- **Adoption row construction inside `revision.py`** — rejected: it re-creates a second writer; the seam is the
  only constructor.
- **`request_disposition`/`forward_effect` on `RevisionRequestOut`** — rejected: those are invocation facts, not
  durable resource state; they live on the command envelope.
- **A dedicated reconciliation-hold table** — rejected: the hold is a derived condition; `Activity` is its
  projection, and append-only semantics give F4b's "reevaluate from scratch" for free.
- **A boolean `replayed` discriminator** — rejected: it collapses two independent facts (request disposition,
  forward effect) into one.

## Consequences

The imported prologue that motivated ADR-0028 becomes recoverable: a fresh Revise auto-starts adoption
(spend-consented, request-bound); a boot after a stranding redeploy reconciles legacy `revision_requested`
scenes into durable, operator-authorizable intent without fabricating consent or spending unbidden. Exactly one
writer mints the active lifecycle, backed by a database invariant; reverse cancellation retires request-orphan
spend without touching operator-owned or running work; and callers learn — truthfully, per invocation —
whether their action created, replaced, replayed, or joined, and whether it moved anything forward. Amendment
mode (#261) builds on this lifecycle but additionally requires chapter-tier ChapterPacket lock coverage (#259);
this ADR deliberately stops short of both.

**Revisit triggers:** a 5th adoption minter appears; adoption cost becomes a real problem (→ a uniform
worker-layer budget); `Approval` gains a prose hash (→ tighten D7 to true snapshot provenance).
