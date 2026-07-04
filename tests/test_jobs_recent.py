"""GET /jobs/recent — the Activity drawer's queue + recently-finished feed (Atelier redesign P0).

DB-backed (skips if Postgres unreachable, like the rest of tests/). Router function called
directly, mirroring tests/test_desk_api.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dominion.api.routers import jobs as jobs_router
from dominion.shared.enums import JobStatus
from dominion.shared.models import Book, Chapter, Job, Scene


def _job(**kw: object) -> Job:
    defaults = dict(kind="draft", token_budget=1000, status=JobStatus.QUEUED)
    defaults.update(kw)
    return Job(**defaults)  # type: ignore[arg-type]


async def _seed(s):
    book = Book(title="Recent Jobs Book")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(chapter)
    await s.flush()
    scene = Scene(chapter_id=chapter.id, scene_no=2, word_count=1480, prose="words")
    s.add(scene)
    await s.flush()
    return book, chapter, scene


async def test_recent_orders_queue_and_computes_durations(db_factory):
    async with db_factory() as s:
        book, _ch, scene = await _seed(s)
        t0 = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
        # Queue: created later should list later (position = created_at asc).
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=3, created_at=t0 + timedelta(minutes=1)))
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=4, created_at=t0 + timedelta(minutes=2)))
        # Done with full stamps + a produced scene -> duration + word count.
        s.add(
            _job(
                book_id=book.id,
                chapter_no=1,
                scene_no=2,
                status=JobStatus.DONE,
                target_scene_id=scene.id,
                claimed_at=t0,
                finished_at=t0 + timedelta(seconds=151),
            )
        )
        # Legacy done row (pre-finished_at): no duration, no scene join — must not crash.
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=1, status=JobStatus.DONE, claimed_at=t0))
        # Failed row carries its error.
        s.add(
            _job(
                book_id=book.id,
                chapter_no=1,
                scene_no=5,
                status=JobStatus.FAILED,
                last_error="boom",
                claimed_at=t0,
                finished_at=t0 + timedelta(seconds=9),
            )
        )
        await s.commit()

        out = await jobs_router.recent(s, book_id=book.id)
        assert [q.scene_no for q in out.queued] == [3, 4]
        by_scene = {r.scene_no: r for r in out.recent}
        assert by_scene[2].duration_s == 151
        assert by_scene[2].word_count == 1480
        assert by_scene[1].duration_s is None  # legacy: finished_at NULL
        assert by_scene[5].status == JobStatus.FAILED
        assert by_scene[5].last_error == "boom"
        # finished_at desc, NULLs last: scene 2 (12:02:31) before scene 5 (12:00:09), legacy last.
        assert [r.scene_no for r in out.recent] == [2, 5, 1]


async def test_recent_scopes_to_book_and_clamps_limit(db_factory):
    async with db_factory() as s:
        book, _ch, _scene = await _seed(s)
        other = Book(title="Other Book")
        s.add(other)
        await s.flush()
        s.add(_job(book_id=book.id, chapter_no=1, scene_no=1))
        s.add(_job(book_id=other.id, chapter_no=9, scene_no=9))
        for i in range(3):
            s.add(
                _job(
                    book_id=book.id,
                    chapter_no=1,
                    scene_no=i,
                    status=JobStatus.DONE,
                    finished_at=datetime.now(UTC) - timedelta(seconds=i),
                )
            )
        await s.commit()

        out = await jobs_router.recent(s, book_id=book.id)
        assert [q.scene_no for q in out.queued] == [1]  # the other book's job is invisible
        assert all(r.chapter_no == 1 for r in out.recent)

        clamped = await jobs_router.recent(s, book_id=book.id, limit=2)
        assert len(clamped.recent) == 2
        # limit=0 clamps up to 1 rather than erroring or returning everything.
        floor = await jobs_router.recent(s, book_id=book.id, limit=0)
        assert len(floor.recent) == 1


async def test_recent_reaches_legacy_jobs_through_their_run(db_factory):
    async with db_factory() as s:
        from dominion.shared.models import Run

        book, _ch, _scene = await _seed(s)
        run = Run(book_id=book.id, scope_json={}, gate_mode="pause_each", token_budget=1000)
        s.add(run)
        await s.flush()
        # Legacy routing: job carries run_id but NOT book_id.
        s.add(_job(run_id=run.id, chapter_no=1, scene_no=7))
        await s.commit()

        out = await jobs_router.recent(s, book_id=book.id)
        assert [q.scene_no for q in out.queued] == [7]
