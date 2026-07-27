"""ADR-0032 W1 — the adoption-entry seam: operation policy, liveness, entry_effect, collision recovery,
telemetry placement, and the constructor guards.

The behaviour-PRESERVING extraction is proven by the existing endpoint oracles (test_adoption_start.py /
test_adoption_reauthor.py) staying green. This file proves the NEW W1 behaviour the seam adds on top:

  * operation policy — START/REAUTHOR mint `operator_independent`; an unwired operation fails closed;
  * monotonic liveness merge — an operator command upgrades a `request_bound` active row, never downgrades;
  * entry_effect — created / promoted / unchanged, per the precise D11/D12 definitions;
  * D3 collision recovery — a stale-read TOCTOU race reconciles to the winner (no duplicate row), emits the
    high-severity collision telemetry IMMEDIATELY, and never fabricates a second active adoption;
  * telemetry split — the collision event is emitted inside the seam (survives rollback); the success
    `adoption_entry_transition` is emitted by the committing wrapper, and never for an inert reuse;
  * constructor guards — only the seam constructs `ImportAdoption(...)`; no raw/bulk insert bypasses it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dominion.shared import adoption_entry
from dominion.shared.adoption_entry import (
    IncompatibleAdoptionEntry,
    ensure_import_adoption,
)
from dominion.shared.enums import AdoptionOperation, EntryEffect, EntryIntent, LivenessBasis
from dominion.shared.models import Book, Chapter, ImportAdoption

_SRC = Path(__file__).resolve().parents[1] / "src" / "dominion"
_SEAM_REL = "shared/adoption_entry.py"


class _RecordingLog:
    """A stand-in for the seam's structlog logger that records events in order, so a test can assert what
    telemetry was emitted (and that collision telemetry is a LOG, not a rolled-back DB row)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.events.append(("warning", event, kw))

    def info(self, event: str, **kw) -> None:
        self.events.append(("info", event, kw))


async def _seed(s, *, pov: str = "Marcus") -> tuple[Book, Chapter]:
    book = Book(title="ADR-0032 W1 seam")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov)
    s.add(ch)
    await s.flush()
    return book, ch


async def _count(s) -> int:
    return (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one()


# --------------------------------------------------------------------------- operation policy + effects


async def test_start_creates_operator_independent_queued(db_factory):
    """OPERATOR_START on an empty chapter creates a `queued` row with `operator_independent` liveness and
    reports effect=created."""
    async with db_factory() as s:
        _, ch = await _seed(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s2:
        result = await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
        assert result.effect is EntryEffect.CREATED
        assert result.to_status == "queued"
        assert result.liveness_basis == "operator_independent"
        assert result.trigger == "operator_start"

    async with db_factory() as s3:
        row = (await s3.execute(select(ImportAdoption))).scalar_one()
        assert row.status == "queued"
        assert row.liveness_basis == "operator_independent"


async def test_start_on_already_queued_operator_row_is_unchanged(db_factory):
    """A second START over an already-`queued`, already-`operator_independent` row changes nothing —
    effect=unchanged (an inert reuse emits no transition telemetry)."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="queued",
                liveness_basis="operator_independent",
                source_fingerprint="seed",
            )
        )
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s2:
        result = await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
        assert result.effect is EntryEffect.UNCHANGED


async def test_start_promotes_awaiting_start_reports_promoted(db_factory):
    """START on an `awaiting_start` row promotes it to `queued` (SPEND) and reports effect=promoted with
    from_status=awaiting_start."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="awaiting_start",
                liveness_basis="request_bound",
                source_fingerprint="seed",
                error="was awaiting start",
            )
        )
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s2:
        result = await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
        assert result.effect is EntryEffect.PROMOTED
        assert result.from_status == "awaiting_start"
        assert result.to_status == "queued"
        assert result.liveness_basis == "operator_independent"  # promoted + basis upgraded together


async def test_operator_command_upgrades_request_bound_liveness_monotonically(db_factory):
    """D2 monotonic merge: an operator START touching a `queued` but `request_bound` row upgrades its basis
    to `operator_independent` (a meaningful mutation ⇒ promoted); the reverse never happens."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="queued",
                liveness_basis="request_bound",
                source_fingerprint="seed",
            )
        )
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s2:
        result = await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
        assert result.effect is EntryEffect.PROMOTED
        assert result.from_status == "queued" and result.to_status == "queued"
        assert result.liveness_basis == "operator_independent"

    async with db_factory() as s3:
        row = (await s3.execute(select(ImportAdoption))).scalar_one()
        assert row.liveness_basis == "operator_independent"  # upgraded, never downgraded


async def test_all_four_adoption_operations_are_wired(db_factory):
    """ADR-0032 D1 names exactly four callers of the one seam. After W4 all four have an explicit
    (entry_intent, liveness_basis) policy — a fifth minter appearing unwired is a revisit trigger, not
    something the seam may guess through."""
    assert set(adoption_entry._POLICY) == set(AdoptionOperation)
    assert adoption_entry._POLICY[AdoptionOperation.REVISION].liveness_basis is LivenessBasis.REQUEST_BOUND
    assert adoption_entry._POLICY[AdoptionOperation.REVISION].entry_intent is EntryIntent.SPEND
    reconciliation = adoption_entry._POLICY[AdoptionOperation.RECONCILIATION]
    assert reconciliation.entry_intent is EntryIntent.RECORD_WITHOUT_SPEND  # a boot never buys anything
    assert reconciliation.liveness_basis is LivenessBasis.REQUEST_BOUND


async def test_unwired_operation_fails_closed(db_factory, monkeypatch):
    """An operation ABSENT from the policy table fails closed rather than guessing an intent/liveness —
    behaviour is defined by the command contract, never inferred. Driven by removing a live entry, so
    the guard keeps testing the MECHANISM once every real operation is wired."""
    monkeypatch.delitem(adoption_entry._POLICY, AdoptionOperation.RECONCILIATION)
    async with db_factory() as s:
        _, ch = await _seed(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s2:
        with pytest.raises(IncompatibleAdoptionEntry):
            await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.RECONCILIATION)


# --------------------------------------------------------------------------- D3 collision recovery


async def test_collision_recovery_reconciles_to_winner(db_factory, monkeypatch):
    """D3: on a stale-read TOCTOU race (the pre-insert active lookup misses a row that actually exists), the
    seam's INSERT collides on `uq_import_adoptions_active_chapter`. It must reconcile to the winner (no
    duplicate row), upgrade its liveness, mark the result collided, and emit the high-severity collision
    telemetry as a LOG — not fabricate a second active adoption."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        winner = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            status="queued",
            liveness_basis="request_bound",
            source_fingerprint="winner",
        )
        s.add(winner)
        await s.commit()
        chapter_id, winner_id = ch.id, winner.id

    real_existing = adoption_entry._existing_adoption
    state = {"stale_served": False}

    async def stale_first(session, chapter_id_, statuses):
        # First active-state lookup returns nothing (simulating a stale read under a concurrent insert);
        # every later lookup (the collision winner-reload) sees the committed winner.
        if not state["stale_served"] and set(statuses) == set(adoption_entry._ACTIVE_INDEX_STATUSES):
            state["stale_served"] = True
            return None
        return await real_existing(session, chapter_id_, statuses)

    monkeypatch.setattr(adoption_entry, "_existing_adoption", stale_first)
    recording = _RecordingLog()
    monkeypatch.setattr(adoption_entry, "log", recording)

    async with db_factory() as s2:
        result = await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)

    assert result.collided is True
    assert result.adoption.id == winner_id  # joined the winner, did not create a rival
    assert result.effect is EntryEffect.PROMOTED  # request_bound winner upgraded by the operator command
    assert result.liveness_basis == "operator_independent"

    kinds = [(level, event) for level, event, _ in recording.events]
    assert ("warning", "adoption_active_invariant_collision") in kinds  # immediate, high-severity
    # the collision event precedes the post-commit success transition
    assert kinds.index(("warning", "adoption_active_invariant_collision")) < kinds.index(
        ("info", "adoption_entry_transition")
    )

    async with db_factory() as s3:
        assert await _count(s3) == 1  # NO duplicate active adoption
        row = await s3.get(ImportAdoption, winner_id)
        assert row.liveness_basis == "operator_independent"


async def test_inert_reuse_emits_no_transition_but_created_does(db_factory, monkeypatch):
    """Telemetry placement (D12): a created/promoted movement emits `adoption_entry_transition`
    POST-COMMIT; a completely inert reuse emits nothing."""
    async with db_factory() as s:
        _, ch = await _seed(s)
        await s.commit()
        chapter_id = ch.id

    recording = _RecordingLog()
    monkeypatch.setattr(adoption_entry, "log", recording)

    async with db_factory() as s2:
        await ensure_import_adoption(s2, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
    assert ("info", "adoption_entry_transition") in [(lv, ev) for lv, ev, _ in recording.events]

    recording.events.clear()
    async with db_factory() as s3:  # second START — inert reuse
        await ensure_import_adoption(s3, chapter_id=chapter_id, operation=AdoptionOperation.OPERATOR_START)
    assert ("info", "adoption_entry_transition") not in [(lv, ev) for lv, ev, _ in recording.events]


# --------------------------------------------------------------------------- W1 schema tightening


async def test_liveness_basis_check_rejects_invalid_value(db_factory):
    """W1 (D13): the CHECK constraint rejects any liveness_basis outside the two permitted values."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="queued",
                source_fingerprint="x",
                liveness_basis="bogus",
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_liveness_basis_accepts_both_valid_values(db_factory):
    """W1: both permitted values pass the CHECK (terminal status keeps them out of the active-chapter
    index, so two rows in one chapter is fine here)."""
    async with db_factory() as s:
        book, ch = await _seed(s)
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="contract_proposed",
                source_fingerprint="a",
                liveness_basis="request_bound",
            )
        )
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status="contract_proposed",
                source_fingerprint="b",
                liveness_basis="operator_independent",
            )
        )
        await s.commit()
        assert await _count(s) == 2


# --------------------------------------------------------------------------- constructor guards (D14)


def _constructor_call_sites(class_name: str) -> list[str]:
    """Every `<class_name>(...)` CONSTRUCTOR call under src/dominion, as 'relpath:lineno'. The ORM class
    declaration (`class X(Base)`) is a ClassDef, not a Call, so it is not counted."""
    sites: list[str] = []
    for py in _SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                if name == class_name:
                    sites.append(f"{py.relative_to(_SRC).as_posix()}:{node.lineno}")
    return sites


def _import_adoption_call_sites() -> list[str]:
    return _constructor_call_sites("ImportAdoption")


def test_only_the_seam_constructs_import_adoption():
    """AST guard (D1/D14): exactly one `ImportAdoption(...)` constructor exists in production source, and it
    lives in the adoption seam. Start, Re-author, routers, workers, reconciliation, and helpers must route
    through ensure_import_adoption[_locked] instead."""
    sites = _import_adoption_call_sites()
    offenders = [s for s in sites if not s.startswith(_SEAM_REL + ":")]
    assert not offenders, "ImportAdoption(...) constructed outside the adoption seam: " + ", ".join(offenders)
    assert len(sites) == 1, f"expected exactly one seam constructor, found {len(sites)}: {sites}"


def test_single_ownership_holds_in_both_directions():
    """D14/D4: single ownership is a TWO-way claim, so the guard must be two-way too.

    The revision module must never construct an `ImportAdoption`, AND the adoption module must never
    construct a `RevisionRequest`. W3/W4 make this live: the coordinators sequence both owners, so the
    tempting shortcut — "just build the other row here, it's right in front of me" — is now one line
    away in three files. `RevisionRequest` construction belongs to `workers/revision.py` alone.
    """
    revision_owner = "workers/revision.py"
    adoption_owner = "shared/adoption_entry.py"

    adoption_sites = _constructor_call_sites("ImportAdoption")
    assert not [s for s in adoption_sites if s.startswith(revision_owner + ":")], (
        f"the revision module constructed an ImportAdoption: {adoption_sites}"
    )

    request_sites = _constructor_call_sites("RevisionRequest")
    assert not [s for s in request_sites if s.startswith(adoption_owner + ":")], (
        f"the adoption module constructed a RevisionRequest: {request_sites}"
    )
    stray = [s for s in request_sites if not s.startswith(revision_owner + ":")]
    assert not stray, "RevisionRequest(...) constructed outside the revision module: " + ", ".join(stray)


def test_no_raw_or_bulk_import_adoption_inserts_in_production_source():
    """Guard against bypassing the seam via raw SQL / Core inserts / bulk mappings (D14). No production
    source may INSERT into import_adoptions except through the ORM in the seam."""
    patterns = [
        re.compile(r"insert\s+into\s+import_adoptions", re.IGNORECASE),
        re.compile(r"\binsert\(\s*ImportAdoption\b"),
        re.compile(r"\bbulk_insert_mappings\(\s*ImportAdoption\b"),
        re.compile(r"\bbulk_save_objects\b"),
    ]
    offenders: list[str] = []
    for py in _SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for pat in patterns:
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{py.relative_to(_SRC).as_posix()}:{line} ({pat.pattern})")
    assert not offenders, "raw/bulk import_adoptions insert(s) bypassing the seam:\n  " + "\n  ".join(offenders)
