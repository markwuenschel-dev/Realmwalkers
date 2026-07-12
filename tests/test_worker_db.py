"""End-to-end worker tests against a real Postgres. The Drafter's LLM call is mocked."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from dominion.shared.enums import (
    BeatStatus,
    GateMode,
    JobKind,
    JobStatus,
    SceneStatus,
)
from dominion.shared.models import Beat, Book, Chapter, Job, Run, Scene
from dominion.workers import worker
from dominion.workers.specialists import drafter as drafter_mod
from tests.conftest import seed_scene_packet


async def _seed_job(factory) -> object:
    """Create book -> chapter -> approved beat (+ scene packet) -> run -> queued draft job."""
    async with factory() as s:
        book = Book(title="Test Book")
        s.add(book)
        await s.flush()
        chapter = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(chapter)
        await s.flush()
        beat = Beat(
            chapter_id=chapter.id,
            scene_no=1,
            tags=[],
            status=BeatStatus.APPROVED,
            beat_text="Marcus wakes in the Realm.",
        )
        s.add(beat)
        await s.flush()
        await seed_scene_packet(s, chapter=chapter, beat=beat)
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1, "scene": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
        )
        s.add(run)
        await s.flush()
        job = Job(
            run_id=run.id,
            book_id=book.id,
            kind=JobKind.DRAFT,
            chapter_no=1,
            scene_no=1,
            token_budget=40_000,
            status=JobStatus.QUEUED,
        )
        s.add(job)
        await s.commit()
        return job.id


async def test_failure_path_marks_job_failed_without_secondary_error(db_factory, monkeypatch):
    """Regression for the rollback/expired-attribute bug: the worker must re-raise the ORIGINAL
    error and mark the job failed — not blow up with MissingGreenlet in the except block."""

    async def boom(self, prose, ctx):
        raise RuntimeError("drafter blew up")

    monkeypatch.setattr(drafter_mod.Drafter, "run", boom)
    job_id = await _seed_job(db_factory)

    with pytest.raises(RuntimeError, match="drafter blew up"):
        await worker.run_once(session_factory=db_factory)

    async with db_factory() as s:
        status = await s.scalar(select(Job.status).where(Job.id == job_id))
    assert status == JobStatus.FAILED


async def test_happy_path_creates_pending_review_scene(db_factory, monkeypatch):
    """A drafted scene lands as pending_review; the job is done; continuity no-ops on empty ledger."""

    async def fake_draft(self, prose, ctx):
        return "Marcus woke to a humming sky, the interface blooming behind his eyes."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    job_id = await _seed_job(db_factory)

    ok = await worker.run_once(session_factory=db_factory)
    assert ok is True

    async with db_factory() as s:
        scene = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalar_one()
        job_status = await s.scalar(select(Job.status).where(Job.id == job_id))

    assert scene.status == SceneStatus.PENDING_REVIEW
    assert "humming sky" in (scene.prose or "")
    assert scene.prose_source == "agent"
    assert scene.passes_run == ["drafter"]
    assert job_status == JobStatus.DONE


async def test_empty_queue_returns_false(db_factory):
    assert await worker.run_once(session_factory=db_factory) is False
