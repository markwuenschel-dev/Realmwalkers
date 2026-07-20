"""Fitness check: no model column has drifted AHEAD of the migrations without an `ADD COLUMN`.

This closes the forward-drift gap that its sibling `test_migration_column_parity.py` documents but
cannot cover on its own. That guard only catches the STALE direction (a `_COLUMN_ADDS` line naming a
table/column the ORM dropped). The FORWARD direction is the dangerous one:

  `create_all` NEVER alters an existing table — it only provisions brand-new ones. So a nullable column
  added to an EXISTING table in models.py but forgotten from `migrations._COLUMN_ADDS` builds fine on a
  fresh `create_all` test DB (every current column is created from scratch) and boots GREEN on CI —
  then throws `UndefinedColumn` against the persistent production Postgres, whose older table never got
  the ALTER.

To tell an "original" column from a later forgotten add, we need a record of what the real database
already has. That record is `tests/schema_baseline.json`, seeded by `scripts/snapshot_schema.py`.

PROD-SEED REQUIREMENT (critical): the baseline MUST be reflected from a prod-equivalent (post-real-
deploy) DB, not a throwaway `create_all` DB — a create_all snapshot would already contain any
forgotten-ALTER column and would bake that drift in as "fine", rubber-stamping the exact bug this gate
exists to catch. Because CI/this swarm has no prod DB, the baseline is intentionally NOT committed:
until an operator seeds it, this test SKIPS (keeping `just verify` green) rather than asserting against
a wrong-world snapshot. This test itself needs NO database — it parses migrations.py, reads
Base.metadata, and reads the JSON.

The drift predicate, for every (table, col) in Base.metadata:
    FAIL iff  table in baseline  AND  col not in baseline[table]  AND  (table, col) not in `added`
where `added` = every `ADD COLUMN` target across `_COLUMN_ADDS` + `_EXTRA_DDL`. New tables (absent from
the baseline) are skipped — create_all provisions them wholesale on the persistent DB too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dominion.shared.migrations import _COLUMN_ADDS, _EXTRA_DDL

# Reuse the parity guard's DDL parser (test_migration_column_parity.py:21-24) and its model-column
# reflection (test_migration_column_parity.py:27-28) verbatim — one source of truth for how an
# `ADD COLUMN` line is parsed and how Base.metadata columns are read.
from tests.test_migration_column_parity import _ADD_COLUMN, _model_columns

_BASELINE_PATH = Path(__file__).parent / "schema_baseline.json"

# Deliberate exceptions: (table, col) pairs that legitimately live ONLY in create_all and are
# knowingly absent from BOTH the prod baseline and `_COLUMN_ADDS`. Empty by default — the normal fix
# for real drift is a new `_COLUMN_ADDS` line or a re-seed, NOT an allowlist entry. Add here only for a
# column you have deliberately decided create_all should own on fresh DBs while the prod baseline lags,
# and document why inline.
_CREATE_ALL_ONLY: set[tuple[str, str]] = set()


def _migration_added_columns() -> set[tuple[str, str]]:
    """Every (table, col) a migration explicitly ADDs — from `_COLUMN_ADDS` (each line is one add) and
    any `ADD COLUMN` embedded in `_EXTRA_DDL` (which also carries ALTER COLUMN / ADD CONSTRAINT / CREATE
    INDEX that the regex correctly ignores)."""
    added: set[tuple[str, str]] = set()
    for ddl in _COLUMN_ADDS:
        m = _ADD_COLUMN.match(ddl.strip())
        assert m is not None, f"unparseable _COLUMN_ADDS entry: {ddl!r}"
        added.add((m["table"], m["col"]))
    for ddl in _EXTRA_DDL:
        for m in _ADD_COLUMN.finditer(ddl):
            added.add((m["table"], m["col"]))
    return added


def test_no_forward_drift_between_models_and_prod_baseline():
    if not _BASELINE_PATH.exists():
        pytest.skip(
            "forward-drift baseline not seeded; run scripts/snapshot_schema.py against a "
            "prod-equivalent DB — see docstring"
        )

    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_tables: dict[str, list[str]] = baseline["tables"]

    added = _migration_added_columns()
    models = _model_columns()

    drift: list[str] = []
    for table, cols in models.items():
        if table not in baseline_tables:
            # Brand-new table: create_all provisions it on the persistent DB too. Not drift.
            continue
        baseline_cols = set(baseline_tables[table])
        for col in sorted(cols):
            if col in baseline_cols:
                continue  # already present in the real DB
            if (table, col) in added:
                continue  # a migration ADDs it on top of create_all
            if (table, col) in _CREATE_ALL_ONLY:
                continue  # deliberate, documented exception
            drift.append(f"{table}.{col}")

    assert not drift, (
        "forward-drift: model column(s) present in Base.metadata but absent from BOTH the prod baseline "
        "AND every migration ADD COLUMN — these boot green on a fresh create_all test DB and throw "
        "UndefinedColumn against the persistent production Postgres:\n  "
        + "\n  ".join(drift)
        + "\nFor each: add an `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` to `_COLUMN_ADDS`, or re-seed "
        "the baseline from a prod-equivalent DB after deploy."
    )
