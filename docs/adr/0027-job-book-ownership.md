# book_id is the authoritative book owner of a Job

## Decision

A `Job` belongs to a **book** through its own `book_id`. `run_id` is retained as nullable provenance only (batch grouping + telemetry attribution) and is never a routing or scoping key. `scope_jobs_to_book` keys solely on `book_id`; there is no `run_id` fallback.

## Context

A job's book was historically expressed two ways: `book_id` directly, or `run_id -> Run.book_id`. An upload-only book has no `Run`, so its jobs carry `book_id` but `run_id = NULL`. Queries that scoped by `run_id` (an INNER JOIN to `Run`, or `run_id IN (select Run.id ...)`) silently dropped those rows. That is exactly how an uploaded scene's revision sat "queued" forever: the trigger/report path counted `0` and never kicked the drain, while the Activity drawer (book-scoped) showed it. The invariant — "every job belongs to a book" — was already the de-facto intent (`book_id` is sourced from the non-null `Chapter.book_id` in every factory) but was unenforced, so any new query could reintroduce the drift.

## Enforcement (three layers)

- **Database** (`shared/job_integrity.py`, run by the boot migration and `dominion-audit`): backfill `book_id` from the authoritative `chapters.book_id`, else from `runs`; a chapter/run **conflict is rejected**, never guessed. `CHECK (book_id IS NOT NULL)` and the book FK are added `NOT VALID` — enforcing every future write immediately while tolerating pre-existing rows, so a boot never bricks and no new orphan can be created. Once no `NULL`-book rows remain, the constraints are validated and the column promoted to physical `NOT NULL` (self-healing on any later boot). A `(book_id, status)` index backs the hot per-book queue queries.
- **Execution** (`worker.py`): `claim_one_job` filters `book_id IS NOT NULL` in the `WHERE`, so an ownerless job is never claimed by any worker regardless of deploy timing, and can't head-of-line-block the FIFO order. An unresolved **live** legacy job is moved to the terminal `JobStatus.QUARANTINED` state — never claimable, never retryable, never clearable — and surfaced via `GET /jobs/integrity-holds`.
- **Static** (`tests/test_job_scope_fitness.py`): an `ast`-based fitness test fails if any module reintroduces the `Job`->`Run` routing scope, so the seam can't erode. `scope_jobs_to_book` and the `JobStatus` classification sets (`CLAIMABLE`/`RETRYABLE`/`DISMISSABLE`/`RETENTION_PURGEABLE`/`INTEGRITY_HELD`/`TERMINAL`) live in `shared/job_policy.py` as the single source of truth.

## Alternatives considered

- **Centralize a permanent dual-key `(book_id OR run_id)` predicate** — leaves the `book_id` guarantee unenforced and keeps `run_id` load-bearing for routing forever. Rejected: it manages the smell instead of removing it.
- **Delete unresolvable legacy rows** — rejected as unproven; ownerless rows are quarantined/reported, never silently dropped.
- **Hard-fail the boot migration on any orphan** — rejected: a stray legacy row would take the single-container prod app offline on deploy. The `NOT VALID` + guarded-promote path preserves availability while still forbidding new orphans.

## Consequences

`run_id` remains only for telemetry provenance and token-budget carry-forward on requeue; the legacy `run_id` book-resolution fallback in `context/resolve.py` was deleted. Integrity holds are reported (structured `error` log every boot while held; an Activity transition only on fingerprint change, tracked by the singleton `JobIntegrityState`) so "eventually promoted" is operationally owned, not silent.
