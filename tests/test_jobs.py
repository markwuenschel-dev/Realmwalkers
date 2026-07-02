"""Requeue and jobs API contract-first tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from conftest import seed_scene_packet
from sqlalchemy import select

from dominion.api.routers import jobs as jobs_router
from dominion.shared.enums import BeatStatus, JobKind, JobStatus, RunStatus
from dominion.shared.models import Beat, Book, Chapter, Job, Run
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


async def test_retry_failed_api_returns_structured_result(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        s.add(
            Job(
                run_id=run.id,
                kind=JobKind.DRAFT,
                chapter_id=ch.id,
                beat_id=beat.id,
                scene_packet_id=sp.id,
                chapter_no=1,
                scene_no=1,
                status=JobStatus.FAILED,
                token_budget=40_000,
                last_error="err",
            )
        )
        await s.flush()
        out = await jobs_router.retry_failed(
            background=MagicMock(),
            session=s,
            book_id=book.id,
        )
        assert out.requested == 1
        assert out.requeued == 1


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


async def test_clear_failed_scoped_to_chapter(db_factory):
    async with db_factory() as s:
        book, ch, beat, run, sp = await _setup(s)
        ch2 = Chapter(book_id=book.id, chapter_no=2, pov="Y")
        s.add(ch2)
        await s.flush()
        beat2 = Beat(chapter_id=ch2.id, scene_no=1, status=BeatStatus.APPROVED, beat_text="b2")
        s.add(beat2)
        await s.flush()
        sp2 = await seed_scene_packet(s, chapter=ch2, beat=beat2)
        for b, scene_no, chapter, chapter_no, sp_id in (
            (beat, 1, ch, 1, sp.id),
            (beat2, 1, ch2, 2, sp2.id),
        ):
            s.add(
                Job(
                    run_id=run.id,
                    kind=JobKind.DRAFT,
                    chapter_id=chapter.id,
                    beat_id=b.id,
                    scene_packet_id=sp_id,
                    chapter_no=chapter_no,
                    scene_no=scene_no,
                    status=JobStatus.FAILED,
                    token_budget=40_000,
                    last_error="err",
                )
            )
        await s.flush()
        out = await jobs_router.clear_failed(session=s, book_id=book.id, chapter_id=ch.id)
        assert out.purged == 1
        assert out.failed == 1
        failed_list = await jobs_router.failed(session=s, book_id=book.id)
        assert len(failed_list) == 1
        assert failed_list[0].chapter_no == 2
