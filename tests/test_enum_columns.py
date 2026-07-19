"""Write-boundary validation for enum-valued columns (ENUM-STR A′).

Unit tests (no DB) cover the pure checker + registry integrity; one DB-gated test proves the
`before_flush` guard actually rejects an off-vocabulary write at flush time.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect

from dominion.shared import enum_columns as ec
from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import Critique, RepairTask, ScenePacket

# --- pure checker (no DB) -------------------------------------------------------------------------


def test_check_value_rejects_off_vocab():
    with pytest.raises(ec.EnumValueError):
        ec.check_value(ScenePacket, "status", "definitely_not_a_status")


def test_check_value_accepts_vocab_and_none():
    ec.check_value(ScenePacket, "status", ScenePacketStatus.APPROVED.value)  # no raise
    ec.check_value(ScenePacket, "status", None)  # nullability is the ORM's concern, not the vocab's


def test_unregistered_column_is_not_checked():
    # A column absent from the registry is a no-op, not an error.
    ec.check_value(Critique, "severity", "hard")  # legacy alias, deliberately KNOWN_UNREGISTERED


def test_reviewer_is_deferred_pending_vocab_reconciliation():
    # ReviewerKind omits live production values ('length', 'budget', 'scene_fidelity'), so `reviewer`
    # is deliberately KNOWN_UNREGISTERED — validating it against ReviewerKind would reject real critiques.
    assert (Critique, "reviewer") in ec.KNOWN_UNREGISTERED
    ec.check_value(Critique, "reviewer", "budget")  # no raise: an unregistered column is a no-op


# --- registry integrity (no DB) -------------------------------------------------------------------


def test_registry_columns_exist_and_are_mapped():
    for (model, attr), allowed in ec.ENUM_COLUMNS.items():
        cols = inspect(model).columns.keys()
        assert attr in cols, f"{model.__name__}.{attr} is not a mapped column"
        assert allowed, f"{model.__name__}.{attr} has an empty allowed-set"


def test_registered_and_known_unregistered_are_disjoint_and_reasoned():
    reg = set(ec.ENUM_COLUMNS)
    unreg = ec.KNOWN_UNREGISTERED
    assert reg.isdisjoint(unreg), "a column is both registered and known-unregistered"
    for key, reason in unreg.items():
        model, attr = key
        assert attr in inspect(model).columns.keys(), f"{model.__name__}.{attr} is not a mapped column"
        assert reason and isinstance(reason, str), f"{key} lacks an exclusion reason"


def test_registered_scalar_defaults_are_in_vocab():
    for (model, attr), allowed in ec.ENUM_COLUMNS.items():
        col = inspect(model).columns[attr]
        default = getattr(col.default, "arg", None)
        if isinstance(default, str):
            assert default in allowed, f"{model.__name__}.{attr} default {default!r} not in vocab"


# --- flush integration (DB-gated) -----------------------------------------------------------------


async def test_before_flush_rejects_off_vocab_status(db_factory):
    """The listener fires inside flush() and raises before the INSERT is emitted (dangling FKs never
    reached — the enum guard runs first in before_flush)."""
    ec.install()  # idempotent; guarantees the listener is active regardless of import order
    async with db_factory() as s:
        s.add(
            RepairTask(
                production_run_id=uuid4(),
                chapter_id=uuid4(),
                repair_kind="fidelity",
                authority_level="span_only",  # valid
                status="__not_a_status__",  # invalid
                instructions="x",
            )
        )
        with pytest.raises(ec.EnumValueError):
            await s.flush()


async def test_before_flush_passes_valid_enums(db_factory):
    """Valid enum values are NOT rejected by the guard (a later FK error from dangling ids is fine)."""
    ec.install()
    async with db_factory() as s:
        s.add(
            RepairTask(
                production_run_id=uuid4(),
                chapter_id=uuid4(),
                repair_kind="fidelity",
                authority_level="span_only",
                status="queued",
                instructions="x",
            )
        )
        try:
            await s.flush()
        except ec.EnumValueError:
            pytest.fail("valid enum values must not be rejected by the write-boundary guard")
        except Exception:
            pass  # dangling FK / other DB errors are expected and not our concern here
