"""Lane 1 migration tests — verify the Critique provenance columns, the partial indexes, the additive
Issue statuses, and forward-only non-interference actually land in a migrated database.

The conftest builds the schema once via create_all + apply_lightweight_migrations (the same path boot
runs), so these assert against a real migrated Postgres.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from dominion.shared.enums import IssueStatus
from dominion.shared.models import Book, Chapter, Critique, Scene
from dominion.workers.scene_fidelity import finding_signature


async def _seed_scene(s) -> Scene:
    book = Book(title="Fidelity")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Serra")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1)
    s.add(scene)
    await s.flush()
    return scene


async def test_critiques_table_has_fidelity_provenance_columns(db_factory) -> None:
    async with db_factory() as s:
        rows = await s.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'critiques'")
        )
        cols = {r[0] for r in rows}
    assert {"draft_attempt_id", "source_artifact_id", "finding_signature", "created_at"} <= cols


async def test_scene_fidelity_partial_indexes_exist(db_factory) -> None:
    async with db_factory() as s:
        rows = await s.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'critiques'"))
        names = {r[0] for r in rows}
    assert "uq_scene_fidelity_critique_report_finding" in names
    assert "ix_scene_fidelity_critique_draft_chrono" in names


def test_issue_status_has_additive_fidelity_states() -> None:
    assert IssueStatus.OVERRIDDEN == "overridden"
    assert IssueStatus.SUPERSEDED == "superseded"
    # Additive — the pre-existing statuses are untouched (ADR 0025).
    assert {"proposed", "verified", "false_positive"} <= {s.value for s in IssueStatus}


async def test_fidelity_critique_persists_with_provenance(db_factory) -> None:
    async with db_factory() as s:
        scene = await _seed_scene(s)
        sig = finding_signature(requirement_id="req-1", clause_id="cl-1", result="lost")
        art_id = uuid.uuid4()
        s.add(
            Critique(
                scene_id=scene.id,
                reviewer="scene_fidelity",
                severity="repair",
                note="Serra's agency is coerced, not chosen.",
                source_artifact_id=art_id,
                draft_attempt_id=uuid.uuid4(),
                finding_signature=sig,
                payload={"clause_id": "cl-1", "result": "lost"},
            )
        )
        await s.flush()
        loaded = (await s.execute(select(Critique).where(Critique.reviewer == "scene_fidelity"))).scalar_one()
        assert loaded.source_artifact_id == art_id
        assert loaded.finding_signature == sig
        assert loaded.created_at is not None  # server_default now() stamped the new row


async def test_report_projection_idempotency_index_is_enforced(db_factory) -> None:
    """Two critiques projecting the same finding from the same report Artifact collide (ADR 0021)."""
    async with db_factory() as s:
        scene = await _seed_scene(s)
        art_id = uuid.uuid4()
        sig = finding_signature(requirement_id="req-1", clause_id="cl-1", result="lost")
        common = dict(
            scene_id=scene.id,
            reviewer="scene_fidelity",
            severity="repair",
            source_artifact_id=art_id,
            finding_signature=sig,
        )
        s.add(Critique(**common))
        await s.flush()
        s.add(Critique(**common))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


async def test_non_fidelity_critiques_bypass_the_partial_index(db_factory) -> None:
    """The partial index constrains only fully-populated scene_fidelity rows; legacy critiques with NULL
    provenance are never affected (forward-only, ADR 0025)."""
    async with db_factory() as s:
        scene = await _seed_scene(s)
        s.add(Critique(scene_id=scene.id, reviewer="continuity", severity="warn"))
        s.add(Critique(scene_id=scene.id, reviewer="continuity", severity="warn"))
        await s.flush()  # no collision — index predicate excludes these
        count = len((await s.execute(select(Critique).where(Critique.reviewer == "continuity"))).scalars().all())
    assert count == 2
