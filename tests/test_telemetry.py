"""Telemetry aggregation endpoints: per-chapter (per-scene breakdown) and per-book (rollups).

Real Postgres (skips if unreachable), router functions called directly with a session — mirrors the
other router tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from dominion.api.routers import telemetry as tel_router
from dominion.shared.models import Book, Chapter, LlmCall


async def _book_with_chapters(s) -> tuple[Book, Chapter, Chapter]:
    book = Book(title="T")
    s.add(book)
    await s.flush()
    ch1 = Chapter(book_id=book.id, chapter_no=1, pov="A", title="One")
    ch2 = Chapter(book_id=book.id, chapter_no=2, pov="B", title="Two")
    s.add_all([ch1, ch2])
    await s.flush()
    return book, ch1, ch2


async def test_chapter_telemetry_aggregates_per_scene(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        s.add_all(
            [
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=1000,
                    output_tokens=200,
                    cache_read_tokens=1000,
                    latency_ms=400,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=500,
                    output_tokens=100,
                    truncated=True,
                    latency_ms=600,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=2,
                    stage="scene_packet_author",
                    model="sonnet",
                    input_tokens=2000,
                    output_tokens=300,
                    error="boom",
                ),
            ]
        )
        await s.flush()

        out = await tel_router.chapter_telemetry(ch.id, s)
        assert out.totals.calls == 3
        assert out.totals.truncations == 1 and out.totals.errors == 1

        scene1 = next(sc for sc in out.scenes if sc.scene_no == 1)
        assert scene1.calls == 2
        assert scene1.models == ["haiku"]
        # cache_read 1000 / (input 1500 + cache_creation 0 + cache_read 1000) = 0.4
        assert scene1.cache_hit_ratio == 0.4
        assert scene1.cache_tokens_saved == 900  # int(1000 * 0.9)
        assert scene1.avg_latency_ms == 500  # (400 + 600) / 2


async def test_chapter_telemetry_empty_when_never_derived(db_factory):
    async with db_factory() as s:
        _book, ch, _ = await _book_with_chapters(s)
        out = await tel_router.chapter_telemetry(ch.id, s)
        assert out.totals.calls == 0 and out.scenes == []


async def test_chapter_telemetry_scopes_to_latest_run(db_factory):
    # The Packets-tab panel must show only the most recent derive run, not a cumulative total across
    # every run ever (the regression that made one patch's effect unreadable).
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        run_old, run_new = uuid.uuid4(), uuid.uuid4()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        s.add_all(
            [
                LlmCall(
                    run_id=run_old,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=999,
                    output_tokens=10,
                    created_at=t0,
                ),
                LlmCall(
                    run_id=run_new,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=100,
                    output_tokens=20,
                    created_at=t0 + timedelta(minutes=5),
                ),
                LlmCall(
                    run_id=run_new,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=2,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=200,
                    output_tokens=30,
                    created_at=t0 + timedelta(minutes=5),
                ),
            ]
        )
        await s.flush()

        out = await tel_router.chapter_telemetry(ch.id, s)
        assert out.totals.calls == 2  # latest run only, not 3
        assert out.totals.input_tokens == 300  # 100 + 200; the old run's 999 is excluded
        assert {sc.scene_no for sc in out.scenes} == {1, 2}
        assert out.run_id == run_new


async def test_book_telemetry_per_run_history_newest_first(db_factory):
    # The Telemetry tab gets a per-run table so each derive/patch is comparable in isolation.
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        run_a, run_b = uuid.uuid4(), uuid.uuid4()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        s.add_all(
            [
                LlmCall(
                    run_id=run_a,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=10,
                    output_tokens=1,
                    created_at=t0,
                ),
                LlmCall(
                    run_id=run_b,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=20,
                    output_tokens=2,
                    created_at=t0 + timedelta(minutes=1),
                ),
            ]
        )
        await s.flush()

        out = await tel_router.book_telemetry(book.id, s)
        assert [r.run_id for r in out.by_run] == [run_b, run_a]  # newest run first
        assert out.by_run[0].calls == 1 and out.by_run[0].chapter_no == 1


async def test_book_telemetry_paginates_run_history(db_factory):
    # The run history paginates (default 5, newest first) so the Telemetry tab never renders an
    # unbounded table; run_total reports the full count so the UI knows when older runs remain.
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        runs = [uuid.uuid4() for _ in range(7)]
        for i, rid in enumerate(runs):
            s.add(
                LlmCall(
                    run_id=rid,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=10,
                    output_tokens=1,
                    created_at=t0 + timedelta(minutes=i),
                )
            )
        await s.flush()

        first = await tel_router.book_telemetry(book.id, s)  # default page
        assert first.run_total == 7
        assert [r.run_id for r in first.by_run] == list(reversed(runs))[:5]  # newest 5, newest first

        page2 = await tel_router.book_telemetry(book.id, s, limit=5, offset=5)
        assert page2.run_total == 7
        assert [r.run_id for r in page2.by_run] == list(reversed(runs))[5:]  # the remaining 2
        # other rollups stay full-book regardless of run paging
        assert page2.totals.calls == 7


async def test_book_telemetry_rolls_up_chapters_stages_models(db_factory):
    async with db_factory() as s:
        book, ch1, ch2 = await _book_with_chapters(s)
        s.add_all(
            [
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch1.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=1000,
                    output_tokens=100,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch1.id,
                    scene_no=1,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=400,
                    output_tokens=40,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch2.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="sonnet",
                    input_tokens=500,
                    output_tokens=50,
                ),
            ]
        )
        await s.flush()

        out = await tel_router.book_telemetry(book.id, s)
        assert out.totals.calls == 3
        # chapters ordered by chapter_no, carrying their labels
        assert [(r.chapter_no, r.title, r.calls) for r in out.by_chapter] == [(1, "One", 2), (2, "Two", 1)]
        assert {g.key for g in out.by_stage} == {"scene_packet_author", "scene_packet_qa"}
        assert {g.key for g in out.by_model} == {"haiku", "sonnet"}
        haiku = next(g for g in out.by_model if g.key == "haiku")
        assert haiku.calls == 2


async def test_run_telemetry_detail(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        run_id = uuid.uuid4()
        s.add_all(
            [
                LlmCall(
                    run_id=run_id,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=100,
                    output_tokens=20,
                    metadata_={"call_index": 0, "max_tokens": 3000},
                ),
                LlmCall(
                    run_id=run_id,
                    book_id=book.id,
                    chapter_id=ch.id,
                    scene_no=1,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=50,
                    output_tokens=10,
                    truncated=True,
                    metadata_={"call_index": 1},
                ),
            ]
        )
        await s.flush()

        out = await tel_router.run_telemetry(run_id, s)
        assert out.totals.calls == 2
        assert out.totals.truncations == 1
        assert len(out.scenes) == 1
        assert out.scenes[0].status == "warn"
        assert len(out.calls) == 2


async def test_list_llm_calls_filters(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        run_id = uuid.uuid4()
        s.add_all(
            [
                LlmCall(
                    run_id=run_id,
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=100,
                    truncated=True,
                ),
                LlmCall(
                    run_id=run_id,
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=50,
                ),
            ]
        )
        await s.flush()

        all_calls = await tel_router.list_llm_calls(s, book_id=book.id)
        assert all_calls.total == 2

        trunc_only = await tel_router.list_llm_calls(s, book_id=book.id, truncated=True)
        assert trunc_only.total == 1
        assert trunc_only.calls[0].truncated is True


async def test_telemetry_problems_truncation(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        s.add(
            LlmCall(
                book_id=book.id,
                chapter_id=ch.id,
                stage="scene_packet_qa",
                model="haiku",
                input_tokens=50,
                truncated=True,
            )
        )
        await s.flush()

        out = await tel_router.book_telemetry_problems(book.id, s)
        assert out.healthy is False
        assert any(p.kind == "truncation" for p in out.problems)


async def test_telemetry_problems_draft_not_ready(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        # No approved chapter packet → draftable is false for the chapter.
        out = await tel_router.book_telemetry_problems(book.id, s)
        assert out.healthy is False
        assert any(p.kind == "draft_not_ready" for p in out.problems)


async def test_compare_runs(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        run_a, run_b = uuid.uuid4(), uuid.uuid4()
        s.add_all(
            [
                LlmCall(
                    run_id=run_a,
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=100,
                ),
                LlmCall(
                    run_id=run_b,
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=200,
                    truncated=True,
                ),
            ]
        )
        await s.flush()

        out = await tel_router.compare_runs(book.id, s, run_a=run_a, run_b=run_b)
        assert out.run_a.input_tokens == 100
        assert out.run_b.truncations == 1


async def test_list_llm_calls_stage_prefix(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        s.add_all(
            [
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_author",
                    model="haiku",
                    input_tokens=100,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="drafter",
                    model="sonnet",
                    input_tokens=200,
                ),
            ]
        )
        await s.flush()

        out = await tel_router.list_llm_calls(s, book_id=book.id, stage_prefix="scene_packet")
        assert out.total == 1
        assert out.calls[0].stage == "scene_packet_author"


async def test_list_llm_calls_problems_only(db_factory):
    async with db_factory() as s:
        book, ch, _ = await _book_with_chapters(s)
        s.add_all(
            [
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="scene_packet_qa",
                    model="haiku",
                    input_tokens=50,
                    truncated=True,
                ),
                LlmCall(
                    book_id=book.id,
                    chapter_id=ch.id,
                    stage="drafter",
                    model="sonnet",
                    input_tokens=200,
                ),
            ]
        )
        await s.flush()

        out = await tel_router.list_llm_calls(s, book_id=book.id, problems_only=True)
        assert out.total == 1
        assert out.calls[0].truncated is True
