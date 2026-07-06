"""approve_final_chapter must never mark a run COMPLETED unless its final_chapter artifact was stamped.

The stamp (final_chapter_status="approved_by_human" + content_hash recompute) was wrapped in
`except Exception: pass`, so a stamp failure would be swallowed and the run marked COMPLETED anyway —
leaving the artifact unmarked and carrying a stale hash, silently (no log, no event). These tests lock
the coupling: the happy path stamps the artifact and completes with a consistent hash; a stamp failure
is fail-closed (the call raises and the run is NOT completed). Candidate C9.
"""

from __future__ import annotations

import pytest

from dominion.shared.enums import ProductionRunStatus
from dominion.shared.models import Artifact, Book, Chapter, ProductionRun
from dominion.workers import production
from dominion.workers.production import _hash_payload, approve_final_chapter


async def _run_with_final_chapter(s) -> tuple[ProductionRun, Artifact]:
    book = Book(title="Approve")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, status="running", current_stage="final_ready")
    s.add(run)
    await s.flush()
    body = {"final_chapter_status": "fully_validated", "prose": "x"}
    art = Artifact(
        production_run_id=run.id,
        artifact_type="final_chapter",
        version=1,
        status="active",
        body=body,
        content_hash=_hash_payload(body),
    )
    s.add(art)
    await s.flush()
    return run, art


async def test_approve_stamps_artifact_and_completes(db_factory):
    async with db_factory() as s:
        run, art = await _run_with_final_chapter(s)
        result = await approve_final_chapter(s, run.id)
        assert result.status == ProductionRunStatus.COMPLETED
        # Same session identity map -> `art` reflects the in-place stamp the tool applied.
        assert art.body["final_chapter_status"] == "approved_by_human"
        assert art.content_hash == _hash_payload(art.body)  # hash stays consistent with the stamped body


async def test_approve_is_fail_closed_when_stamp_raises(db_factory, monkeypatch):
    # If stamping the artifact fails, the run must NOT be marked COMPLETED. Under the old
    # `except Exception: pass` this swallowed and completed anyway; the fix lets it propagate.
    async with db_factory() as s:
        run, _art = await _run_with_final_chapter(s)

        def boom(_value: object) -> str:
            raise RuntimeError("hash backend down")

        monkeypatch.setattr(production, "_hash_payload", boom)
        with pytest.raises(RuntimeError):
            await approve_final_chapter(s, run.id)
        assert run.status != ProductionRunStatus.COMPLETED  # fail-closed: still not completed
