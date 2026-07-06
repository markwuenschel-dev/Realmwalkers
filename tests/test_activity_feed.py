"""Central Activity feed: emit → list → clear.

The drawer reads ONE source (`GET /activity`); every surface writes through `record_activity`. These
pin the read filters (newest-first, dismissed hidden by default) and the clear semantics ("finished"
only touches terminal kinds; "all" clears everything), which the drawer's "Clear finished" relies on.
"""

from __future__ import annotations

from dominion.api.routers import activity as activity_router
from dominion.shared.models import Book, Chapter
from dominion.shared.schemas import ActivityClearIn
from dominion.workers import activity


async def _seed_book_chapter(s):
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=3, pov="Mara", title="The Breach")
    s.add(chapter)
    await s.flush()
    return book, chapter


async def test_record_and_list_newest_first(db_factory):
    async with db_factory() as s:
        book, chapter = await _seed_book_chapter(s)
        await activity.record_activity(
            s, kind="scene_decision", title="Scene 1 approved", source="reviews", book_id=book.id
        )
        await activity.record_activity(
            s, kind="draft_done", title="Ch 3 · Scene 2 drafted", source="jobs", book_id=book.id
        )
        await s.commit()

        rows = await activity_router.list_activity(s, book_id=book.id)
        assert [r.kind for r in rows] == ["draft_done", "scene_decision"]  # newest first


async def test_clear_finished_only_dismisses_terminal_kinds(db_factory):
    async with db_factory() as s:
        book, _chapter = await _seed_book_chapter(s)
        await activity.record_activity(
            s, kind="scene_decision", title="Scene 1 approved", source="reviews", book_id=book.id
        )
        await activity.record_activity(s, kind="draft_done", title="Scene 2 drafted", source="jobs", book_id=book.id)
        await activity.record_activity(s, kind="draft_failed", title="Scene 3 failed", source="jobs", book_id=book.id)
        await s.commit()

        out = await activity_router.clear_activity(ActivityClearIn(scope="finished", book_id=book.id), s)
        assert out.dismissed == 2  # draft_done + draft_failed, not the review decision

        visible = await activity_router.list_activity(s, book_id=book.id)
        assert [r.kind for r in visible] == ["scene_decision"]
        # The cleared rows still exist, just hidden — include_dismissed surfaces them again.
        all_rows = await activity_router.list_activity(s, book_id=book.id, include_dismissed=True)
        assert len(all_rows) == 3


async def test_clear_all_dismisses_everything(db_factory):
    async with db_factory() as s:
        book, _chapter = await _seed_book_chapter(s)
        await activity.record_activity(
            s, kind="scene_decision", title="Scene 1 approved", source="reviews", book_id=book.id
        )
        await activity.record_activity(s, kind="run_started", title="Run started", source="runs", book_id=book.id)
        await s.commit()

        out = await activity_router.clear_activity(ActivityClearIn(scope="all", book_id=book.id), s)
        assert out.dismissed == 2
        assert await activity_router.list_activity(s, book_id=book.id) == []


async def test_production_event_mirrors_into_feed(db_factory):
    # record_event is the production chokepoint; every event must also appear in the central feed.
    from dominion.shared.models import ProductionRun
    from dominion.workers import production_support

    async with db_factory() as s:
        book, chapter = await _seed_book_chapter(s)
        run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="running")
        s.add(run)
        await s.flush()

        await production_support.record_event(
            s, run_id=run.id, event_type="run_started", message="Production run started"
        )
        await s.commit()

        rows = await activity_router.list_activity(s, book_id=book.id)
        assert any(r.kind == "run_started" and r.source == "production" for r in rows)
        # The mirror resolves book/chapter from the run so it's book-scoped, not orphaned.
        assert rows[0].chapter_id == chapter.id
