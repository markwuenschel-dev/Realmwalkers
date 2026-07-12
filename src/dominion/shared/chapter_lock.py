"""Per-chapter workflow coordination lock (ADR 0028).

Every authority-changing operation on a chapter — source-prose mutation (import overwrite, inbox
hand-edit, delete, reparent, redraft/revert/repair), lazy fingerprint-validate + revision-Job mint,
the adoption worker's compare-and-set ChapterPacket publish, ChapterPacket propose/replace/approve/
supersede, and a target-ScenePacket approval that resumes a waiting request — must serialize per
chapter, or an import/edit can race between fingerprint validation and job creation, or an author can
approve a stale ChapterPacket while adoption publishes its replacement.

This is a Postgres TRANSACTION-level advisory lock: it waits on conflict and releases at transaction
end (no leak on rollback/crash). It coordinates the cross-table invariant only; it does NOT replace
the `FOR UPDATE SKIP LOCKED` queue-claim locks or the ordinary row locks on the rows actually changed.

Mandatory protocol (any mutation path that bypasses this bypasses the guarantee):
    1. Locate the chapter only; make no decision from that read.
    2. `await acquire_chapter_workflow_lock(session, chapter_id)`.
    3. Reload the mutable rows under normal row locks as needed.
    4. Recompute/validate the fingerprint, then write and commit.

The adoption worker must NOT hold this across evidence/author model calls — take it only in the short
final publish transaction, lock the adoption row, recompute the fingerprint, and publish only if it
still matches (`invalidated` wins over a late worker completion).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

# The advisory-lock key namespace. The namespace prevents accidental collision with any future
# advisory-lock domain; a 64-bit hash collision merely serializes two unrelated chapters (safe, slower).
_LOCK_KEY_PREFIX = "dominion:chapter-workflow:"

# Default wait ceiling for a request-path acquisition, so a stalled transaction cannot hang an API
# request indefinitely. Workers pass a longer/None timeout and retry with jitter.
DEFAULT_LOCK_TIMEOUT_MS = 4000

# Postgres SQLSTATE raised when lock_timeout fires while waiting on the advisory lock.
_LOCK_NOT_AVAILABLE = "55P03"


class ChapterWorkflowBusy(Exception):
    """The per-chapter workflow lock could not be acquired within lock_timeout. Retryable: the API maps
    it to a 409 `chapter_workflow_busy`; a worker retries with jitter."""

    def __init__(self, chapter_id: uuid.UUID) -> None:
        self.chapter_id = chapter_id
        super().__init__(f"chapter {chapter_id} workflow is busy; retry")


def _is_lock_timeout(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return sqlstate == _LOCK_NOT_AVAILABLE


async def acquire_chapter_workflow_lock(
    session: AsyncSession,
    chapter_id: uuid.UUID,
    *,
    timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> None:
    """Take the exclusive per-chapter advisory lock for the CURRENT transaction.

    Blocks until granted or, if `timeout_ms` is set, until lock_timeout fires — in which case
    `ChapterWorkflowBusy` is raised (retryable). Pass `timeout_ms=None` to wait indefinitely (only for
    background workers that will otherwise retry). The lock auto-releases at transaction end.
    """
    if timeout_ms is not None:
        # set_config(..., is_local=true) == SET LOCAL: scoped to this transaction, so it never leaks to
        # a pooled connection's next transaction. Value is milliseconds (lock_timeout's default unit).
        await session.execute(
            text("SELECT set_config('lock_timeout', :ms, true)"),
            {"ms": str(int(timeout_ms))},
        )
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{_LOCK_KEY_PREFIX}{chapter_id}"},
        )
    except (OperationalError, DBAPIError) as exc:
        if _is_lock_timeout(exc):
            raise ChapterWorkflowBusy(chapter_id) from exc
        raise
