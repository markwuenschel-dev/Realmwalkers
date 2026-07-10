"""Fitness check: every `_COLUMN_ADDS` entry names a real model table + column.

Schema evolution is hand-maintained (no Alembic): `migrations._COLUMN_ADDS` lists `ADD COLUMN`
statements applied on top of `create_all`. This guards the STALE-entry direction — a migration line
that references a table/column the ORM no longer defines (renamed or removed) — which would otherwise
sit unnoticed. Pure static parse of the DDL against `Base.metadata`; no database.

Not covered (needs a schema baseline — tracked as a follow-up): the forward direction, a NEW model
column forgotten from `_COLUMN_ADDS`. `create_all` builds every current column on a fresh DB, so with no
older snapshot nothing here can distinguish an original column from a later add — which is exactly the
drift that boots green on CI but throws `UndefinedColumn` against the persistent production Postgres.
"""

from __future__ import annotations

import re

from dominion.shared.migrations import _COLUMN_ADDS
from dominion.shared.models import Base

_ADD_COLUMN = re.compile(
    r"ALTER TABLE (?P<table>\w+) ADD COLUMN (?:IF NOT EXISTS )?(?P<col>\w+)\b",
    re.IGNORECASE,
)


def _model_columns() -> dict[str, set[str]]:
    return {name: {c.name for c in table.columns} for name, table in Base.metadata.tables.items()}


def test_every_column_add_matches_a_model_column():
    models = _model_columns()
    problems: list[str] = []
    for ddl in _COLUMN_ADDS:
        m = _ADD_COLUMN.match(ddl.strip())
        assert m is not None, f"unparseable _COLUMN_ADDS entry: {ddl!r}"
        table, col = m["table"], m["col"]
        if table not in models:
            problems.append(f"{table}.{col}: table absent from Base.metadata")
        elif col not in models[table]:
            problems.append(f"{table}.{col}: column not defined on the model")
    assert not problems, "stale _COLUMN_ADDS entries (renamed/removed in models.py?): " + "; ".join(problems)
