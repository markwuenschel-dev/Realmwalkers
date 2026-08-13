"""Forward-drift gate for `chapter_packets` — every ORM column is provisioned on a PERSISTENT database.

Why this file exists at all. Schema evolution here is hand-maintained: `create_all` NEVER alters an
existing table, so a new ORM column on a pre-existing table must ALSO appear as an `ADD COLUMN` in
`migrations._COLUMN_ADDS`. Forgetting that boots green on CI and on every test run — a fresh
`create_all` database builds every current column regardless — and then throws `UndefinedColumn`
against the persistent production Postgres. That is the single most dangerous class of change in this
repo, and #261 added TEN columns to `chapter_packets`, the table the whole amendment lifecycle turns on.

The two existing guards do not cover this direction, by their own admission:
  * `test_migration_column_parity.py:9-13` — covers only the STALE direction (a migration line naming a
    column the ORM no longer defines) and states plainly that the forward direction "needs a schema
    baseline — tracked as a follow-up".
  * `test_migration_forward_drift.py` — DOES check the forward direction, but SKIPS unless an operator has
    seeded `tests/schema_baseline.json` from a prod-equivalent DB, and that file is deliberately not
    committed. A skipped gate is a green gate.

So this closes the gap for ONE table, without inventing a repo-wide baseline: `_ORIGINAL_COLUMNS` is the
set `chapter_packets` had before amendment mode, transcribed from the live pre-change schema of both
deployed clusters (10 columns; verified by direct `information_schema` query, not from a model file). Any
ORM column outside that set is by definition a later addition and MUST be in the migration list. This
needs no database and no baseline file, so it cannot skip.
"""

from __future__ import annotations

import re

from dominion.shared.migrations import _COLUMN_ADDS, _EXTRA_DDL
from dominion.shared.models import Base

#: `chapter_packets` as it stood BEFORE #261 — the 10 columns `create_all` provisioned originally and
#: which therefore need no ALTER. Transcribed from a live `information_schema.columns` read of the
#: deployed schema before the amendment columns were added. Frozen on purpose: this is a historical
#: fact, so it must NEVER be updated to make a failure go away. A new column belongs in `_COLUMN_ADDS`.
_ORIGINAL_COLUMNS = frozenset(
    {
        "id",
        "book_id",
        "chapter_id",
        "status",
        "confidence",
        "qa_verdict",
        "qa_warnings",
        "body",
        "open_questions",
        "created_at",
    }
)

# Same parser shape as test_migration_column_parity.py:21-24, widened to also see the `ADD COLUMN`s that
# live in _EXTRA_DDL (test_migration_forward_drift.py counts both as "provisioned", and so must this).
_ADD_COLUMN = re.compile(
    r"ALTER TABLE (?P<table>\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(?P<col>\w+)\b",
    re.IGNORECASE,
)


def _provisioned_columns(table: str) -> set[str]:
    """Every column of `table` that some ADD COLUMN statement provisions on an existing database."""
    out: set[str] = set()
    for ddl in (*_COLUMN_ADDS, *_EXTRA_DDL):
        for m in _ADD_COLUMN.finditer(ddl):
            if m["table"].lower() == table:
                out.add(m["col"])
    return out


def test_every_new_chapter_packet_column_is_provisioned_on_an_existing_db():
    """A ChapterPacket ORM column that is neither original nor ADDed would `UndefinedColumn` in prod.

    This is the assertion that would have caught a forgotten ALTER for any of the ten #261 columns, and
    will catch the eleventh. It is deliberately table-scoped rather than repo-wide: a repo-wide version
    needs the prod baseline that `test_migration_forward_drift.py` waits on.
    """
    orm_columns = {c.name for c in Base.metadata.tables["chapter_packets"].columns}
    provisioned = _provisioned_columns("chapter_packets")
    missing = sorted(orm_columns - _ORIGINAL_COLUMNS - provisioned)
    assert not missing, (
        "chapter_packets columns defined on the model but never ADDed for an existing database "
        f"(create_all will not alter a persistent prod table, so these would raise UndefinedColumn): {missing}. "
        "Add an 'ALTER TABLE chapter_packets ADD COLUMN IF NOT EXISTS ...' line to migrations._COLUMN_ADDS."
    )


def test_the_original_column_set_still_matches_the_model():
    """Inertness canary. If a column were REMOVED from the model but left in `_ORIGINAL_COLUMNS`, the
    subtraction above would quietly mask a real forgotten ALTER for some other column. Pin that the
    original set is still a subset of what the model defines, so this guard cannot rot into a rubber
    stamp the way an unmaintained allowlist does."""
    orm_columns = {c.name for c in Base.metadata.tables["chapter_packets"].columns}
    vanished = sorted(_ORIGINAL_COLUMNS - orm_columns)
    assert not vanished, (
        f"columns in _ORIGINAL_COLUMNS no longer exist on the ChapterPacket model: {vanished}. "
        "Do not edit _ORIGINAL_COLUMNS to fix this — it records a historical schema state. Remove the "
        "column's stale _COLUMN_ADDS line instead (test_migration_column_parity.py covers that direction)."
    )


def test_the_amendment_columns_are_all_covered():
    """Names the ten #261 columns explicitly, so a future refactor that drops one from `_COLUMN_ADDS`
    fails with a precise message rather than an empty-set assertion that reads as "nothing to check"."""
    provisioned = _provisioned_columns("chapter_packets")
    expected = {
        "supersedes_packet_id",
        "superseded_by_packet_id",
        "superseded_at",
        "origin_mode",
        "approval_source",
        "approved_at",
        "source_fingerprint",
        "evidence_manifest_fingerprint",
        "origin_adoption_id",
        "amendment_scope",
    }
    assert expected <= provisioned, (
        f"amendment columns missing from the migration list: {sorted(expected - provisioned)}"
    )
