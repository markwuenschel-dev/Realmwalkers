"""Write-boundary validation for enum-valued columns (ENUM-STR A').

The enum vocabularies in `enums.py` are persisted as bare `Text` — no SQLAlchemy `Enum`, no DB CHECK —
so an off-vocabulary value (a typo, a drifted writer, a bad migration) persists silently and reads back
as canonical. A CI test over enum *literals* catches inventory drift but does nothing about a bad value
computed at runtime. This module closes that gap at the ORM write boundary: one `before_flush` listener
validates every *registered* enum column against its allowed vocabulary and raises before the
INSERT/UPDATE SQL is emitted.

Deliberately a registry, not a blanket sweep. A column joins `ENUM_COLUMNS` once its LIVE vocabulary is
confirmed to match its enum; a column with a legacy alias or a distinct vocabulary is recorded in
`KNOWN_UNREGISTERED` with a reason, so every enum column is an explicit decision rather than an omission.
A DB-level CHECK / `Enum` migration is intentionally out of scope here — it would break on the very legacy
values documented below (e.g. `reviewer='scene_fidelity'`, `severity='hard'`) — and belongs in its own
deliberate, data-audited effort. This guard is the low-risk, high-leverage first step.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum

from sqlalchemy import event
from sqlalchemy.orm import Session

from dominion.shared import enums
from dominion.shared.models import Base, Critique, ProductionRun, RepairTask, Scene, ScenePacket


class EnumValueError(ValueError):
    """An enum-valued column was assigned a value outside its declared vocabulary."""


def _values(enum_cls: type[StrEnum]) -> frozenset[str]:
    return frozenset(member.value for member in enum_cls)


# (model, attribute) -> the exact set of allowed string values for that column. Add a column here only
# once its LIVE vocabulary is confirmed to match (data-scrub before registering, not enum-as-written).
ENUM_COLUMNS: dict[tuple[type[Base], str], frozenset[str]] = {
    (Scene, "status"): _values(enums.SceneStatus),
    (ScenePacket, "status"): _values(enums.ScenePacketStatus),
    (ProductionRun, "status"): _values(enums.ProductionRunStatus),
    (RepairTask, "status"): _values(enums.RepairTaskStatus),
    (RepairTask, "authority_level"): _values(enums.RepairAuthorityLevel),
}

# Enum-valued columns deliberately NOT yet validated, each with a reason. Recording them here (rather
# than silently omitting) keeps every enum column an explicit decision. Resolve the reason, confirm the
# live vocabulary, then move the column up into ENUM_COLUMNS.
KNOWN_UNREGISTERED: dict[tuple[type[Base], str], str] = {
    (Critique, "reviewer"): (
        "ReviewerKind {continuity, combat, sensory, dialogue, pacing, voice} omits values written in "
        "production: 'length' (pipeline.py:216), 'budget' (pipeline.py:312), 'scene_fidelity' "
        "(production_repair.py:1680). Register once the reviewer vocabulary is reconciled (candidate "
        "REVIEWER-KIND) — DO NOT register against ReviewerKind as-is; it would reject live critiques."
    ),
    (Critique, "severity"): (
        "legacy rows/JSON snapshots may store 'hard' (pre-unification spelling of BLOCK); register once "
        "the severity vocabulary is unified behind one predicate (candidate SEV-ALIAS)."
    ),
}


def _rules_for(model: type) -> Iterator[tuple[str, frozenset[str]]]:
    for (registered_model, attr), allowed in ENUM_COLUMNS.items():
        if registered_model is model:
            yield attr, allowed


def check_value(model: type[Base], attr: str, value: object) -> None:
    """Raise `EnumValueError` if `value` is a non-null string outside the column's vocabulary.

    `None` is never rejected here — column nullability is the ORM/DB's concern, not the vocabulary's.
    An unregistered column is a no-op. Pure and DB-free: the `before_flush` listener is a thin loop
    over this.
    """
    allowed = ENUM_COLUMNS.get((model, attr))
    if allowed is None or value is None:
        return
    if value not in allowed:
        raise EnumValueError(
            f"{model.__name__}.{attr} = {value!r} is not a valid value (expected one of {sorted(allowed)})"
        )


def validate_instance(obj: Base) -> None:
    """Validate every registered enum column on one ORM instance."""
    for attr, _allowed in _rules_for(type(obj)):
        check_value(type(obj), attr, getattr(obj, attr))


def _before_flush(session: Session, flush_context: object, instances: Iterable[object] | None) -> None:
    for obj in list(session.new) + list(session.dirty):
        validate_instance(obj)


_INSTALLED = False


def install() -> None:
    """Register the write-boundary validator on the global Session class. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Session, "before_flush", _before_flush)
    _INSTALLED = True


install()
