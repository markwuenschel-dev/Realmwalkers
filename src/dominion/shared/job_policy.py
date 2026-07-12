"""The single source of truth for (a) how `JobStatus` values classify a job and (b) how a Job query
is scoped to a book. Import these instead of hand-writing `== JobStatus.X` selection checks or
`run_id`-based book scopes.

Ownership invariant (ADR 0027): a Job belongs to a *book* via its `book_id`; `run_id` is provenance
only, never a routing/scoping key. `scope_jobs_to_book` therefore keys solely on `book_id`. A static
fitness test (`tests/test_job_scope_fitness.py`) forbids the legacy `Job`->`Run` routing scope outside
this module and the integrity/migration modules.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select

from dominion.shared.enums import JobStatus
from dominion.shared.models import Job

# --- Status classification: the ONLY sanctioned selection policies. -----------------------------
CLAIMABLE = frozenset({JobStatus.QUEUED})  # a worker may claim and run
RETRYABLE = frozenset({JobStatus.FAILED})  # retry-failed may requeue
DISMISSABLE = frozenset({JobStatus.FAILED})  # clear-failed (user dismiss) — distinct policy from retention
RETENTION_PURGEABLE = frozenset({JobStatus.DONE})  # retention / clear-finished — DONE only, never FAILED
INTEGRITY_HELD = frozenset({JobStatus.QUARANTINED})  # withheld from execution AND all failure controls
TERMINAL = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.QUARANTINED})


def scope_jobs_to_book(stmt: Select, book_id: uuid.UUID | None) -> Select:
    """Scope a Job query to one book. Single key: a Job belongs to a book via `book_id` (ADR 0027) —
    no `run_id` fallback. A NULL-book legacy row is backfilled, quarantined, or visible only to the
    integrity audit; it does not belong in a per-book result. `book_id is None` means 'all books' (the
    unscoped Desk/terminal view)."""
    if book_id is None:
        return stmt
    return stmt.where(Job.book_id == book_id)
