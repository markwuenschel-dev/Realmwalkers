"""Mechanism regression for the N1 `MissingGreenlet` enrich-after-commit class.

Every model with a server-side `updated_at` (`onupdate=func.now()`) has its `updated_at` column
EXPIRED at flush when an UPDATE fires — regardless of `expire_on_commit=False`, because the value is
computed by the database. A post-commit attribute read (as `model_validate`/`enrich_*_out` does) then
triggers a *synchronous* lazy-load on the async session → `sqlalchemy.exc.MissingGreenlet`.

This test pins the mechanism over the five `onupdate` models so the class stays understood even as the
endpoints evolve: after mutate→commit WITHOUT refresh, reading `updated_at` raises MissingGreenlet;
after `session.refresh(row)` the same read is safe. `DraftRunTimeline` is covered defensively here —
it has no API serialization endpoint, so it gets the mechanism assertion but no router fix.

See docs/plans/n1-greenlet-enrich-after-commit-contract.md (candidate N1).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import MissingGreenlet

from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    DraftRunTimeline,
    ProductionRun,
    RepairTask,
    ScenePacket,
)


async def _parents(s):
    book = Book(title="N1 mechanism")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Mara")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": []},
        open_questions={"items": []},
    )
    s.add(cp)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, status="running", current_stage="drafting")
    s.add(run)
    await s.flush()
    return book, ch, cp, run


def _build(model_name, book, ch, cp, run):
    """Return (row, mutate) for one onupdate model. `mutate` dirties a benign nullable field so an
    UPDATE fires — which is what expires the server-computed `updated_at`."""
    if model_name == "ScenePacket":
        row = ScenePacket(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            scene_no=1,
            status="proposed",
            body={"scene_no": 1},
            source_hash="seed",
        )
        return row, lambda r: setattr(r, "stale_reason", "n1")
    if model_name == "ChapterSequence":
        row = ChapterSequence(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            status="proposed",
            body={"scenes": []},
        )
        return row, lambda r: setattr(r, "stale_reason", "n1")
    if model_name == "ProductionRun":
        row = ProductionRun(book_id=book.id, chapter_id=ch.id, status="running", current_stage="drafting")
        return row, lambda r: setattr(r, "current_stage", "n1_mutated")
    if model_name == "RepairTask":
        row = RepairTask(
            production_run_id=run.id,
            chapter_id=ch.id,
            repair_kind="span_patch",
            authority_level="scene_local",
            status="queued",
            instructions="Fix it.",
        )
        return row, lambda r: setattr(r, "status", "waiting_for_human")
    # DraftRunTimeline — defensive-only (no API serialization endpoint)
    row = DraftRunTimeline(production_run_id=run.id, chapter_id=ch.id, current_scene_no=1)
    return row, lambda r: setattr(r, "current_exit_state", "n1_mutated")


@pytest.mark.parametrize(
    "model_name",
    ["ScenePacket", "ChapterSequence", "ProductionRun", "RepairTask", "DraftRunTimeline"],
)
async def test_onupdate_column_expires_at_flush_and_refresh_restores_it(db_factory, model_name):
    async with db_factory() as s:
        book, ch, cp, run = await _parents(s)
        row, mutate = _build(model_name, book, ch, cp, run)

        s.add(row)
        await s.commit()  # INSERT: server-defaults come back via RETURNING → updated_at is loaded/safe.
        assert row.updated_at is not None  # baseline: readable after insert

        # Mutate + commit WITHOUT refresh: the UPDATE's server-side onupdate expires `updated_at`.
        mutate(row)
        await s.commit()

        # The class: a bare read of the expired server-computed column triggers a sync lazy-load on the
        # async session → MissingGreenlet (exactly what model_validate/enrich do post-commit).
        with pytest.raises(MissingGreenlet):
            _ = row.updated_at

        # The fix: refresh reloads the row (async), and the read is safe again.
        await s.refresh(row)
        assert row.updated_at is not None
