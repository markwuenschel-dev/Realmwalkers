"""Telemetry aggregation endpoints: per-chapter (per-scene breakdown) and per-book (rollups).

Real Postgres (skips if unreachable), router functions called directly with a session — mirrors the
other router tests.
"""
from __future__ import annotations

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
        s.add_all([
            LlmCall(book_id=book.id, chapter_id=ch.id, scene_no=1, stage="scene_packet_author",
                    model="haiku", input_tokens=1000, output_tokens=200, cache_read_tokens=1000,
                    latency_ms=400),
            LlmCall(book_id=book.id, chapter_id=ch.id, scene_no=1, stage="scene_packet_qa",
                    model="haiku", input_tokens=500, output_tokens=100, truncated=True,
                    latency_ms=600),
            LlmCall(book_id=book.id, chapter_id=ch.id, scene_no=2, stage="scene_packet_author",
                    model="sonnet", input_tokens=2000, output_tokens=300, error="boom"),
        ])
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
        assert scene1.avg_latency_ms == 500       # (400 + 600) / 2


async def test_chapter_telemetry_empty_when_never_derived(db_factory):
    async with db_factory() as s:
        _book, ch, _ = await _book_with_chapters(s)
        out = await tel_router.chapter_telemetry(ch.id, s)
        assert out.totals.calls == 0 and out.scenes == []


async def test_book_telemetry_rolls_up_chapters_stages_models(db_factory):
    async with db_factory() as s:
        book, ch1, ch2 = await _book_with_chapters(s)
        s.add_all([
            LlmCall(book_id=book.id, chapter_id=ch1.id, scene_no=1, stage="scene_packet_author",
                    model="haiku", input_tokens=1000, output_tokens=100),
            LlmCall(book_id=book.id, chapter_id=ch1.id, scene_no=1, stage="scene_packet_qa",
                    model="haiku", input_tokens=400, output_tokens=40),
            LlmCall(book_id=book.id, chapter_id=ch2.id, scene_no=1, stage="scene_packet_author",
                    model="sonnet", input_tokens=500, output_tokens=50),
        ])
        await s.flush()

        out = await tel_router.book_telemetry(book.id, s)
        assert out.totals.calls == 3
        # chapters ordered by chapter_no, carrying their labels
        assert [(r.chapter_no, r.title, r.calls) for r in out.by_chapter] == [
            (1, "One", 2), (2, "Two", 1)
        ]
        assert {g.key for g in out.by_stage} == {"scene_packet_author", "scene_packet_qa"}
        assert {g.key for g in out.by_model} == {"haiku", "sonnet"}
        haiku = next(g for g in out.by_model if g.key == "haiku")
        assert haiku.calls == 2
