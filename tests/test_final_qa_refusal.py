"""run_final_qa must not report a refusal as a success.

It returned `latest_chapter_draft_qa(run_id)` — the newest QA artifact that ALREADY EXISTS — and only
raised when that came back None. So the refusal was only ever visible on a run that had never
assembled. On a run that has assembled before (the real ones on the box carry 26-28 QA versions), a
later refusal still found an artifact, returned it 200, and the Desk announced "Chapter QA v28
written" for a call that wrote nothing and left the run parked.

The fix compares artifact identity across the assemble call. `assemble_run` writes a NEW
chapter_draft_qa unconditionally on every pass it completes, so getting the same artifact back means
the L6 gate refused — which is the only way that happens.
"""

from __future__ import annotations

import pytest

from dominion.shared.models import Artifact, Book, Chapter, ProductionRun
from dominion.workers import production_sequence
from dominion.workers.production import run_final_qa
from dominion.workers.production_support import hash_payload


async def _run_with_qa_history(s, *, qa_versions: int) -> tuple[ProductionRun, Artifact | None]:
    """A run carrying `qa_versions` prior chapter_draft_qa artifacts, as a real assembled run does."""
    book = Book(title="Final QA")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="A")
    s.add(ch)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, status="running", current_stage="waiting_for_scene_drafts")
    s.add(run)
    await s.flush()
    latest = None
    for version in range(1, qa_versions + 1):
        body = {"verdict": "pass", "version": version}
        latest = Artifact(
            production_run_id=run.id,
            artifact_type="chapter_draft_qa",
            version=version,
            status="active",
            body=body,
            content_hash=hash_payload(body),
        )
        s.add(latest)
    await s.flush()
    return run, latest


async def test_refusal_on_a_run_with_qa_history_raises_instead_of_returning_the_stale_report(db_factory, monkeypatch):
    """The regression. Assembly refuses (writes nothing); the run already has 28 QA versions."""

    async def _refuses(session, run):  # noqa: ANN001 — matches assemble_run's signature
        return None  # the L6 gate parks the run and returns without creating an artifact

    monkeypatch.setattr(production_sequence, "assemble_run", _refuses)

    async with db_factory() as s:
        run, stale = await _run_with_qa_history(s, qa_versions=28)
        assert stale is not None
        with pytest.raises(ValueError) as excinfo:
            await run_final_qa(s, run.id)
        # The caller gets the parked stage, not a v28 artifact dressed up as this call's output.
        assert "assembly refused" in str(excinfo.value)
        assert "waiting_for_scene_drafts" in str(excinfo.value)


async def test_refusal_on_a_run_with_no_qa_history_still_raises(db_factory, monkeypatch):
    """The path that already worked, kept working — absence and staleness both mean refused."""

    async def _refuses(session, run):  # noqa: ANN001
        return None

    monkeypatch.setattr(production_sequence, "assemble_run", _refuses)

    async with db_factory() as s:
        run, stale = await _run_with_qa_history(s, qa_versions=0)
        assert stale is None
        with pytest.raises(ValueError, match="assembly refused"):
            await run_final_qa(s, run.id)


async def test_a_completed_assembly_returns_the_artifact_it_just_wrote(db_factory, monkeypatch):
    """The success path must still succeed, and must hand back the NEW report, not the previous one."""
    written: dict[str, Artifact] = {}

    async def _assembles(session, run):  # noqa: ANN001
        body = {"verdict": "pass", "version": 29}
        art = Artifact(
            production_run_id=run.id,
            artifact_type="chapter_draft_qa",
            version=29,
            status="active",
            body=body,
            content_hash=hash_payload(body),
        )
        session.add(art)
        await session.flush()
        written["artifact"] = art

    monkeypatch.setattr(production_sequence, "assemble_run", _assembles)

    async with db_factory() as s:
        run, stale = await _run_with_qa_history(s, qa_versions=28)
        out = await run_final_qa(s, run.id)
        assert out.id == written["artifact"].id
        assert out.id != stale.id  # type: ignore[union-attr]
        assert out.version == 29
