# ADR-0032: Unified Adoption-Entry Lifecycle

**Status:** accepted · **implementation_authorized:** true · **Decision owner:** mark
**Charted as:** wayfinder map #258, ticket #260 (`/expanded-grill-with-docs`).

**Revision history**
- **v2.2 (2026-07-24) — W2/W3 rollout re-split (build-time, honest amendment).** Code-level grilling of W2
  found both Revise routes mutate/read outside the chapter-lock boundary, so the forward coordinator cannot be
  integrated before its full route cutover. W2 now wires only the LIVE reverse path (the chapter-locked
  scene-approval command + `reconcile_adoption_demand_locked`); `accept_revision_intent` and
  `_accept_revision_request_locked` move to W3 WITH the forward cutover. Rollout-sequencing change only — no
  F1/F2/F3, liveness, or ownership decision reopened. See the D13 W2/W3 lines and the v2.2 boundary note.
- **v2.1 (2026-07-24) — recorded tightened `liveness_basis` semantics.** Retention-authority definition, the
  monotonic merge table, collision-reread merging both axes, and fail-closed reverse-cancel on an ambiguous
  demand count. Documentation fidelity to the owner's stated design; no structural change to D2/D9.
- **v2 (2026-07-24) — F2 re-ruled A′ → C.** The atomic-coupling decision was corrected: a false A/B binary
  conflated *orchestration* with *state ownership*. The command boundary (`accept_revision_intent`) now owns
  only lock/transaction/ordering; the revision module remains the sole `RevisionRequest` writer and the
  adoption module the sole `ImportAdoption` writer. F1 strengthened to B+ (invariant-collision telemetry;
  explicit intent enum), F3 qualified with an eligibility envelope. Round-2 `liveness_basis` is preserved as
  the guard inside the adoption-owned demand reconciler.
- v1 (2026-07-23) — original F1=B / F2=A′ / F3=A contract.

**Refines and supersedes ADR-0028's _tail_ decisions** for: automatic adoption entry on revise, legacy
reconciliation, entry intent & liveness, active-adoption uniqueness, reverse cancellation, and command-response
semantics. It does **not** supersede ADR-0028's **landed** architecture — evidence extraction, the leased
adoption worker, publish/compare-and-set, derive-binding, and request resume all stand. References ADR-0029
(claim-source precedence) unchanged. Out of scope: amendment mode (#261), chapter-tier ChapterPacket lock
coverage (#259), the ADR-0031 authorization axis, autonomy, cost ceilings, fidelity scoring.

## Decision

Auto-start-on-revise and legacy reconciliation are **two entry paths into one canonical adoption-entry
lifecycle**, not two peer slices. Three principles:

1. **One writer of adoption state.** Exactly one adoption-owned primitive mutates the active adoption states
   (`awaiting_start`/`queued`/`running`); all four callers supply *intent* and route through it. The invariant
   "≤1 active adoption per chapter" is a **database structural guarantee**, not merely a lock convention.
2. **Single ownership, coordinated atomically.** The revision module remains the sole `RevisionRequest` writer;
   the adoption module remains the sole `ImportAdoption` writer. A command coordinator above both owns the
   chapter lock, the transaction boundary, and operation ordering — and nothing else. No module owns another
   module's state; no endpoint independently sequences the two mutations.
3. **The Revise action is spend intent, within an eligibility envelope.** A fresh explicit Revise supplies
   spend consent — no second confirmation, no revise-specific cost ceiling — but only *queues, promotes, or
   reuses an eligible adoption*; ineligible chapter states fail closed.

## Context (verified at HEAD)

Slice 3b shipped **explicit operator Start only**. The consequences the unification must resolve:

- The mint/promote logic lives **inline** in `start_contract_adoption._body()` (`api/routers/adoption.py:137-179`).
  Replicating it in the revise seam or a boot reconciler would create multiple lifecycle writers.
- `accept_revision_request` (`workers/revision.py:138`) is the **sole `RevisionRequest` constructor**
  (`revision.py:229`, and its own docstring `revision.py:3`). It lands the request at `awaiting_contract` and
  does **not** touch adoption. It is the request writer — **not** a chapter aggregate root.
- **Four** callers construct or mint adoptions, not three: operator **Start** (`adoption.py:170`), operator
  **Re-author** (`adoption.py:261`, force-token + `reauthor_of_adoption_id` lineage), and — after this ADR —
  the **sync** revise entry and boot **reconciliation**.
- The "≤1 active adoption per chapter" invariant is enforced **only** by a read-check under the chapter lock
  (`adoption.py:159`); the sole unique index is on `force_author_token` (`shared/migrations.py:320`). The
  chapter lock is a coordination protocol — a caller bypassing it bypasses the guarantee (`shared/chapter_lock.py`).
- No reconciliation writer exists; `awaiting_start` is read (promoted by Start, `adoption.py:163`) but never
  written. The two direct HTTP revise surfaces (`api/routers/reviews.py:148`, `:262`) return ad-hoc dicts and
  derive `200/202` solely from `AcceptResult.replayed`.
- `ImportAdoptionStatus.CANCELLED` is documented as "an `awaiting_start`/`queued` adoption with no remaining
  active requests" (`shared/enums.py:399`) — a reverse rule nothing enforces.

## Design

### D1 — The one adoption writer (F1 = B+): callers supply intent, not transitions

The entire decision-and-mutation block currently inline in the Start endpoint — evidence-only eligibility,
active-adoption lookup, `awaiting_start→queued` promotion, new-`queued` construction, source-fingerprint
capture — moves into one **adoption-owned** primitive. Callers supply *intent*; they never implement lifecycle
transitions.

```
ensure_import_adoption_locked(session, *, chapter_id, entry_intent, mode,
                              force_author_token=None, reauthor_of=None) -> (adoption, entry_effect)
    # adoption-owned; assumes run_under_chapter_workflow is held; NEVER commits.
ensure_import_adoption(session, ...same...)   # standalone wrapper; acquires run_under_chapter_workflow
```

`entry_intent` is an enum (never a boolean — amendment mode and future entry policies would make a boolean
unreadable):

| Existing state | `SPEND` | `RECORD_WITHOUT_SPEND` |
|---|---|---|
| none | create `queued` | create `awaiting_start` |
| `awaiting_start` | promote to `queued` | return unchanged |
| `queued` | return unchanged | return unchanged |
| `running` | return unchanged | return unchanged |
| terminal only | create per intent, when otherwise eligible | same |

Callers:

| caller | seam entry | entry_intent | liveness_basis |
|---|---|---|---|
| sync revise | the **locked primitive**, inside the coordinator's txn (D4) | `SPEND` | `request_bound` |
| operator Start | the **wrapper** | `SPEND` | `operator_independent` |
| operator Re-author | the **wrapper** (force-token + lineage) | `SPEND` | `operator_independent` |
| boot reconciliation | the **wrapper** | `RECORD_WITHOUT_SPEND` | `request_bound` |

An **AST-aware production-source test** asserts no `ImportAdoption(...)` constructor exists under `src/dominion`
except the ORM class declaration and the one inside the primitive.

### D2 — Entry intent and liveness are orthogonal axes

`entry_intent` decides the **initial status**; `liveness_basis` (`request_bound | operator_independent`,
persisted on the adoption) is **current retention authority**, not historical creation provenance — it decides
**survival** when no request remains:

- `request_bound` — active only while at least one qualifying `RevisionRequest` requires its output.
- `operator_independent` — an explicit operator command established chapter-contract reconstruction as
  independently desired work; the operator command is itself durable demand.

An adoption may **begin** `request_bound` (sync revise / reconciliation) and later be **upgraded** by operator
Start — its creator has not changed, but its current reason for survival has. Merge is **monotonic**; no path
downgrades `operator_independent`:

```
request_bound        + request_bound        → request_bound
request_bound        + operator_independent → operator_independent
operator_independent + anything             → operator_independent
```

Operator Start/Re-author touching an existing `request_bound` adoption — even one already `queued` —
promotes/keeps it `queued`, sets operator lineage, and upgrades its basis. It never mints a second active row.

### D3 — Active-adoption uniqueness is a structural invariant

```sql
CREATE UNIQUE INDEX uq_import_adoptions_active_chapter
  ON import_adoptions (chapter_id)
  WHERE status IN ('awaiting_start', 'queued', 'running');
```

Terminal states (`contract_proposed`, `failed`, `invalidated`, `cancelled`) never permanently block a later
valid adoption. On a constraint collision the primitive:

1. rolls back **only** the insertion savepoint;
2. reloads the winning active row;
3. **merges both dimensions** — promotes status (`awaiting_start` + `SPEND` → `queued`) *and* upgrades liveness
   (`request_bound` + operator-independent intent → `operator_independent`), only when compatible; **neither
   status nor liveness ever regresses** — never a bare "return the winning row";
4. **emits high-severity invariant telemetry** — a correct canonical caller under the lock should almost never
   reach this race, so a collision signals an architectural bypass, not ordinary concurrency;
5. **fails closed** on incompatible state.

The index is duplication-prevention only; it does not make lock-bypass structurally impossible, and the
canonical path still serializes under the chapter lock. The index protects the invariant from future caller
drift, maintenance scripts, reconciliation mistakes, and deployment-version overlap — none of which the
advisory lock can guarantee against. **What would change D1/D3:** proof that *every* possible DB writer
(reconciliation, maintenance commands, deployment overlap, future worker paths) is structurally incapable of
bypassing the advisory lock. The current architecture does not provide that proof.

### D4 — Atomic command coordination with separate canonical owners (F2 = C)

The offered A/B binary was false. **A** (adoption construction/cancellation *inside* `accept_revision_request`)
makes the revision module a second owner of `ImportAdoption`. **B** (each endpoint calls request-accept then
separately starts adoption) repeats orchestration and drifts across revise surfaces. The correct shape is a
**shared command boundary above both lifecycle owners**:

```
accept_revision_intent(...)                 # public clean-session command
    owns run_under_chapter_workflow + commit/rollback + ordering
    ├── _accept_revision_request_locked(...) # revision-owned; assumes lock; never commits
    └── ensure_import_adoption_locked(...)    # adoption-owned; assumes lock; never commits
```

Ownership stays clean:

- **Revision lifecycle owner:** `RevisionRequest` classification, persistence, replacement, replay, request
  status, request↔Job linkage.
- **Adoption lifecycle owner:** `ImportAdoption` eligibility, creation, promotion, cancellation, the
  active-adoption invariant, source fingerprint.
- **Command coordinator:** chapter lock, transaction boundary, operation ordering, atomic success-or-rollback
  across both owners — nothing else.

All revise surfaces (`reviews.py:148`, `:262`) call `accept_revision_intent`; none individually sequences the
two mutations. There is **no `lock_already_held` flag** — the coordinator owns the lock; the owners' `_locked`
bodies assume it. Adoption remains **chapter-shared** (one adoption serves every active request in its chapter);
`RevisionRequest.import_adoption_id` is a serving/provenance link, not a request-private adoption.

Canonical transaction order:

```
acquire chapter workflow lock
reload mutable chapter/scene state
classify revision request
handle conflict / replacement / replay
persist Approval + RevisionRequest when needed
attempt contract-backed revision-Job mint
if request remains awaiting_contract:
    ensure_import_adoption_locked(entry_intent=SPEND, liveness_basis=request_bound)
    link request.import_adoption_id
commit everything atomically
```

**What would change D4:** evidence that `accept_revision_request` is already the accepted aggregate root for
all chapter workflow state, not merely the sole `RevisionRequest` writer. Current models and comments support
separate request and chapter-shared adoption ownership.

### D5 — Exact-replay reconciles adoption entry (side-effect correction)

An exact request replay returns before mutation today (`revision.py:206-208`). Under the coordinator, request
replay and adoption-entry replay are distinct questions: a repeated explicit Revise still reconciles adoption
entry via the seam —

- replay + no adoption → **create** `queued`;
- replay + `awaiting_start` → **promote** to `queued`;
- replay + `queued`/`running` → **inert**.

Otherwise a reconciliation-restored request + `awaiting_start` adoption would leave a fresh explicit Revise
click stuck behind operator Start merely because the request itself replayed.

### D6 — The Revise action is spend intent, within an eligibility envelope (F3 = A)

A fresh explicit Revise supplies spend consent: **no second confirmation, no revise-specific cost ceiling.**
Recorded precisely:

> A fresh Revise action queues or reuses adoption automatically **whenever the scene and chapter are eligible
> for the required adoption mode** — not "always create an initial adoption regardless of chapter state."

Eligibility envelope:

- the imported scene lacks the required approved scene contract;
- no incompatible adoption is active;
- **initial** mode only for a genuinely evidence-only chapter;
- an existing `awaiting_start` adoption is **promoted**, not duplicated;
- an existing `queued`/`running` adoption is **reused/joined**;
- a mixed or already-approved chapter requiring **amendment fails closed** until amendment mode exists (#261);
- pause controls worker **draining**, not durable intent creation.

The 30-scene surprise-cost concern is a **transparency** problem, addressed by one-click pre-action disclosure
("Revising this imported scene prepares a chapter contract from all N imported scenes"), visible before the
click, never a second modal. Any hard budget belongs uniformly at the adoption **claim/execution** boundary,
not embedded in one entry caller (which would give Start, sync Revise, and reconciliation different budget
semantics). **What would change D6:** evidence that users understand Revise as authorizing only a single-scene
operation with materially different, undisclosed billing/latency/data-use — in which case the *action* is
renamed/redesigned, not patched with a surprise confirmation.

### D7 — Reconciliation entry (boot)

Scan predicate:

```
Scene.status == revision_requested
AND no active RevisionRequest for the scene
AND the LATEST Approval OVERALL for this scene row (ORDER BY created_at DESC, id DESC) has
      decision == REVISE AND scene_id == scene.id AND version == scene.version
```

Query latest-**overall**, then test that it is REVISE — never "latest REVISE" (which can skip a later
APPROVE/DENY and resurrect replaced intent). Fail closed on **identity drift**, not elapsed time.

Legacy `Approval` carries **no prose hash** (only `RevisionRequest` carries `target_prose_hash`).
Reconciliation therefore pins the **current** prose hash onto the reconstructed request; sets
`origin = legacy_reconciliation`, preserves the source `approval_id`; and records that intent was re-anchored
from **version-level** provenance, not a validated historical snapshot. It mints via the seam with
`RECORD_WITHOUT_SPEND` + `request_bound` (`awaiting_start`, not worker-claimable — an unpaused queue is not
consent for historical spend). Runs in the lifespan seam, **never** in `apply_lightweight_migrations` (which
also runs the test fixture).

### D8 — The integrity hold (no valid current-row REVISE)

The **canonical hold state is derived**, never owned by a row:

```
active hold  ⇔  Scene.status == revision_requested  AND  no active RevisionRequest
                AND  no valid current-row REVISE Approval
```

The operator surface is an `Activity` **projection** (via `record_activity`, **not** the failure-swallowing
`safe_record_activity`):

```
kind = integrity_hold · source = reconciliation
payload.hold_code   = legacy_revision_intent_missing
payload.reason_code = missing_approval | latest_decision_not_revise | scene_version_mismatch
payload.scene_id · payload.prose_hash · payload.dedup_key = deterministic(hold_code, scene_id, prose_hash)
```

Existence-check and insert in the **same chapter-locked transaction**, deduped by
`(hold_code, scene_id, current_prose_hash)` → one hold per unresolved prose snapshot; a hand-edit changes the
hash → a new diagnostic state, old event remains history. No dedicated hold table. Retention may later delete
the event while the inconsistency persists; re-emission on a future boot is acceptable and truthful — this ADR
promises no permanent event retention.

### D9 — Reverse cancellation is adoption-owned; invoked on demand removal

Reverse cancellation belongs to the **adoption owner**, not `accept_revision_request`:

```
reconcile_adoption_demand_locked(session, chapter_id)   # adoption-owned; assumes chapter lock
    cancel  ⇔  adoption.liveness_basis == request_bound          # round-2 guard, preserved
           AND  adoption.status in {awaiting_start, queued}
           AND  qualifying active-request count == 0
    # the active-request count must resolve unambiguously; an ambiguous or failed count
    # FAILS CLOSED (leave the adoption alone) — never infer "no demand" from a bad read.
```

Invoked by any request-lifecycle mutation that can **remove demand** — principally the explicit request-cancel
path (`revision.py:387`), whose whole authority-changing transaction acquires the chapter lock. `SUPERSEDED`
always installs a replacement request; by `COMPLETED`/`FAILED` (`worker.py:127/166`) the adoption is already
`contract_proposed`. **The `liveness_basis` guard is load-bearing:** without it this would cancel an
operator-started (`operator_independent`) adoption that legitimately has no request. Never cancel
`operator_independent`, `running`, or terminal adoptions — nor a row whose active-request count is ambiguous or
failed (fail closed; request code reports changed demand, adoption code decides the consequence). `running`
cancellation would require a cooperative cancellation policy — deferred (D10).

### D10 — Running adoptions finish

A claimed/extracting adoption is **never** interrupted mid-model-call (mirrors ADR-0028:53's running-Job rule).
`running → contract_proposed`; no request resumed; the proposal stays reviewable/reusable; later work may reuse
or supersede it. Observed via `adoption_completed_without_active_requests` — a flag, not a new status. No
cleanup lifecycle is defined here (deferred).

### D11 — Command-response contract

The durable GET resource `RevisionRequestOut` is **unchanged** — invocation facts can't be reconstructed on a
later GET. `accept_revision_intent` returns a new envelope:

```
RevisionAcceptanceOut
  request: RevisionRequestOut
  request_disposition: created | replaced | replayed
  forward_effect:      none | revision_job_queued | adoption_created | adoption_promoted | adoption_joined
```

`adoption_joined` = a second scene's request attaches to already-`queued`/`running` adoption without creating
or promoting it. HTTP:

```
200  iff  request_disposition == replayed  AND  forward_effect == none
202  for every other accepted result
```

Both HTTP surfaces adopt the typed contract in the same wave; OpenAPI + FE client regenerated
(`codegen:check`). No compatibility shim.

### D12 — Observability (one entry vocabulary)

A revision-triggered auto-start may itself be a promotion, so auto-start / reconciliation / promotion are not
separate kinds. One event:

```
adoption_entry_transition
  action: created | promoted
  trigger: revision | operator_start | reconciliation | reauthor
  entry_intent: spend | record_without_spend
  from_status · to_status · liveness_basis · adoption_id · request_id?
```

Plus `adoption_reverse_cancelled`, `adoption_completed_without_active_requests`, and the `integrity_hold`
event (D8). **Nothing** is emitted for a completely inert reuse — the command response already reports it.

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
      then CREATE UNIQUE INDEX uq_import_adoptions_active_chapter (partial, active states).

W1  Extract the adoption-owned seam; route existing Start + Re-author through it; add the AST constructor guard;
      assign/upgrade liveness_basis explicitly; THEN drop the temp default, make the column NOT NULL, enforce
      the two permitted values. (Prevents an older revision minting null-basis rows between W0 and W1.)

W2  Wire the demand-removal safety path before any request-bound minter: introduce the chapter-locked
      scene-approval command over _cancel_active_requests_for_scene_locked and the adoption-owned
      reconcile_adoption_demand_locked; preserve running, operator-independent, and terminal adoptions;
      fail closed on an indeterminate demand read.

W3  Introduce accept_revision_intent over _accept_revision_request_locked; route both Revise surfaces
      through the coordinator; add sync request-bound adoption entry, consent-on-replay,
      RevisionAcceptanceOut, and OpenAPI/frontend regeneration.

W4  Boot reconciliation inserted AHEAD of the drain-resume block (currently main.py:67; drains kick at :102-106
      before the integrity probe at :110). Current-row Approval reconstruction; snapshot-keyed integrity events.
```

**W2/W3 boundary (v2.2 amendment).** The original W2 wording placed the forward coordinator before its route
cutover. Code-level grilling established that both Revise routes currently perform mutable reads/writes outside
the chapter-lock boundary, so the coordinator cannot be integrated safely until the complete forward command
cutover. W2 remains independently deployable by wiring the live reverse path; W3 performs the forward
transaction migration once, without inert infrastructure or a transitional unlocked "_locked" body. No F1/F2/F3,
liveness, or ownership decision changes — only rollout sequencing, corrected on verified integration evidence.

Backfilling existing rows to `operator_independent` is a **conservative compatibility choice**, not a claim
that every deployed row was operator-created — legacy rows lack trustworthy liveness provenance, and
auto-cancelling them would be unsafe. **Rollback:** drop the partial-unique index (accept the weaker lock+read
invariant, a known regression); keep the additive column; no destructive column rollback.

### D14 — Invalid states & acceptance

| Invalid state | Guard / proof surface |
|---|---|
| Two active adoptions / chapter | partial-unique index rejects the 2nd (DB-level test) |
| Queued adoption, zero active requests, **`basis=request_bound`** | `reconcile_adoption_demand_locked` (D9); valid when `operator_independent` |
| `awaiting_start→queued` without SPEND intent | `entry_intent` explicit; only Start/Re-author/fresh-or-replayed Revise supply SPEND |
| Imported/uncontracted RevisionRequest committed without its adoption entry | one atomic coordinator transaction (D4); injected mid-sequence failure rolls back both |
| Revision module writes `ImportAdoption`, or adoption module writes `RevisionRequest` | single ownership (D4) — AST guard covers both directions; no endpoint sequences both but the coordinator |
| Adoption minted outside the chapter lock | single writer + caller tests + lock assertions + high-severity collision telemetry (index prevents duplication only) |
| Late worker publish over an invalidated fingerprint | existing publish revalidation (`import_adoption.py:485`) |
| Reconciliation selects an old REVISE while ignoring a later decision | select latest Approval overall, then test decision (D7) |
| Last request cancels an operator-started adoption | `liveness_basis` prevents it (D9) |

Additional acceptance obligations: two active requests in one chapter share **one** adoption and both link to
it; two concurrent reconciliation scanners produce **one** reconstructed request/adoption and **at most one**
hold event per snapshot; dirty migration data refuses index creation with conflicting identities visible;
Re-author encountering an unclaimed `awaiting_start` adoption promotes it **in place** to `queued`, sets
force-token + lineage, upgrades basis to `operator_independent`, never mints a second active row; the AST guard
permits only the ORM declaration + the seam; a boot-ordering test proves reconciliation commits **before** the
repair/draft drains are kicked.

## Alternatives considered

- **Two peer slices with independent writers** — rejected: two writers of `awaiting_start`/`queued` is the
  shadow lifecycle this ADR exists to prevent.
- **F2 as an A/B binary** — rejected as a false binary. **A** (adoption mutation inside `accept_revision_request`)
  makes the revision module a second `ImportAdoption` owner; **B** (per-endpoint orchestration) drifts across
  revise surfaces. **C** — a coordinator above two single-owner writers — preserves single ownership *and*
  atomicity.
- **Lock + read-check as the sole uniqueness guard** — rejected: only as strong as every future caller's lock
  discipline; the partial-unique index is the durable structural backstop.
- **`RevisionRequest` owns the adoption (child relation)** — rejected: adoption is chapter-shared; the link is
  soft (serving/provenance), not ownership.
- **`entry_intent` as a boolean** — rejected: unreadable once amendment mode or another entry policy is added.
- **`request_disposition`/`forward_effect` on `RevisionRequestOut`** — rejected: invocation facts, not durable
  resource state; they live on the command envelope.
- **A dedicated reconciliation-hold table** — rejected: the hold is a derived condition; `Activity` is its
  append-only projection.
- **A boolean `replayed` discriminator** — rejected: it collapses two independent facts (disposition, effect).

## Consequences

The imported prologue that motivated ADR-0028 becomes recoverable: a fresh eligible Revise auto-starts adoption
(spend intent, request-bound); a boot after a stranding redeploy reconciles legacy `revision_requested` scenes
into durable, operator-authorizable intent without fabricating consent or spending unbidden. Exactly one writer
mints the active lifecycle, backed by a database invariant; a command coordinator provides atomicity **without**
collapsing revision and adoption ownership; reverse cancellation retires request-orphan spend without touching
operator-owned or running work; and callers learn — truthfully, per invocation — whether their action created,
replaced, replayed, or joined, and whether it moved anything forward. Amendment mode (#261) builds on this
lifecycle but additionally requires chapter-tier ChapterPacket lock coverage (#259); this ADR deliberately stops
short of both.

**Revisit triggers:** a 5th adoption minter appears; adoption cost becomes a real problem (→ a uniform
worker-layer budget at the claim/execution boundary); `Approval` gains a prose hash (→ tighten D7 to true
snapshot provenance); proof emerges that no DB writer can bypass the advisory lock (→ the partial-unique index
becomes optional, D3); operator Start is redefined from "prepare this chapter's contract" to "authorize spend
for an existing revision request" (→ Start no longer implies `operator_independent`, D2).
