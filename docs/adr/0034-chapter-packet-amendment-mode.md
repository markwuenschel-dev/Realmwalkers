# ADR-0034: ChapterPacket amendment mode — copy-on-write supersession for the no-seed case

**Status:** accepted · **implementation_authorized:** true · **Decision owner:** mark
**Charts:** issue #261, under map #258.
**Implements** ADR-0028 §38's `amendment` adoption mode, which every prior record deferred
(ADR-0032 v2.4, and its Consequences: *"Amendment mode (#261) builds on this lifecycle but additionally
requires chapter-tier ChapterPacket lock coverage (#259); this ADR deliberately stops short of both."*).
Builds on ADR-0032's adoption-entry seam (D1) and on #259's chapter-lock coverage, both landed.
References ADR-0033 D5b (scene-tier approval provenance) as the pattern this deliberately diverges from
at the chapter tier (D6).

**Revision history**
- v1 (2026-07-30) — first record, authored against the implementation as it stood at the final
  verification pass. Records the eight decisions the build made, the two invariants that became DATABASE
  guarantees rather than conventions, and the reason eligibility is keyed on seed **presence** rather than
  on `ScenePacket.status == STALE`. **Three divergences between the code and the design as described are
  recorded inline** rather than smoothed over: D4's shared transition is documented in two docstrings but
  the ordinary approve route still writes `status` itself; D7's model-layer comment claims self-referential
  FKs the migration deliberately does not create; and the rollout stops two waves short of a completable
  operator flow (build-state note below).

> **Build state at v1 — read this before relying on the record.** The decisions below are settled and the
> **domain layer, the schema, and the operator surfaces are live**: eligibility
> (`workers/packet/amendment.py:185-296`), the one locked authority transition (`:363-484`), the entry
> policy (`shared/adoption_entry.py:177-183`), all four CHECKs and both partial unique indexes
> (`shared/migrations.py:326-398`), and three routes — the advisory preflight
> (`api/routers/adoption.py:180-209`), amendment start (`:212-268`, supplying
> `AdoptionOperation.AMENDMENT`), and the approve+supersede transition
> (`api/routers/packets.py:339-433`), with the ordinary approve route now refusing a proposed amendment
> (`packets.py:277-308`).
>
> **Two pieces are NOT built, and one of them is reachable.** (a) The **copy-on-write authoring pass does
> not exist**: `workers/import_adoption.py:515-520` still fails any `mode=amendment` claim closed with the
> Slice-3b `AmendmentModeUnsupported` (`:96-104`). `POST .../amendment/start` therefore mints an adoption
> the worker will immediately fail — the entry path is live in front of a pipeline that refuses itself.
> (b) The boot **authority sweep is named but undefined**: `workers/boot_reconciliation.py:39` says *"See
> `reconcile_chapter_packet_authority` for the five states and their predicates"*, and a grep across `src`
> and `tests` finds no such definition; the five `IntegrityHoldReason` members it would write
> (`shared/enums.py`, `MULTIPLE_APPROVED_CHAPTER_PACKETS` … `CHAPTER_AUTHORITY_VACATED`) have no writer.
> D7 and D8 mark these `[OPEN]`. Nothing here is a claim that an author can complete an amendment today.
>
> **This record was written against a moving tree.** The #261 changes are uncommitted, and the operator
> routes landed between this ADR's first draft and its final verification pass. Every citation was
> re-opened at that final pass; anything asserted about the two unbuilt pieces above is true as of it and
> is the first thing to re-check.

## Decision (the settled core)

**An amendment is a new ChapterPacket row, copied on write from the chapter's approved packet, that takes
the single authority slot in the same transaction its predecessor vacates it.** Four things follow, and
each is a database guarantee rather than a lock convention:

| Guarantee | Object it bounds | Mechanism | Where |
|---|---|---|---|
| **One authority per chapter** | `status='approved'` rows | partial unique index | `migrations.py:326-328` |
| **One open branch per chapter** | `origin_mode='amendment' AND status='proposed'` rows | partial unique index | `migrations.py:333-335` |
| **No orphaned supersession** | `superseded` rows with no successor | CHECK | `migrations.py:362-369` |
| **No autonomous chapter approver** | `approval_source` vocabulary | CHECK omitting the value | `migrations.py:393-398` |

**Amendment is not a general "edit an approved contract" facility.** It is admitted for exactly one state
that normal re-derivation cannot repair: imported prose exists, the chapter has an APPROVED ChapterPacket,
and an affected scene has **no seed** in that packet's `body["scene_seeds"]`. A merely-stale seed is a
normal re-derive — the seed is still there, so `derive.py`'s per-seed loop still visits it. A valid seed
needs nothing at all.

**Supersede, never delete.** The predecessor becomes `superseded` and stays on disk as an immutable
historical record naming its successor. Nothing about an approved contract is destroyed to make room for
its replacement.

## Context (verified at HEAD `91c755a` plus the uncommitted #261 working tree; every citation opened this session)

The #261 changes are **uncommitted** at the time of writing — `git status --porcelain` shows
`src/dominion/workers/packet/amendment.py` and `tests/test_amendment_mode.py` as untracked, and six `src`
files plus two test files modified. Line numbers below are the working-tree lines.

1. **"One approved ChapterPacket per chapter" was an APPLICATION invariant only.** It was upheld solely by
   `workers/packet/__init__.py:_persist`'s delete-then-insert under the chapter lock (`:913-915`,
   `delete(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id)`), which its own docstring calls
   *"the ONLY production INSERT/replace of a ChapterPacket"* (`:855`). No schema object enforced it. The
   #261 preflight's own comment records that the violation is **reachable on a real database today**
   (`migrations.py:715-717`).
2. **A second approved packet would have been resolved arbitrarily.** `draft_readiness.py:514-523` selects
   the approved packet with `.limit(1)` and **no `ORDER BY`**, so `GET /draft/readiness` would silently
   pick one of two.
3. **`source_hash` is computed from DIFFERENT payloads at derive and at recompute.**
   `scene_packet/derive.py:576-584` passes `canon_chunk_hashes=canon_chunk_hashes` and
   `scene_pov=pov_override or None`; `scene_packet/staleness.py:111-117` passes **neither**. Both call the
   same `hash_mod.source_hash`. So any packet derived against populated canon compares unequal on the next
   recompute and is marked `STALE` with `stale_reason = "upstream inputs changed since derivation"`
   (`staleness.py:119-120`) whether or not anything actually drifted.
4. **The chapter fingerprint has exactly one membership query.** `chapter_scene_rows`
   (`shared/prose_fingerprint.py:21-45`) returns the chapter's non-superseded scenes as
   `(scene_no, scene_id, version, prose)`; `chapter_source_fingerprint` (`:53-65`) hashes them
   order-independently. `workers/import_adoption.py:132-142` is now a thin delegation to it, and
   `workers/packet/amendment.py:214,414` call it directly.
5. **`session.get` without `populate_existing` does not emit SQL.** The discipline is already stated twice
   in production code: `api/routers/reviews.py:295-300` (*"`populate_existing=True` is LOAD-BEARING, not
   defensive noise… a bare `session.get` would then return that PRE-LOCK instance with no SQL at all,
   silently defeating the reload"*), and `workers/production_repair.py:638-644` (*"`with_for_update` alone
   acquires the FOR UPDATE lock but does NOT repopulate an already-identity-mapped instance — the status
   guard below would read the STALE pre-load"*).
6. **`run_under_chapter_workflow` acquires the advisory lock before any row lock and owns the commit.**
   `shared/chapter_lock.py:118-133`: it acquires *first* — *"so the lock precedes EVERY row lock
   (`FOR UPDATE`) that `body` later takes"* — then awaits `body`, then commits; on any exception it rolls
   back. Its **CLEAN-TRANSACTION PRECONDITION** (`:127-132`) is that `body` must not commit or roll back.
7. **The delete-on-replace policy exists but is fenced off from approved material.** `_persist`'s
   `preserve_approved` branch (`workers/packet/__init__.py:904-912`) re-checks `latest_approved` **under
   the lock** and **returns the existing approved packet instead of replacing**.
   `ImportAdoption.chapter_packet_id` is `ON DELETE SET NULL` for exactly that replace path
   (`shared/models.py:494-503`,
   ADR-0028 Slice 3b Q11 tier-C: *"a re-author REPLACES the chapter's current packet, so the pipeline
   deletes the old one"*). The Re-author operation itself carries `refuses_approved_packet=True`
   (`shared/adoption_entry.py:146`), and the route turns that into
   `409 chapter_contract_already_approved` whose message reads *"Changing approved material is an
   **amendment/revision**, not a re-author — the force route will not overwrite it"*
   (`api/routers/adoption.py:143-153`).
8. **`hard_delete_chapter_packets` bulk-deletes every packet for a chapter, in no lineage order.**
   `api/packet_delete.py:67-78`: it selects every `ChapterPacket` for the chapter and calls
   `session.delete(row)` in query order.
9. **The scene tier permits an autonomous approver; nothing yet did at the chapter tier.**
   `ScenePacketApprovalSource` carries `AUTONOMOUS_POLICY` (`shared/enums.py:263`, ADR-0033 D5b), and
   `ChapterPacket` had no approval-provenance column at all before this work.
10. **`AdoptionOperation` already anticipated amendment as a distinct operation.** Its docstring
    (`shared/enums.py:564-568`) states AMENDMENT is *"the ONLY operation whose eligibility envelope
    REQUIRES an already-approved ChapterPacket"*, and every other operation refuses one via
    `refuses_approved_packet` (`shared/adoption_entry.py:146,156,165`).
11. **`ImportAdoptionMode.AMENDMENT` pre-existed and was refused closed.** The member is
    `shared/enums.py:504`; the worker fails any claim carrying it
    (`workers/import_adoption.py:515-520`) with `AmendmentModeUnsupported` — *"amendment adoption mode is
    a Slice 3b non-goal and is refused closed; it is never partially implemented"* (`:96-104`).
12. **The `ChapterPacket` writer guard from #259 exists and is extensible.**
    `tests/test_issue259_chapter_packet_writer_guard.py` walks the AST of every `src` file mentioning
    `ChapterPacket`, tracks names bound to one, and flags an authority-field store outside the lock
    (`:30-34`, `:287-296`). Every exemption is re-verified against a named locked caller on each run
    (`:418-431`).

## Design

### D1 — Eligibility is STRUCTURAL (seed presence), never `ScenePacket.status == STALE` — [SETTLED]

> **A chapter is amendable iff it has an approved ChapterPacket, has at least one imported scene carrying
> prose, and at least one of those scenes resolves to NO seed in the approved packet's
> `body["scene_seeds"]`.** The predicate is seed presence. `ScenePacket.status` is never consulted.

`assess_chapter` (`workers/packet/amendment.py:185-296`) reads, writes nothing, calls no model, and
returns a frozen `AmendmentVerdict` (`:113-132`) carrying the evidence that produced it. Its five reason
tokens are enumerated as module constants (`:137-141`) *"so the API schema, the Desk, and the tests share
one vocabulary and a typo cannot invent a silent new outcome"* — `unseeded_scenes_present` is the only
eligible one.

**Why not STALE.** Context fact 3: `source_hash` is built from different payloads at derive
(`derive.py:576-584`, which passes `canon_chunk_hashes` **and** `scene_pov`) than at recompute
(`staleness.py:111-117`, which passes **neither**). Every packet derived against a book with embedded
canon is therefore marked STALE on the next recompute regardless of real drift. STALE is a false positive
for exactly the population amendment mode serves, and it **cannot distinguish stale-seed from no-seed** —
which is the one distinction D1 turns on. Seed presence can, and is immune to the defect.

**That hash asymmetry is a real, separate defect, and it is NOT fixed here.** It is listed under
Non-goals. Amendment mode is deliberately built so as not to depend on it in either direction: fixing it
later changes nothing about eligibility, and leaving it broken does not make a chapter wrongly amendable.

**Two seed→scene linkages are honoured, and the OR is the fail-closed direction.** A scene counts as
seeded when the producing adoption's `seed_bindings` binds a seed to that exact scene id **or** some seed
claims that scene's `scene_no` (`amendment.py:256-260`). Both exist in the wild: `seed_bindings` is
written only for adoption-derived packets, planning-path packets link by `scene_no` alone. Taking the OR
means an ambiguous chapter is treated as **covered** and amendment is **refused** — the opposite bias to
`derive.py:670-684`, which fails a *scene* closed on a missing binding. The risks are asymmetric: there,
drafting against a contract that does not cover the prose; here, superseding an approved contract that was
fine.

**`_bound_scene_ids`' "still present" filter is load-bearing** (`amendment.py:299-329`): `seed_bindings`
is a historical record of the packet at publish time, so a binding whose seed has since been edited out of
the body must not count as coverage — that scene is precisely the no-seed case.

**`_seed_index` mirrors the derive loop's own filter exactly** (`amendment.py:166-182`) — a seed counts
only when it is a dict carrying a truthy `seed_id`, matching `derive.py:456-457`. If the two disagreed,
amendment could be refused for a chapter whose derive loop still skips the scene.

### D2 — "Active" means `status='approved'`, and the proposed amendment coexists with it — [SETTLED]

```sql
CREATE UNIQUE INDEX uq_chapter_packets_active_chapter
  ON chapter_packets (chapter_id) WHERE status = 'approved';          -- migrations.py:326-328

CREATE UNIQUE INDEX uq_chapter_packets_open_amendment
  ON chapter_packets (chapter_id)
  WHERE origin_mode = 'amendment' AND status = 'proposed';            -- migrations.py:333-335
```

The first index is partial over `approved` **only**, deliberately: *"`proposed` amendments must coexist
with the approved predecessor they were copied from (that IS the review state), and
`superseded`/`blocked` rows are history, not authority"* (`migrations.py:320-321`). That exclusion is what
makes D4's hand-over expressible as one transaction. Fact 1 records that this invariant was previously
application-only and fact 2 records what a violation cost.

Coexistence would otherwise be unbounded, so the review state is capped separately by the second index:
one open amendment branch per chapter, the structural half of retry idempotency. It is partial over
`proposed` only — *"a `blocked` amendment is terminal diagnostic evidence, and including it would let one
failed attempt bar every future amendment of that chapter forever"* (`migrations.py:331-332`).

The eligibility layer answers the same question typed rather than as an `IntegrityError`: an existing open
branch yields `reason = amendment_already_open` carrying `open_amendment_packet_id`
(`amendment.py:273-286`), *"a typed answer beats an IntegrityError"*.

**Where the existing branch reaches the operator, and where it does not.** The advisory preflight surfaces
it — `AmendmentEligibilityOut.open_amendment_packet_id` (`shared/schemas.py:625`), populated at
`api/routers/adoption.py:205`, described as *"Set when a proposed amendment branch already exists (the
idempotent refusal) — review that one"*. The **write** path does not: `ChapterNotAmendable` carries the
`reason` token but not the packet id (`shared/adoption_entry.py:106-109`), so
`POST .../amendment/start`'s 409 (`api/routers/adoption.py:237-247`) tells the author *that* a branch is
open without naming it. Recorded rather than fixed: the Desk can resolve it with one extra preflight call,
and the refusal is correct either way — the index would reject the fork regardless.

### D3 — Supersede, never delete — [SETTLED]

A superseded packet keeps its row, its `body`, and its history, and gains three fields:
`status = 'superseded'`, `superseded_by_packet_id`, `superseded_at` (`amendment.py:441-445`).
`PacketStatus.SUPERSEDED`'s docstring pins the semantics: *"A terminal, immutable historical record — it
MUST name its successor… SUPERSEDED is the one state that is NOT a candidate for anything: it is never
re-approved, never re-derived from, and never returned by an authority reader"* (`shared/enums.py:156-162`).

**Why the existing DELETE-on-replace policy does not extend here.** ADR-0028 Slice 3b's Q11 tier-C ruling
made re-author *replace* the chapter's current packet, deleting the old row and nulling the adoption's
link (`shared/models.py:494-503`). That ruling was scoped to **unapproved transient** packets, and the
code fences the approved case off three ways (fact 7): `_persist`'s `preserve_approved` branch re-checks
under the lock and returns the existing approved packet rather than replacing it
(`workers/packet/__init__.py:904-912`); the REAUTHOR policy carries `refuses_approved_packet=True`
(`shared/adoption_entry.py:146`); and the route's 409 says in so many words that changing approved
material is *"an amendment/revision, not a re-author"* (`api/routers/adoption.py:143-153`). That refusal
is the hole this ADR fills, and it named the fill correctly — so extending delete-on-replace to approved
material would contradict the ruling that created the gap, not implement it.

Two structural consequences of choosing supersede:

- The predecessor's derived ScenePackets are **staled, not deleted** (`amendment.py:487-519`), *"preserves
  the author's review history and the ApprovalBlocker rows that hang off them"* (`:457-458`).
- `AMENDMENT_STALE_REASON` (`amendment.py:58`) is a distinct, queryable string —
  `"superseded by an approved chapter-packet amendment — re-derive this scene"` — rather than
  `staleness.py:120`'s generic `"upstream inputs changed since derivation"`, because *"that string cannot
  tell an author whether a canon edit, a word-budget change, or a superseded chapter contract caused it,
  and the recovery differs"* (`:54-57`).

### D4 — ONE locked transition; an ordinary approve is the degenerate no-predecessor case — [SETTLED, with the sharing NOT yet realized]

> **`_apply_authority_locked` (`amendment.py:363-484`) is the single authority transition, written so that
> ordinary approve and amendment approve are the SAME operation — an ordinary approve is simply the case
> with no predecessor to supersede (`expect_amendment=False`, and every amendment-specific guard at
> `:413,418` is behind `if is_amendment`).**

The reason for one body is stated at the seam itself: *"a second seam is how 'two approved packets' becomes
reachable again"* (`:374-375`).

> **⚠ Divergence from brief — the sharing is designed but not wired.** The brief describes the transition
> as *"shared by ordinary approve and amendment approve"*, and
> `api/routers/packets.py:346` asserts *"both routes funnel into
> `workers/packet/amendment._apply_authority_locked`"*. **The ordinary route does not.**
> `approve_packet`'s locked body still writes `row.status = PacketStatus.APPROVED` inline
> (`api/routers/packets.py:311`) and calls nothing from `workers.packet.amendment`. The two write sites are
> kept apart by a **route-level guard** instead: `packets.py:292-308` refuses a `proposed` amendment with
> `409 amendment_requires_amendment_approval` and points the author at the correct endpoint, checked
> *inside* the locked body on the post-lock reload *"not on a pre-lock read: an amendment can be published
> by the adoption worker between a caller's read and this transaction"* (`:287-291`). That guard is real
> and load-bearing — its own comment records that before it, the case *"failed closed only by accident:
> `uq_chapter_packets_active_chapter` rejected the second approved row and the author got a raw 500"*
> (`:280-281`). But **`_apply_authority_locked` is currently the single writer of the AMENDMENT transition,
> not of chapter-packet approval in general.** Folding `approve_packet` into it is the remaining half of
> this decision; until then D4's guarantee rests on the guard plus the index rather than on one function.

The AST writer guard from #259 makes a
regression visible — `AUTHORITY_FIELDS` now covers the six new lineage/provenance columns
(`tests/test_issue259_chapter_packet_writer_guard.py:75-91`), justified because
`supersedes_packet_id`/`superseded_by_packet_id` *"are what the two lineage CHECKs test, so a write to
either can move a chapter between 'has an authority' and 'has none'"* and `origin_mode` *"selects which
partial unique index a row falls under, so flipping it can free or occupy the single active slot"*
(`:69-74`).

**Transaction order, and why it cannot be otherwise** (`amendment.py:382-386`, executed at `:387-467`):

```
1. reload the target under the lock (populate_existing)      -> :387
2. re-check the prose fingerprint; fail closed on drift      -> :413-416
3. re-check the predecessor is STILL the approved authority  -> :418-435
4. predecessor leaves `approved` naming its successor
   -> await session.flush()                                  -> :441-445
   THEN the successor takes the freed slot
   -> await session.flush()                                  -> :447-454
5. stale the invalidated ScenePackets; record the scope       -> :459-467
```

Step 4's flush between the halves is not tidiness. `uq_chapter_packets_active_chapter` covers `approved`
only, so the successor cannot enter until the predecessor has left — *"which is the guarantee that two
approved packets are unreachable rather than merely unlikely"* (`:383-385`). Without the flush SQLAlchemy
picks its own UPDATE order and may promote before demoting; `tests/test_amendment_mode.py:117-145`
(`test_a_superseded_packet_frees_the_active_slot`) pins exactly that ordering, and its comment
(`:125-129`) records that the index *catching* the mistake is the invariant working.

**Idempotent replay is a terminal success, not an error** (`amendment.py:398-408`): an already-approved
target returns the existing state with `was_already_approved=True` and an empty staled set, so a retried
request neither errors nor performs a second supersession. `_stale_children_of` skips already-stale rows
(`:504`) *"so an idempotent replay reports an empty set, not a lie"*.

**A missing or demoted predecessor fails closed with a diagnosis, not an `IntegrityError`.**
`AmendmentPredecessorMissing` (`:103-107`) fires when the amendment names no predecessor, when the
predecessor is gone, or when its status is no longer `approved` — *"the exact state
`ck_chapter_packets_amendment_names_predecessor` forbids"*, caught in code first so the operator gets a
sentence instead of a constraint name.

### D5 — Model calls stay outside the lock; the transition reloads, revalidates, and fails closed — [SETTLED]

The adoption worker authors the amendment packet — one to two minutes of model calls — **outside** the
lock. `approve_amendment` (`amendment.py:522-559`) then reacquires the chapter workflow lock via
`run_under_chapter_workflow`, and *inside* it: re-runs `assess_chapter` against authoritative state
(`:545`, *"the verdict that justified authoring this amendment was computed minutes and one model call
ago"*), then calls `_apply_authority_locked`.

**`populate_existing=True` is load-bearing, not defensive.** `_reload_packet_locked`
(`amendment.py:352-360`) passes both `populate_existing=True` and `with_for_update=True`, because
`session.get` alone returns the identity-mapped instance **without emitting SQL** — so a caller that
already read the row (the pre-flight response does) would have its **pre-lock copy** silently returned and
"reload under the lock" would do nothing at all, leaving every guard below to evaluate stale state. The
same discipline is spelled out in production at `api/routers/reviews.py:295-300` and
`workers/production_repair.py:638-644` (fact 5).

**The drift gate is recomputed under the lock, from the same membership query** (`amendment.py:413-416`):
`chapter_source_fingerprint(await chapter_scene_rows(session, chapter_id))` compared against
`packet.source_fingerprint`, with a `NULL` stored fingerprint treated as failure. A mismatch raises
`AmendmentSourceDrifted` (`:88-100`) and **nothing is written** — the amendment stays `proposed` and
reviewable. A pre-lock check would be worthless: *"the whole point is that prose can move while a model
call is in flight"* (`:410-412`). Fact 4 is what makes the comparison meaningful — one membership query
behind every chapter fingerprint, so the adoption worker's drift CAS and this gate are comparable rather
than silently incommensurable (`prose_fingerprint.py:27-31`).

`run_under_chapter_workflow` owns the commit (fact 6), so a crash anywhere before it changes nothing, and
a `ChapterWorkflowBusy` means the body never ran (`amendment.py:531-535`). `_apply_authority_locked` is
registered in the writer guard's exemption table with its locking caller named, and
`test_every_exemption_still_points_at_a_locked_caller` re-verifies that caller on every run
(`tests/test_issue259_chapter_packet_writer_guard.py:122-129`).

### D6 — No autonomous chapter approver, enforced structurally — [SETTLED]

> **`ChapterPacketApprovalSource` has NO autonomous member, and a CHECK permits only the two values it
> does have. Adding an autonomous chapter approver therefore requires a migration.**

```sql
CHECK (approval_source IS NULL
       OR approval_source IN ('manual_command', 'legacy_unclassified'))   -- migrations.py:393-398
```

The enum is `MANUAL_COMMAND | LEGACY_UNCLASSIFIED` (`shared/enums.py:190-191`). Its docstring states the
contrast with the scene tier explicitly: *"At the scene tier an automated approver is legitimate within
its ceiling (ADR-0030). At the CHAPTER tier it is not — no model output may approve a chapter contract,
supersede a predecessor, clear a blocker, or select which packet holds authority. That prohibition is
expressed as the ABSENCE of a value here and a CHECK constraint"* (`:171-179`). `ScenePacketApprovalSource`
keeps `AUTONOMOUS_POLICY` (`:263`) — the divergence is deliberate, one tier only.

`MANUAL_COMMAND` asserts *"a deliberate command, NOT an authenticated human identity (the system has
none)"* (`enums.py:181-182`), the same honesty ADR-0033 D5b established at the scene tier.
`LEGACY_UNCLASSIFIED` is treated as unproven: *"unproven provenance is not human provenance"* (`:183-184`).

Enforcement is a CHECK rather than a code convention *"so introducing an autonomous chapter approver
requires a schema migration rather than a one-line code change"* (`enums.py:178-179`) — *"a decision that
cannot be made accidentally in a refactor"* (`migrations.py:390-392`).
`tests/test_amendment_mode.py:161-171` proves it at the database: setting
`approval_source = "autonomous_policy"` raises `IntegrityError`.

`approval_source` on the transition is a required argument on `_apply_authority_locked` and defaults to
`MANUAL_COMMAND` only on the public `approve_amendment` (`amendment.py:527`), *"because every wired caller
is a deliberate command; the enum has no autonomous member, so no model-driven caller can supply one even
deliberately"* (`:537-538`).

### D7 — No self-referential FKs on the lineage columns — [SETTLED; its reconciliation half OPEN]

`supersedes_packet_id` and `superseded_by_packet_id` are plain `UUID` columns
(`shared/models.py:328-329`; `migrations.py:163-164` add them as bare `UUID`). A repo-wide grep for both
names across `src` returns no `ADD CONSTRAINT … FOREIGN KEY` on either.

The reason is the **delete shape**, and it is unlike `import_adoptions.reauthor_of_adoption_id`, which
*does* get a self-FK (`migrations.py:484-488`). Fact 8: `packet_delete.hard_delete_chapter_packets`
removes **every** `chapter_packets` row for a chapter in one transaction, in query order
(`api/packet_delete.py:75-77`). A self-FK would make that per-row delete **order load-bearing**; an
`ON DELETE SET NULL` escape hatch would instead null a lineage column on a row whose `status` is still
`superseded`, tripping `ck_chapter_packets_superseded_names_successor`. The migration states exactly this
(`migrations.py:399-406`) and concludes: lineage integrity is enforced by the CHECKs plus the boot
reconciliation sweep (see the `[OPEN]` note below — the sweep is not built), and *"a dangling lineage id
can only ever arise from a deliberate whole-chapter contract delete, where losing the history is the point
of the operation."*

Three CHECKs carry that load:

| Constraint | Predicate | Where |
|---|---|---|
| `ck_chapter_packets_superseded_names_successor` | `status <> 'superseded' OR superseded_by_packet_id IS NOT NULL` | `migrations.py:362-369` |
| `ck_chapter_packets_amendment_names_predecessor` | `origin_mode <> 'amendment' OR status <> 'approved' OR supersedes_packet_id IS NOT NULL` | `:372-379` |
| `ck_chapter_packets_no_self_lineage` | both lineage ids `IS DISTINCT FROM id` | `:382-387` |

Together the first two make *"approved amendment with no superseded predecessor"* unrepresentable
(`:370-371`); the third stops a self-loop that would satisfy both while making the lineage walk
non-terminating (`:380-381`).

**The reconciliation half of D7 is designed and NOT built — [OPEN].** The CHECKs cannot see a *dangling*
lineage id (a `superseded_by_packet_id` naming a row that no longer exists), which is exactly the residue
D7 accepts in exchange for the delete shape. That residue was to be caught by an observe-only boot sweep,
and its vocabulary exists: five `IntegrityHoldReason` members —
`MULTIPLE_APPROVED_CHAPTER_PACKETS`, `SUPERSESSION_SUCCESSOR_MISSING`,
`APPROVED_AMENDMENT_WITHOUT_PREDECESSOR`, `SUPERSEDED_PACKET_HAS_LIVE_CHILDREN`,
`CHAPTER_AUTHORITY_VACATED` (`shared/enums.py`), whose docstring states the policy: the sweep *"REPORTS
rather than repairs: which contract a book is written against is a human's decision, and silently picking
one would destroy the evidence"*, and notes that `SUPERSEDED_PACKET_HAS_LIVE_CHILDREN` is *"the one
genuinely reachable case"* (a scene packet approved through another route after the supersession).
`workers/boot_reconciliation.py:39` promises the function — *"See `reconcile_chapter_packet_authority` for
the five states and their predicates"* — and its module docstring contrasts the two sweeps' intents (the
D7/D8 sweep *reconstructs* lost intent; the authority sweep only *observes*, because the transition is one
locked transaction, so *"observing one means a CONSTRAINT was bypassed"*). **A grep across `src` and
`tests` finds no definition of `reconcile_chapter_packet_authority` and no writer for any of the five
members.** Until it exists, a dangling lineage id is detectable only by hand.

> **⚠ Divergence from brief — a stale model-layer comment contradicts the migration.**
> `shared/models.py:324-327` says the lineage columns are *"PLAIN UUIDs with NO inline ForeignKey — the
> self-referential FKs are added NOT VALID in migrations._EXTRA_DDL (mirroring
> scene_packets.source_scene_id and import_adoptions.reauthor_of_adoption_id)"*. **No such FK is added.**
> `migrations.py:399-406` says the opposite and gives this decision's reasoning; the grep confirms the
> migration, not the comment. D7 as recorded above is what the code does. The `models.py` comment is
> wrong and should be corrected to match — it is a documentation defect, not a behavioural one, and it is
> the kind that gets cited later as evidence for a constraint that never existed.

### D8 — Migration policy: additive, gated, and fail-closed at the index — [SETTLED; rollout complete through W2, W3–W5 OPEN]

Ten nullable `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements (`migrations.py:163-185`), *"so each is
safe on a populated prod table; the two CHECKs and the two partial UNIQUE indexes that give them teeth
live in `_EXTRA_DDL` below, behind the `_preflight_single_active_chapter_packet` guard"* (`:160-162`).

**`origin_mode` is the one column that must be NOT NULL**, because both the eligibility envelope and
`uq_chapter_packets_open_amendment` read it and *"a NULL would silently escape a partial index's WHERE
clause"* (`:166-168`). It is added with `DEFAULT 'initial'` (`:172`), backfilled for pre-existing rows
(`:259`), and tightened with `SET NOT NULL` in `_EXTRA_DDL` (`:356`) behind
`ck_chapter_packets_origin_mode` (`:350-355`). **Unlike ADR-0032 W0's `liveness_basis` temp default, this
default is PERMANENT** (`:169-171`): the ORM supplies the value on every insert
(`models.py:335`), and the default only covers a mid-deploy window where an older writer is still running.

**The provenance columns get NO server default** (`:173-177`) — a fresh row must land `NULL` ("never
approved") rather than claim a provenance it does not have. Four gated backfills, each self-gating on
`IS NULL` so it is a no-op on every boot after the first (`:252-265`):

| Backfill | Gate | Rationale |
|---|---|---|
| `approval_source = 'legacy_unclassified'` | `status='approved' AND approval_source IS NULL` | an already-approved packet predates chapter-tier provenance and *"must not claim a deliberate command approved it"* (`:252-254`) |
| `origin_mode = 'initial'` | `origin_mode IS NULL` | *"every packet that predates amendment mode was an initial proposal by definition"* (`:257-258`) |
| `approved_at = created_at` | `status='approved' AND approved_at IS NULL` | `created_at` is the only timestamp such a row has — *"the honest available approximation rather than a fabricated precise time"* (`:260-263`) |

The drift-gate and evidence columns are **deliberately left NULL for legacy rows** (`:179-181`): *"a
legacy packet genuinely HAS no captured fingerprint, and defaulting one in would manufacture a 'drift
verified' claim that no code ever checked."* `models.py:346-347` carries the same reasoning — the
projection reports the absence rather than defaulting it to "verified".

**The preflight refuses to build the constraints over a violating database, and repairs nothing.**
`_preflight_single_active_chapter_packet` (`migrations.py:707-780`) runs before `_EXTRA_DDL`
(`:877-880`) and raises `DuplicateActiveChapterPacketError` (`:699-704`) — aborting the whole migration
transaction — on either of two findings: a chapter with more than one `approved` packet
(`:726-755`), or any row whose `status` is outside the four permitted values (`:757-771`, so the raw
`ADD CONSTRAINT ck_chapter_packets_status` cannot *"fail cryptically mid-DDL instead of naming the
offending rows"*). The report lists the chapter plus each conflicting packet's
`id/status/confidence/origin_mode/created_at`.

**It picks no winner, deliberately**, and the reason is a policy statement rather than caution: *"A
ChapterPacket is the constraint document every drafting agent obeys, and auto-selecting a survivor would
silently change which contract the book is written against; that is a human's decision, not a
migration's"* (`:711-713`). This mirrors ADR-0032 W0's duplicate-adoption preflight — same shape, higher
stakes, since a packet is authority rather than work-in-flight.

**Rollout.**

```
W0  Guarded schema — LANDED.  Ten nullable ADDs; four gated backfills; fail-closed preflight;
      then both partial unique indexes, four CHECKs, and origin_mode SET NOT NULL.
W1  Domain layer — LANDED.  assess_chapter + the one locked transition; the AMENDMENT entry policy;
      chapter_scene_rows promoted to shared/prose_fingerprint.py; AUTHORITY_FIELDS extended with the
      six new columns and the transition registered as a caller-verified exemption.
W2  Operator surfaces — LANDED.  GET .../amendment/eligibility (advisory, no lock, no model);
      POST .../amendment/start (the FIFTH adoption-entry caller, mapping ChapterNotAmendable onto a
      409 carrying the verdict's own token + REFUSAL_MESSAGES sentence);
      POST .../packet/{id}/approve-amendment over approve_amendment, with a typed 409 per failure
      mode; and the wrong-endpoint guard on the ordinary approve route.
W3  The authoring pass — NOT BUILT.  [OPEN]  The blocking gap, because W2 is reachable without it:
      the copy-on-write author must replace the AmendmentModeUnsupported refusal at
      import_adoption.py:515-520 (copy the approved body, author seeds for the unseeded scenes from
      evidence, capture source_fingerprint + evidence_manifest_fingerprint + origin_adoption_id +
      supersedes_packet_id, publish at status=proposed). Until it lands, .../amendment/start mints an
      adoption the worker immediately fails.
W4  Lineage reconciliation — NOT BUILT.  [OPEN]  reconcile_chapter_packet_authority, promised at
      boot_reconciliation.py:39, observe-only, writing the five IntegrityHoldReason members (D7).
W5  Residual wiring — NOT BUILT.  [OPEN]
      (a) fold approve_packet into _apply_authority_locked so the shared transition is real and not
          only documented (the D4 divergence);
      (b) replace "amendment mode, which is not available yet" at reviews.py:431,442 — reviews.py is
          untouched by this work and still tells the author the feature does not exist;
      (c) tests over _apply_authority_locked itself — see the coverage note in Consequences.
```

**Rollback:** drop the two partial unique indexes and the four CHECKs, accepting the weaker
application-only invariant (a known regression, fact 1). The additive columns stay; no destructive column
rollback.

## Non-goals

- **The derive/recompute `source_hash` asymmetry** (`derive.py:576-584` vs `staleness.py:111-117`, fact 3)
  is a **separate defect and is NOT fixed here**. It causes false-positive STALE for any book with
  embedded canon. D1 is built so as not to depend on it in either direction; fixing it later requires no
  change to this record.
- **ADR-0031 D13 — the precedence-authority ruling (#259 residual 2).** D13
  (`docs/adr/0031-approval-authorization-boundary-and-adr-0028-decomposition.md:81-83`) rules that
  `shared/claim_precedence.py` *"is not automatically part of the target architecture"* and owes a bounded
  decision record comparing integrate / replace / delete. Amendment mode neither resolves nor depends on
  that; `packet/evidence.precedence_adjudication` remains advisory and never a gate
  (`workers/packet/__init__.py:824-826`).
- **The stranded-RUNNING-`Job` lease gap.** `tests/test_job_lease_recovery.py` (untracked at HEAD) is a
  RED-first record that `Job.claimed_by`/`claimed_at` have *"the exact shape of a lease and none of its
  enforcement"* (`:12`), so a `Job` that is RUNNING when the process dies is stranded permanently and
  keeps masquerading as active (`:110-179`). Amendment mode neither causes nor repairs it, and it touches
  `jobs`, not `chapter_packets`.
- **Autonomous chapter-tier approval.** Excluded structurally rather than merely deferred (D6). Admitting
  it is a migration plus a new ADR, not a code change.
- **Chapter-tier ApprovalBlockers, amendment-scoped cost ceilings, fidelity scoring, and the Desk's
  amendment review surface.** Not designed here.

## Alternatives considered

- **Delete-on-replace, extending ADR-0028 Slice 3b's Q11 tier-C policy to approved packets** — rejected.
  That ruling was scoped to unapproved transient packets and the code fences the approved case off three
  independent ways (fact 7), including a 409 whose message names *"amendment/revision"* as the correct
  path (`api/routers/adoption.py:143-153`). Deleting an approved packet also destroys the only record of
  which contract already-drafted prose was written against, and `superseded` rows are what let a later
  reader answer that question at all.
- **Treating `proposed` as active (a single "current packet" slot covering both statuses)** — rejected: it
  makes the review state unrepresentable. An amendment must be reviewable **while** its predecessor still
  governs, which is precisely the state `uq_chapter_packets_active_chapter`'s `WHERE status = 'approved'`
  permits (`migrations.py:320-321`). A wider index would force either a gap with no authority or a
  pre-emptive supersession before anyone reviewed the replacement.
- **Keying eligibility on `ScenePacket.status == STALE`** — rejected on evidence, not taste. Fact 3: STALE
  fires for every packet derived against populated canon regardless of drift, so it is a false positive
  for the target population, **and** it cannot distinguish stale-seed (a normal re-derive) from no-seed
  (the amendment case) — the one distinction that decides whether an approved contract gets superseded.
  Building on it would have made amendment mode's correctness depend on fixing an unrelated hash defect.
- **A separate `chapter_packet_amendments` table instead of copy-on-write rows in `chapter_packets`** —
  rejected: an amendment **is** a ChapterPacket, and on approval it becomes *the* ChapterPacket. A
  separate table would need its own body schema, its own QA path, and a promotion step that copies a row
  between tables — and "one approved authority per chapter" would then span two tables, which no single
  unique index can express. Every existing reader (`draft_readiness`, `derive`, the Desk, the projection)
  would need a second lookup. Copy-on-write in place means the invariant is one index and the readers are
  unchanged.
- **Two approval seams (ordinary approve, and a separate amendment approve)** — rejected: two seams is how
  "two approved packets" becomes reachable again (`amendment.py:374-375`). An ordinary approve is the
  degenerate no-predecessor case of the same transition (D4). *Note that the code has not yet finished
  acting on this rejection — see D4's divergence note: two write sites still exist, fenced apart by a
  route-level guard rather than merged.*
- **A route-level guard as the permanent answer instead of merging the two approve paths** — rejected as
  the end state, accepted as the interim. `packets.py:292-308`'s
  `409 amendment_requires_amendment_approval` is correct and its placement inside the locked body on the
  post-lock reload is right, but it protects against the *wrong endpoint* rather than against a *second
  writer*: a third approve path added later would need to remember the guard, which is precisely the class
  of failure "one transition" exists to remove.
- **Self-referential FKs for lineage integrity** — rejected on the delete shape (D7):
  `hard_delete_chapter_packets` bulk-deletes a chapter's whole packet history, so a self-FK makes delete
  order load-bearing and an `ON DELETE SET NULL` escape hatch trips the lineage CHECKs instead.
- **Letting the migration pick a surviving packet when it finds two approved rows** — rejected: which
  contract a book is written against is a human's decision, not a migration's (`migrations.py:711-713`).

## Consequences

- **Two application invariants became database guarantees.** "One approved ChapterPacket per chapter" and
  "one open amendment branch per chapter" now hold against any writer, including a maintenance script, a
  future worker, or deployment-version overlap — the classes the advisory lock cannot cover.
  `tests/test_amendment_mode.py:84-114` proves the first at the DB and proves the existing authority
  survives a rejected second write.
- **The new index immediately caught a real fixture violation, which is the invariant earning its keep.**
  `tests/conftest.py`'s `seed_scene_packet` used to mint a fresh approved ChapterPacket on every call, so
  two calls for one chapter created two approved rows. It now reuses the chapter's existing approved
  packet — *"'at most one approved ChapterPacket per chapter' is a real invariant and this helper was
  quietly violating it"*. Nothing those tests assert depended on the second packet.
- **`GET /draft/readiness` stops being able to resolve an arbitrary authority.**
  `draft_readiness.py:514-523`'s `ORDER BY`-less `.limit(1)` is still there, but the split-brain that made
  it dangerous is now
  unrepresentable. The query is worth tightening; it is no longer a correctness hazard.
- **A boot over a dirty production database will now FAIL CLOSED rather than start.** If any chapter
  already carries two approved packets, `_preflight_single_active_chapter_packet` aborts the migration
  transaction with a per-packet report and the app does not come up until an operator resolves it by hand.
  That is the intended behaviour (fact 1 records the violation is reachable), but it is a **deploy-time
  operational obligation**, not a silent no-op — the first boot after this lands is the moment to have the
  report ready to read.
- **Coverage at v1: 20 tests across three files, and the transition is exercised directly.**
  `tests/test_amendment_mode.py` (7) pins the DB invariants — I2 (`:84`), the freed slot and the
  load-bearing inter-half flush (`:117`), orphaned supersession (`:148`), invariant 8 (`:161`) — and
  eligibility's three-way split (`:177`, `:199`, `:217`). `tests/test_amendment_torture.py` (10) calls
  `approve_amendment` for real: concurrent approvals leaving exactly one authority (`:213`), lock-timeout
  writing nothing (`:289`), idempotent replay superseding once (`:324`), prose drift failing closed
  (`:386`), a crash after the rows moved (`:427`), a demoted predecessor (`:505`), child-staling including
  already-stale rows (`:556`), no approved ScenePacket surviving on a superseded contract (`:615`),
  the legacy-classification backfill (`:667`), and the preflight refusing the index (`:715`).
  `tests/test_amendment_migration_parity.py` (3) proves every new column is provisioned on an
  already-existing DB, not only by `create_all`.
- **`amendment_scope`'s documented shape overstates what is written** — the one remaining write-only-field
  gap. `models.py:359-360` documents the key set as
  `{"unseeded_scene_ids": [...], "staled_scene_packet_ids": [...], "new_seed_ids": [...]}`, but
  `_apply_authority_locked` writes `predecessor_packet_id`, `staled_scene_packet_ids`, `superseded_at`
  (`amendment.py:462-466`). `unseeded_scene_ids` and `new_seed_ids` are never written, so the Desk's
  "affected scenes" list cannot rely on them. Either the writer or the comment must change before a reader
  does; the comment is the cheaper fix, since `unseeded_scene_ids` is recoverable from the verdict at
  authoring time and `new_seed_ids` presupposes the unbuilt authoring pass (W3).
- **`chapter_scene_rows` now has one home and two callers**, so the adoption worker's drift CAS and
  amendment mode's drift gate are comparable by construction (`prose_fingerprint.py:21-45`;
  `import_adoption.py:132-142` delegates). Any future change to snapshot membership moves both at once —
  which is the point, and also the new blast radius.
- **ADR-0032's stopping point is reached, not passed.** Its Consequences named amendment mode as
  depending on both the adoption-entry lifecycle and #259's lock coverage; both landed, and the AMENDMENT
  policy entry (`shared/adoption_entry.py:177-183`) is the inverse envelope its `entry_intent` enum was
  kept non-boolean for (ADR-0032's Alternatives: *"`entry_intent` as a boolean — rejected: unreadable once
  amendment mode or another entry policy is added"*).
- **The operator story is reachable but not yet completable, and that asymmetry is the live risk.**
  `POST .../amendment/start` mints a `mode=amendment` adoption; the worker then fails it closed at
  `import_adoption.py:515-520`. So an author who follows the eligibility preflight's advice gets a failed
  adoption rather than a proposal to review — worse than a refusal, because the refusal at least said what
  to do instead. **W3 is therefore not merely "the next slice"; it is what makes W2 honest.** Until it
  lands, `.../amendment/start` should be treated as unreleased regardless of it being routable. Separately,
  Revise still tells the author amendment mode *"is not available yet"*
  (`api/routers/reviews.py:431,442` — that file is untouched by this work), which will read as a
  contradiction to anyone who has already used the amendment routes.

**Revisit triggers:** the derive/recompute `source_hash` asymmetry is fixed (→ re-read D1's rejection of
STALE; the structural predicate stays correct, but the *reason* it was chosen weakens, and a cheaper
staleness-based pre-filter may become available); a second amendment generation appears in one chapter
(→ verify the lineage walk and `ix_chapter_packets_supersedes` still serve "what superseded what");
`hard_delete_chapter_packets` is replaced by a lineage-aware delete (→ D7's self-FK rejection loses its
grounds and the FKs become available); an authenticated human identity exists in the system (→
`MANUAL_COMMAND`'s "deliberate command, not an identity" caveat can be tightened, D6); a chapter-tier
ApprovalBlocker is introduced (→ decide whether it holds the amendment's approval, the predecessor's
supersession, or both); an autonomous chapter approver is genuinely wanted (→ a migration plus a new ADR,
never a code change, D6); a fifth `AdoptionOperation` needs an inverse envelope
(→ `requires_amendable_chapter` is currently a one-off boolean and would become a policy-shape problem).
