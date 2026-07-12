"""Requeue and jobs API contract-first tests."""

from __future__ import annotations

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.api.routers import jobs as jobs_router
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, RunStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Run, Scene
from dominion.workers.draft_queue import reconcile_and_requeue_failed_draft_jobs


async def _setup(s):
    book = Book(title="Requeue")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="X")
    s.add(ch)
    run = Run(
        book_id=book.id,
        scope_json={},
        gate_mode="pause_each",
        token_budget=40_000,
        status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b")
    s.add(beat)
    await s.flush()
    sp = await seed_scene_packet(s, chapter=ch, beat=beat)
    return book, ch, beat, run, sp


async def test_requeue_creates_fresh_job_for_failed_draft(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        old = Job(
            run_id=run.id,
            kind=JobKind.DRAFT,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
            chapter_no=1,
            scene_no=1,
            status=JobStatus.FAILED,
            token_budget=40_000,
            last_error="transient",
        )
        s.add(old)
        await s.flush()
        result = await reconcile_and_requeue_failed_draft_jobs(s, book_id=book.id)
        assert result.queued == 1
        new_jobs = (await s.execute(select(Job).where(Job.status == JobStatus.QUEUED))).scalars().all()
        assert len(new_jobs) == 1
        assert new_jobs[0].scene_packet_id == sp.id
        assert new_jobs[0].id != old.id


async def test_requeue_skips_when_scene_packet_not_approved(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        sp.status = "proposed"
        old = Job(
            run_id=run.id,
            kind=JobKind.DRAFT,
            chapter_id=ch.id,
            beat_id=beat.id,
            scene_packet_id=sp.id,
            chapter_no=1,
            scene_no=1,
            status=JobStatus.FAILED,
            token_budget=40_000,
        )
        s.add(old)
        await s.flush()
        result = await reconcile_and_requeue_failed_draft_jobs(s, book_id=book.id)
        assert result.queued == 0
        assert len(result.skipped) >= 1


async def test_clear_failed_api_purges_failed_jobs(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        for scene_no in (1, 2):
            s.add(
                Job(
                    run_id=run.id,
                    kind=JobKind.DRAFT,
                    chapter_id=ch.id,
                    beat_id=beat.id,
                    scene_packet_id=sp.id,
                    chapter_no=1,
                    scene_no=scene_no,
                    status=JobStatus.FAILED,
                    token_budget=40_000,
                    last_error="err",
                )
            )
        await s.flush()
        out = await jobs_router.clear_failed(session=s, book_id=book.id)
        assert out.purged == 2
        assert out.failed == 0
        failed_list = await jobs_router.failed(session=s, book_id=book.id)
        assert failed_list == []


async def test_queue_counts_sees_upload_revision_with_null_run_id(db_factory):
    """The core strand bug: an upload-only book has no Run, so its revision Job carries book_id but a
    NULL run_id. An INNER JOIN to Run dropped it, so /jobs/draft-next read queued==0 and never kicked
    the drain (Activity showed 'Queued · 1', the Desk showed 'idle'). Scoping must use the OR predicate."""
    async with db_factory() as s:
        book = Book(title="Upload only")  # deliberately NO Run row
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=0, pov="X")
        s.add(ch)
        await s.flush()
        scene = Scene(chapter_id=ch.id, scene_no=1, prose="prologue draft", version=1, status=SceneStatus.DRAFT)
        s.add(scene)
        await s.flush()
        s.add(
            Job(
                run_id=None,  # no Run for an upload-only book
                book_id=book.id,
                kind=JobKind.REVISE_FULL,
                chapter_id=ch.id,
                target_scene_id=scene.id,
                chapter_no=0,
                scene_no=1,
                status=JobStatus.QUEUED,
                token_budget=40_000,
            )
        )
        await s.flush()
        counts = await jobs_router._queue_counts(s, book.id)
        assert counts.get(str(JobStatus.QUEUED)) == 1


async def test_requeue_resets_failed_revision_in_place(db_factory):
    """A FAILED revise_* job keeps its target scene + Approval feedback, so retry-failed resets it in
    place (same id) rather than minting a fresh DRAFT — and reaches it via the OR scope even with a
    NULL run_id (upload-only book)."""
    async with db_factory() as s:
        book = Book(title="Upload only")  # no Run
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=0, pov="X")
        s.add(ch)
        await s.flush()
        scene = Scene(chapter_id=ch.id, scene_no=1, prose="prologue", version=1, status=SceneStatus.REVISION_REQUESTED)
        s.add(scene)
        await s.flush()
        old = Job(
            run_id=None,
            book_id=book.id,
            kind=JobKind.REVISE_FULL,
            chapter_id=ch.id,
            target_scene_id=scene.id,
            chapter_no=0,
            scene_no=1,
            status=JobStatus.FAILED,
            token_budget=40_000,
            last_error="transient",
            claimed_by="worker-1",
        )
        s.add(old)
        await s.flush()
        old_id = old.id

        result = await reconcile_and_requeue_failed_draft_jobs(s, book_id=book.id)
        assert result.queued == 1
        await s.refresh(old)
        # Reset in place: same row, not a fresh job.
        assert old.id == old_id
        assert old.status == JobStatus.QUEUED
        assert old.last_error is None
        assert old.claimed_by is None
        assert old.target_scene_id == scene.id
        queued = (await s.execute(select(Job).where(Job.status == JobStatus.QUEUED))).scalars().all()
        assert [j.id for j in queued] == [old_id]


async def test_clear_failed_purges_revision_jobs_too(db_factory):
    """Clear must dismiss failed REVISE_* jobs, not just DRAFT — the failed count/banner are
    kind-agnostic, so a DRAFT-only purge left revision failures counted but unclearable."""
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        for kind in (JobKind.REVISE_FULL, JobKind.REVISE_PASS):
            s.add(
                Job(
                    run_id=run.id,
                    kind=kind,
                    chapter_id=ch.id,
                    beat_id=beat.id,
                    scene_packet_id=sp.id,
                    chapter_no=1,
                    scene_no=1,
                    status=JobStatus.FAILED,
                    token_budget=40_000,
                    last_error="BadRequestError: temperature is deprecated for this model",
                )
            )
        await s.flush()
        # Banner shows the revision failures (kind-agnostic)...
        assert len(await jobs_router.failed(session=s, book_id=book.id)) == 2
        # ...and Clear now dismisses them (previously purged 0, leaving the count stuck).
        out = await jobs_router.clear_failed(session=s, book_id=book.id)
        assert out.purged == 2
        assert out.failed == 0
        assert await jobs_router.failed(session=s, book_id=book.id) == []
