# ADR-0035: Read-through reviews supplied text

**Status:** accepted · **implementation_authorized:** true · **Decision owner:** mark
**Rulings:** text source, author ruling 2026-09-14; seven amendments (queue independence and leased
ownership among them), author ruling 2026-09-15, with no further design cycle.

**Revision history**
- v1 (2026-09-15) — first record, written while the slice is being built on `main`. Citations are
  working-tree lines at the time of writing: the read-through models, CHECKs, config and escalation floor
  exist uncommitted; the worker and router do not exist yet. Re-verify a line before relying on it.

## Decision (the settled core)

**A Read-through is a paid, server-side reading of chapter text the author supplies, kept as an immutable
snapshot, that returns ranked editor's notes for each chapter and across the set.** It reads only its
snapshot and writes only its own rows: it approves nothing, rewrites nothing, and never selects or changes a
scene version. Its results are durable; the in-process task that produces them is not. Every bound on spend
is enforced before or during a call, never reconstructed afterwards.

The domain term is **Read-through**; the Desk screen is **Notes**. "Editorial review" is already a
manuscript export preset (`frontend/src/desk/manuscript/presets.ts:129-131`) and "editorial pass" already
defines a Production Run (`CONTEXT.md:8`).

## Context (verified in the working tree, 2026-09-15)

1. **Nothing reviews a whole document.** The author asked for edit recommendations "for the whole document,
   not scene by scene, beat by beat". A search of `src`, `frontend/src` and `docs` for chapter/book/manuscript
   review, editorial letter or developmental edit returns no match. The two nearest surfaces do other jobs:
   - **Edit** (`POST /style-review`, `api/routers/style_review.py:133`) is rules-only by instruction —
     *"Report only what a named rule condemns"* (`workers/reviewers/style_audit.py:56-60`) — with a
     4,000-token output cap (`:45`).
   - **Final QA** is a gate, not a reader: `run_final_qa` (`workers/production.py:765-780`) runs
     `assemble_run` and returns or refuses its QA artifact, and `production_sequence.py` calls no model.
2. **No rule in code selects the author's text.** On the deployed server, the current scene versions of
   Chapters 1 and 2 are model rewrites (observed during planning). Both "current scene" readers take the
   highest non-superseded version per scene number and nothing else: the Desk
   (`frontend/src/desk/api/hooks/useDeskCollections.ts:80-88`) and production
   (`workers/production_sequence.py:778-791`).
3. **The queue pause is a drain switch, and precedent points both ways.** It *"stops the drain from
   claiming new jobs"* (`workers/background_work.py:37-38`); `CONTEXT.md:223` calls it the switch that stops
   *"asynchronous claims"*. Its readers are the drains and workers (`background_work.py:135,175`,
   `worker.py:152`, `sweeper.py:392`, `autonomy_action.py:89`) and the adoption drain
   (`import_adoption.py:750`). Author-triggered derive and propose schedule model work as BackgroundTasks
   without consulting it (`api/routers/scene_packets.py:121-134`, `api/routers/packets.py:100-111`).
4. **The shared LLM helpers have four traps for a paid, retryable caller.**
   - `llm.complete` charges the work budget *after* the response and raises `BudgetExceeded` if over
     (`workers/llm.py:1014-1015`), so output already paid for is discarded.
   - `input_budget` refuses an oversized prompt locally, before any provider traffic (`llm.py:577-579,632-639`).
   - `attempt_with_escalation` defaults to an `EscalationPolicy()` with no output floor
     (`workers/llm_escalation.py:60`), drops the fallback's usage (`:84`, `_usage2`), accepts a fallback
     whenever `is_success(value2)` (`:104-105`), and returns `value2` even when it failed (`:108`).
   - OpenAI-compatible calls share one per-loop semaphore, held across the whole retry loop
     (`llm.py:501-528`), sized by `llm_openai_concurrency = 1` (`shared/config.py:349`).

   The retry loop catches `Exception` only (`llm.py:469`), so `asyncio.CancelledError` passes through it.
5. **The catalog-guarded CHECK block is ADD-only.** `IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE
   conname = …)` (e.g. `shared/migrations.py:580-585`) never re-applies a CHECK whose text changed under the
   same name.

## Design

### D1 — The text source is a supplied, immutable snapshot (option B) — [SETTLED · author ruling 2026-09-14]

> A Read-through reads text the author supplies per chapter — an uploaded `.md`/`.txt` file or pasted
> text — stored verbatim in `read_through_chapters.text` and never modified afterwards.

**Why not option A (review server scene versions, plus a "Use this version" action that never approves).**
Fact 2: reviewing the current versions would review model rewrites as if they were the author's book. A
therefore needs the author to choose a version per scene first, which means a new write path on `scenes`
that must be kept provably separate from approval. That is a larger, authority-adjacent change the notes
themselves don't need. B depends on no scene state: the snapshot is the book as the author means it at
that moment, and notes point into text that cannot move under them. **A is deferred, not rejected.** It
becomes viable once a trustworthy rule for "the author's current text" exists.

Accepted cost: the author supplies text again for each Read-through, and `.docx` enters only by paste.

### D2 — Execution: durable run row, in-process task, leased ownership — [SETTLED · author amendment 2026-09-15]

The POST commits a `queued` run row and schedules a FastAPI BackgroundTask, as derive and propose already
do (fact 3). **Results survive a restart; the task does not.**

- **Claim** is one conditional `UPDATE … WHERE status='queued' OR (status='running' AND lease_expires_at <
  now()) RETURNING` that mints a per-run `owner_token` uuid and a lease. The owner is a token, not a pid:
  `WORKER_ID = f"adoption-{os.getpid()}"` (`workers/import_adoption.py:96`) collides across containers.
- **Heartbeat** renews the lease every ttl/3 (ttl 90 s, `config.py:276`) with an owner-conditional update,
  *while the owner awaits the provider*. A failed renewal means ownership is lost: the owner cancels its
  call and exits without writing.
- **Every result write is conditioned on current ownership.** It's a short transaction that locks the run
  row, checks `owner_token`, status ∈ {running, stopping} and an unexpired lease, writes, and commits. **No
  row lock is held across a model call.**
- **Recovery** runs at lifespan start and lazily on GET status/list. It interrupts only (a) `running` or
  `stopping` runs whose lease has expired, and (b) `queued` runs nobody claimed within the admission
  deadline (120 s, `config.py:275`), reported as "didn't start; nothing was spent". **A live lease is never
  touched.**

Why a lease: a status alone cannot tell a live owner from a dead one, and a recovery that could interrupt a
live owner would let two writers produce one run's notes.

### D3 — Queue independence is author product policy — [SETTLED · author ruling 2026-09-15]

> A Read-through runs whether or not the drafting queue is paused.

**This is the author's product decision, recorded as such.** It is consistent with the pause's stated
purpose, stopping drains from claiming queued work (`background_work.py:37-38`; `CONTEXT.md:223`), and with
derive and propose, which don't consult the pause either. That precedent did not settle it: the adoption
drain *does* stop on the pause (`import_adoption.py:750`), and a Read-through is paid work the author starts
explicitly, like derive, but long-running, like adoption. What stops a Read-through is Stop (D4), not the
queue pause.

### D4 — Paid-operation bounds — [SETTLED · values are INITIAL CONFIG, `config.py:266-281`]

Each bound closes one way a run could spend more than the author asked for.

| Risk | Bound | Where |
|---|---|---|
| A lost POST response, retried, pays twice | `client_request_id` + `payload_sha256`, unique per `(book_id, client_request_id)`. A retry with the same id and payload returns the existing run (200); the same id with a different payload is a 409 | `shared/models.py:1347-1353` |
| Two runs on one book | partial unique index on `book_id` WHERE status IN (queued, running, stopping) | `models.py:1355-1360` |
| Many books at once | admission counts active runs across all books against `read_through_max_active` (2), under `pg_advisory_xact_lock(hashtextextended('read_through_admission', 0))` | pattern `shared/chapter_lock.py:101-104`; `config.py:274` |
| Fallbacks and retries multiply | an attempt allowance per run (2 per chapter + 2 for the book pass), checked before every call | `config.py:279-280` |
| Tokens creep | a cumulative ceiling (600k): no call starts if charged + its estimate would exceed it | `config.py:281` |
| A hung call or run | a call deadline (900 s, which includes waiting for the model slot and retries) under `asyncio.timeout`, and a run deadline (10,800 s) | `config.py:277-278` |
| An oversized prompt reaches the provider | admission builds every prompt from the snapshot and returns 422 naming the chapter; at call time `input_budget` refuses it locally (fact 4) | `config.py:270,272`; `llm.py:632-639` |
| The work budget throws away paid output | a fresh `TokenBudget` per call, with a hard limit ≥ input + `max_tokens` + headroom, so the post-call `BudgetExceeded` cannot fire on a call already paid for | `llm.py:1014-1015` |
| Spend leaves no trace | telemetry flushed **per attempt, in its own transaction**, in a `finally`, under `run_id = read_through.id` with chapter/phase/attempt metadata. If the flush fails it is rolled back, `accounting_gap = true` is set in a separate update, and the notes are kept | `workers/telemetry_db.py:29-51` |

Admission and execution build prompts with **one builder from one snapshot** (settings + voice guide), so
the estimate that admitted a chapter is for the same prompt that runs.

**Stop** keeps completed chapters, prevents every later call, and cancels the in-flight one (fact 4:
cancellation passes through the retry loop). The screen says that **a request already sent to the model may
still be billed**: cancelling stops the wait, not the provider's metering.

### D5 — Truncation travels with the attempt; the final result is re-validated — [SETTLED]

Fact 4 means the escalation helper can return a truncated but parseable fallback as a success, and a failed
`value2` as the result. So:

- `attempt_fn` returns a value carrying `truncated` from *that call's own* usage, and
  `is_success = validated and not truncated`. The helper's primary check (`llm_escalation.py:62`) and
  fallback check then agree.
- The returned value is **re-validated** before a chapter is marked done. If it is truncated or invalid, the
  chapter fails.
- The policy is passed **explicitly**, `policy_for_setting("read_through_model")`, so the configured floor
  (`"read_through_model": 24000`, `llm_escalation.py:156`) raises a truncated primary's fallback allowance
  (`:74-75`). The default policy has no floor (`:60`).
- A rate limit on the primary escapes the helper (`:61`), and the caller fails the chapter as
  "rate limited".
- **Only unparseable, invalid or truncated output earns a fallback attempt.** A call timeout,
  `PromptBudgetExceeded`, `ContextWindowExceeded` or `BudgetExceeded` fails the chapter without one:
  - the input-budget gate refuses any model equally;
  - the call deadline already includes the wait for the shared model slot.
- **The fallback model is read from live settings, not the run's snapshot.** The helper resolves it itself
  (`resolve_fallback_model`, `llm_escalation.py:31-36`). A mid-run change to the fallback in the Agents
  screen therefore applies to that run's later fallbacks. The primary model and every limit still come from
  the snapshot.
- **Cancelled and timed-out calls still leave a telemetry row.** `llm.complete` records failures only on
  `except Exception`, which cancellation skips, so the worker writes a zero-token row for those attempts
  (`workers/read_through/run.py`, `_call`). Every attempt stays attributable.

### D6 — Anchors are located on the server, stored, and rendered as stored — [SETTLED]

A note quotes the chapter: one to three verbatim quotes, with `…` to elide. The server turns each quote into
a **Read-through Anchor** (named so it is not confused with the SceneFidelity *Fidelity Evidence Anchor*):

- **A matching projection, never a normalized upload.** Quote and text are compared after NFKC, curly-quote,
  dash and special-space folding, whitespace collapse and casefolding (the fold at
  `workers/reviewers/base.py:56-86`), with `*`/`_` emphasis ignored. An index map leads back to the raw text,
  and the stored segment text is the exact raw substring.
- **Offsets are UTF-16 code units**, the unit the browser slices in, so astral and combining characters
  cannot shift a highlight.
- **Ellipsis segments match in order within one scene**, using manuscript import's scene-break rule
  (`workers/memory/manuscript_split.py:34,143`), promoted to a shared helper rather than copied.
- **One placement → `located`. Several → `ambiguous`.** Every candidate is kept (up to 5, with the true
  count) and shown; none is silently chosen, because picking the first would pin the note to a passage the
  model may not have meant. **None → `unlocated`.** A note whose anchors are all unlocated is dropped and
  counted.
- **The Desk renders stored spans and never re-searches.** It checks `text.slice(start, end) ===
  segment.text` and flags any mismatch; no first-occurrence search exists anywhere.
- **Structural notes may anchor to a location rather than evidence** (`anchor_role = location`). The quote
  marks where the reader needs something; it does not claim to prove an absence.

### D7 — The book pass reads full text when it fits, else digests, and says which — [SETTLED]

The cross-chapter call runs only after every chapter outcome is known. It is not run after Stop, and it is
skipped when fewer than two chapters are done. It reads **full text** when that prompt's estimate fits the
book input budget (110k, `config.py:272`), and otherwise the **validated chapter digests**.
`book_input_mode` and `book_chapter_ids` are persisted and shown, so omitted or failed chapters are visible
after reload and in the export. **In digest mode the prompt forbids claiming absence.** The model is reading
summaries, so "X is never explained" must become "verify whether X is explained".

## Not in this slice

Option A (server scene versions + "Use this version"); resuming an interrupted run; splitting one
multi-chapter file; `.docx` upload; canon-aware continuity; applying a note to prose; per-chapter retry; a
smoke harness for the role; dollar cost; a category filter.

## Consequences

- **A Read-through can delay other OpenAI work.** Every OpenAI-compatible call shares one in-process slot,
  held across retries (fact 4). By default, summary folds (`review_model`, `memory/summaries.py:118-119`) and
  Edit (`style_audit_model`) are `gpt-5.6-luna` (`config.py:45,255`), like `read_through_model`
  (`config.py:266`). So a long chapter call makes Inbox summary folds and Edit wait. The call deadline
  counts that wait, and the run banner discloses it ("May wait for a model slot").
- **CHECK constraints are versioned `_v1`** because the guard is ADD-only (fact 5; `migrations.py:593-597`).
  Changing a vocabulary takes a `_v2` block plus a drop of `_v1`, never an edited list under the old name.
- **Snapshots of creative text live only in the app database.** Chapter text, digests and quoted spans are
  manuscript text, stored in the deployed Postgres and nowhere else. Tests and fixtures use synthetic prose
  only.
- **A restart mid-run ends as `interrupted`, not resumed.** Completed chapters and their notes remain.
- **Every paid attempt is attributable**, by `run_id`, by stage (`read_through_chapter` /
  `read_through_book`) and by attempt metadata. When it isn't, the run says so (`accounting_gap`).
- **Model and limits are an initial configuration** (`gpt-5.6-luna`, fallback `gpt-5.6-terra`), accepted by
  the author to be judged on the first live reading. A successful run proves only that the workflow operates.
  Whether the notes are useful and respect the intended voice is the author's call.

**Revisit triggers:**
- A rule for "the author's current text" in scene versions exists → option A (D1).
- OpenAI slot concurrency rises above one, or Read-throughs get their own slot → the delay consequence.
- `llm.complete` stops raising `BudgetExceeded` after the call → the per-call budget in D4.
- `attempt_with_escalation` starts checking fallback usage → D5's re-validation becomes a backstop.
- A lease-enforced durable queue is adopted for author-started model work → D2 and D3.
