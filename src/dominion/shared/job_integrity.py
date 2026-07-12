"""Job->book ownership integrity (ADR 0027): backfill, conflict rejection, live-job quarantine, and
constraint promotion for the `book_id` ownership invariant.

Lives in `shared` (not `workers`) so the migration layer can call it without importing upward. Works
in raw SQL against an `AsyncConnection`, so it runs identically from the boot migration and the
`dominion-audit` CLI.

Two entry points:
- `inspect_job_ownership(conn)`  — READ-ONLY classification of any `book_id IS NULL` rows.
- `reconcile_job_ownership(conn)` — idempotent backfill + quarantine + `NOT VALID` constraints +
  guarded promotion to physical `NOT NULL`.

`apply_lightweight_migrations` calls `reconcile` at boot. The CLI calls `inspect` by default and
`reconcile` only with `--apply`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# `book_id` is authoritative and taken from the non-null `chapters.book_id`; a job's `run_id` resolves
# to a book only as a fallback and MUST NOT override a chapter that disagrees (that is corruption, not a
# tie to break). These queries encode exactly that precedence.

_CLASSIFY = text(
    """
    SELECT
      count(*) FILTER (WHERE book_id IS NULL)                                              AS null_total,
      count(*) FILTER (WHERE book_id IS NULL AND status = 'queued')                        AS null_queued,
      count(*) FILTER (WHERE book_id IS NULL AND status = 'running')                       AS null_running,
      count(*) FILTER (WHERE book_id IS NULL AND status IN ('done','failed','quarantined')) AS null_terminal,
      count(*) FILTER (WHERE status = 'quarantined')                                        AS quarantined_total
    FROM jobs
    """
)

_CONFLICTS = text(
    """
    SELECT count(*) FROM jobs j
    JOIN chapters c ON c.id = j.chapter_id
    JOIN runs r     ON r.id = j.run_id
    WHERE j.book_id IS NULL AND c.book_id <> r.book_id
    """
)

# The operator problem: everything blocking full constraint promotion — quarantined live jobs AND any
# still-unresolved (terminal/conflict) NULL-book row. Quarantine leaves `book_id` NULL, so a plain
# `book_id IS NULL` already covers the quarantined rows; the OR keeps intent explicit.
_HOLD_ROWS = text(
    """
    SELECT id, status, chapter_no, scene_no, last_error
    FROM jobs
    WHERE status = 'quarantined' OR book_id IS NULL
    ORDER BY id
    """
)

# 1) Backfill from the authoritative chapter link, but ONLY when no run disagrees (reject conflicts).
_BACKFILL_FROM_CHAPTER = text(
    """
    UPDATE jobs j SET book_id = c.book_id
    FROM chapters c
    WHERE j.book_id IS NULL AND j.chapter_id = c.id
      AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.id = j.run_id AND r.book_id <> c.book_id)
    """
)

# 2) Backfill from run provenance only when there is no chapter link to be authoritative or conflict.
_BACKFILL_FROM_RUN = text(
    """
    UPDATE jobs j SET book_id = r.book_id
    FROM runs r
    WHERE j.book_id IS NULL AND j.run_id = r.id AND j.chapter_id IS NULL
    """
)

# 3) Quarantine unresolved LIVE jobs so an ownerless job can never run. Terminal rows are retained as-is.
_QUARANTINE_LIVE = text(
    """
    UPDATE jobs
    SET status = 'quarantined', finished_at = now(), last_error = 'INTEGRITY_OWNERLESS'
    WHERE book_id IS NULL AND status IN ('queued','running')
    """
)

# 4) NOT VALID constraints: enforce EVERY future write immediately while tolerating pre-existing rows,
#    so boot never bricks and no new orphan can be created. Guarded for idempotency (no ADD IF NOT EXISTS
#    for constraints in Postgres). On a fresh DB create_all already made `jobs_book_id_fkey`, so the FK
#    guard finds it and skips.
_ADD_CHECK_NOT_VALID = text(
    """
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jobs_book_id_not_null') THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_book_id_not_null CHECK (book_id IS NOT NULL) NOT VALID;
      END IF;
    END $$;
    """
)

_ADD_FK_NOT_VALID = text(
    """
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jobs_book_id_fkey') THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_book_id_fkey FOREIGN KEY (book_id) REFERENCES books(id) NOT VALID;
      END IF;
    END $$;
    """
)

# 5) Promote to physical NOT NULL + validate the constraints, but ONLY when zero NULL-book rows remain.
#    Self-healing: lands automatically on the first boot after the last hold is resolved.
_PROMOTE_IF_CLEAN = text(
    """
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM jobs WHERE book_id IS NULL) THEN
        ALTER TABLE jobs VALIDATE CONSTRAINT jobs_book_id_not_null;
        ALTER TABLE jobs VALIDATE CONSTRAINT jobs_book_id_fkey;
        ALTER TABLE jobs ALTER COLUMN book_id SET NOT NULL;
      END IF;
    END $$;
    """
)

_IS_PROMOTED = text(
    "SELECT a.attnotnull FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
    "WHERE c.relname = 'jobs' AND a.attname = 'book_id'"
)


@dataclass
class IntegrityReport:
    null_book_total: int = 0
    null_queued: int = 0
    null_running: int = 0
    null_terminal: int = 0
    quarantined_total: int = 0
    conflicts: int = 0
    backfilled_from_chapter: int = 0
    backfilled_from_run: int = 0
    newly_quarantined: int = 0
    hold_count: int = 0  # quarantined-live ∪ unresolved NULL-book rows — the promotion blockers
    promoted: bool = False  # book_id is physically NOT NULL after this run
    fingerprint: str = ""
    holds: list[dict] = field(default_factory=list)

    @property
    def has_holds(self) -> bool:
        return self.hold_count > 0


def _fingerprint(hold_rows: list[dict]) -> str:
    """Stable hash of the current holds (id+status), so the transition record fires only on change —
    including back to the empty-holds fingerprint when everything resolves."""
    canonical = ";".join(f"{r['id']}:{r['status']}" for r in hold_rows)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


async def _hold_rows(conn: AsyncConnection) -> list[dict]:
    rows = (await conn.execute(_HOLD_ROWS)).mappings().all()
    out: list[dict] = []
    for r in rows:
        reason = "quarantined_ownerless" if r["status"] == "quarantined" else "unresolved_owner"
        out.append(
            {
                "id": str(r["id"]),
                "status": r["status"],
                "reason": reason,
                "chapter_no": r["chapter_no"],
                "scene_no": r["scene_no"],
                "last_error": r["last_error"],
            }
        )
    return out


async def _fill_classification(conn: AsyncConnection, report: IntegrityReport) -> None:
    c = (await conn.execute(_CLASSIFY)).mappings().one()
    report.null_book_total = c["null_total"]
    report.null_queued = c["null_queued"]
    report.null_running = c["null_running"]
    report.null_terminal = c["null_terminal"]
    report.quarantined_total = c["quarantined_total"]
    report.conflicts = (await conn.execute(_CONFLICTS)).scalar_one()
    report.holds = await _hold_rows(conn)
    report.hold_count = len(report.holds)
    report.fingerprint = _fingerprint(report.holds)
    report.promoted = bool((await conn.execute(_IS_PROMOTED)).scalar_one())


async def inspect_job_ownership(conn: AsyncConnection) -> IntegrityReport:
    """Read-only: classify NULL-book rows and enumerate current integrity holds. Writes nothing."""
    report = IntegrityReport()
    await _fill_classification(conn, report)
    return report


async def reconcile_job_ownership(conn: AsyncConnection) -> IntegrityReport:
    """Idempotent: backfill (chapter, then run; reject conflicts) -> quarantine live orphans -> add
    NOT VALID constraints -> promote to physical NOT NULL once no NULL-book rows remain. Returns the
    post-reconcile report."""
    report = IntegrityReport()
    report.backfilled_from_chapter = (await conn.execute(_BACKFILL_FROM_CHAPTER)).rowcount
    report.backfilled_from_run = (await conn.execute(_BACKFILL_FROM_RUN)).rowcount
    report.newly_quarantined = (await conn.execute(_QUARANTINE_LIVE)).rowcount
    await conn.execute(_ADD_CHECK_NOT_VALID)
    await conn.execute(_ADD_FK_NOT_VALID)
    await conn.execute(_PROMOTE_IF_CLEAN)
    await _fill_classification(conn, report)
    return report
