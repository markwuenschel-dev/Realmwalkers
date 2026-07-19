"""SQL-aggregation parity tests for the telemetry endpoints.

The book/chapter/problems/compare endpoints aggregate llm_calls in SQL instead of loading every row;
these seed a small exhaust and assert the SQL rollups match the in-memory reference (`_totals`/
`group_calls`) the responses were originally computed with, plus real LIMIT/OFFSET pagination for
by_run. Router functions are called directly against real Postgres (like tests/test_desk_api.py) and
skip automatically when Postgres isn't reachable (see tests/conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_scene_packet
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers.telemetry import (
    book_telemetry,
    book_telemetry_problems,
    chapter_telemetry,
    compare_runs,
    run_telemetry,
)
from dominion.shared.models import Book, Chapter, LlmCall
from dominion.shared.schemas import TelemetryProblemOut, TelemetryTotals
from dominion.workers.telemetry_agg import _totals, group_calls
from dominion.workers.telemetry_diagnostics import build_problems
from dominion.workers.telemetry_draft_problems import detect_draft_not_ready

T0 = datetime(2026, 1, 1, tzinfo=UTC)

# RunRollupOut/ChapterRollupOut decoration fields; excluding them leaves exactly the TelemetryTotals.
_RUN_FIELDS = {"run_id", "started_at", "chapter_id", "chapter_no", "title"}
_CHAPTER_FIELDS = {"chapter_id", "chapter_no", "title"}


def _call(book_id: uuid.UUID, **kw) -> LlmCall:
    kw.setdefault("stage", "drafter")
    kw.setdefault("model", "claude-sonnet-4")
    minute = kw.pop("minute", 0)
    return LlmCall(book_id=book_id, created_at=T0 + timedelta(minutes=minute), **kw)


async def _seed(s):
    """Two chapters, two runs plus one legacy row (no run_id), mixed models/cache/latency/problems."""
    book = Book(title="T")
    s.add(book)
    await s.flush()
    ch1 = Chapter(book_id=book.id, chapter_no=1, title="One", pov="P")
    ch2 = Chapter(book_id=book.id, chapter_no=2, title="Two", pov="P")
    s.add_all([ch1, ch2])
    await s.flush()
    run1, run2 = uuid.uuid4(), uuid.uuid4()
    s.add_all(
        [
            _call(
                book.id,
                chapter_id=ch1.id,
                run_id=run1,
                scene_no=1,
                stage="scene_packet_author",
                minute=0,
                input_tokens=1000,
                output_tokens=200,
                cache_creation_tokens=6000,
                latency_ms=1200,
            ),
            _call(
                book.id,
                chapter_id=ch1.id,
                run_id=run1,
                scene_no=1,
                stage="scene_packet_qa",
                model="claude-haiku-4",
                minute=1,
                input_tokens=500,
                output_tokens=50,
                cache_read_tokens=400,
                latency_ms=800,
                truncated=True,
                metadata_={"fallback_attempt": True, "max_tokens": 999, "stop_reason": "max_tokens"},
            ),
            _call(book.id, minute=5, stage="summary", input_tokens=10, output_tokens=5),
            _call(
                book.id,
                chapter_id=ch2.id,
                run_id=run2,
                scene_no=2,
                minute=10,
                model="claude-opus-4",
                input_tokens=2000,
                output_tokens=900,
                cache_read_tokens=1500,
                error="boom",
            ),
        ]
    )
    await s.flush()
    return book, ch1, ch2, run1, run2


async def _book_rows(s, book_id: uuid.UUID) -> list[LlmCall]:
    stmt = select(LlmCall).where(LlmCall.book_id == book_id).order_by(LlmCall.created_at)
    return list((await s.execute(stmt)).scalars())


async def test_book_telemetry_sql_rollups_match_python_reference(db_factory):
    async with db_factory() as s:
        book, ch1, ch2, run1, run2 = await _seed(s)
        rows = await _book_rows(s, book.id)

        out = await book_telemetry(book.id, s, limit=100, offset=0)

        assert out.totals == TelemetryTotals(**_totals(rows))

        # Independent literal oracle over the four seeded rows, hand-computed WITHOUT `_totals`, so a
        # common-mode bug in the shared aggregation helper (a wrong cache-hit / latency / count rule
        # that the SQL and Python sides would BOTH reproduce) is caught -- the SQL==helper parity above
        # only proves the two agree, not that either is correct. Seed rows (see `_seed`):
        #   author (run1): in=1000 out=200 cc=6000 cr=0    lat=1200 trunc=F err=-    fb=-
        #   qa     (run1): in= 500 out= 50 cc=0    cr=400  lat= 800 trunc=T err=-    fb=fallback_attempt
        #   summary(-   ): in=  10 out=  5 cc=0    cr=0    lat=None trunc=F err=-    fb=-
        #   drafter(run2): in=2000 out=900 cc=0    cr=1500 lat=None trunc=F err=boom fb=-
        #   input_tokens          = 1000 + 500 + 10 + 2000 = 3510
        #   output_tokens         =  200 +  50 +  5 +  900 = 1155
        #   cache_creation_tokens = 6000 (author row only)
        #   cache_read_tokens     = 400 + 1500 = 1900
        #   prompt tokens         = 3510 + 6000 + 1900 = 11410
        #   cache_hit_ratio       = round(1900 / 11410, 3) = 0.167
        #   cache_tokens_saved    = int(1900 * 0.9) = 1710
        #   truncations = 1 (qa), errors = 1 (drafter), fallbacks = 1 (qa fallback_attempt)
        #   avg_latency_ms        = int((1200 + 800) / 2) = 1000  (summary & drafter rows have no latency)
        expected_book_totals = {
            "calls": 4,
            "input_tokens": 3510,
            "output_tokens": 1155,
            "cache_creation_tokens": 6000,
            "cache_read_tokens": 1900,
            "cache_hit_ratio": 0.167,
            "cache_tokens_saved": 1710,
            "truncations": 1,
            "errors": 1,
            "fallbacks": 1,
            "avg_latency_ms": 1000,
        }
        # estimated_cost_usd/cache_savings_usd derive from external per-model pricing tables (not the
        # aggregation rules under test here), so they remain covered by the SQL==helper assertion above.
        _cost_fields = {"estimated_cost_usd", "cache_savings_usd"}
        assert out.totals.model_dump(exclude=_cost_fields) == expected_book_totals  # SQL rollup
        assert TelemetryTotals(**_totals(rows)).model_dump(exclude=_cost_fields) == expected_book_totals  # helper
        for groups, key in ((out.by_stage, lambda c: c.stage), (out.by_model, lambda c: c.model)):
            ref = dict(group_calls(rows, key))
            assert {g.key for g in groups} == set(ref)
            for g in groups:
                assert g.model_dump(exclude={"key"}) == TelemetryTotals(**ref[g.key]).model_dump()
        # group_calls ordered buckets by call count descending
        assert [g.calls for g in out.by_stage] == sorted((g.calls for g in out.by_stage), reverse=True)

        by_ch = {r.chapter_id: r for r in out.by_chapter}
        for ch in (ch1, ch2):
            ref_t = TelemetryTotals(**_totals([c for c in rows if c.chapter_id == ch.id]))
            assert by_ch[ch.id].model_dump(exclude=_CHAPTER_FIELDS) == ref_t.model_dump()
        assert [r.chapter_no for r in out.by_chapter] == [1, 2]
        assert by_ch[ch1.id].title == "One"

        # per-run rollups: newest started first, legacy no-run bucket included
        assert out.run_total == 3
        assert [r.run_id for r in out.by_run] == [run2, None, run1]
        for r in out.by_run:
            ref_t = TelemetryTotals(**_totals([c for c in rows if c.run_id == r.run_id]))
            assert r.model_dump(exclude=_RUN_FIELDS) == ref_t.model_dump()
        assert out.by_run[0].started_at == T0 + timedelta(minutes=10)
        assert out.by_run[0].chapter_no == 2 and out.by_run[0].title == "Two"


async def test_book_telemetry_paginates_runs_in_sql(db_factory):
    async with db_factory() as s:
        book, _ch1, _ch2, run1, run2 = await _seed(s)

        pages = [await book_telemetry(book.id, s, limit=1, offset=i) for i in range(3)]

        assert all(p.run_total == 3 for p in pages)
        assert all(len(p.by_run) == 1 for p in pages)
        assert [p.by_run[0].run_id for p in pages] == [run2, None, run1]
        assert (await book_telemetry(book.id, s, limit=5, offset=3)).by_run == []


async def test_chapter_telemetry_scopes_to_latest_run_in_sql(db_factory):
    async with db_factory() as s:
        book, ch1, _ch2, _run1, _run2 = await _seed(s)
        run3 = uuid.uuid4()
        late = _call(book.id, chapter_id=ch1.id, run_id=run3, scene_no=1, minute=20, input_tokens=42, output_tokens=7)
        s.add(late)
        await s.flush()

        out = await chapter_telemetry(ch1.id, s)

        assert out.run_id == run3
        assert out.totals == TelemetryTotals(**_totals([late]))
        assert [sc.scene_no for sc in out.scenes] == [1] and out.scenes[0].calls == 1


async def test_run_telemetry_batched_links_resolve_scene_packet(db_factory):
    async with db_factory() as s:
        book, ch1, _ch2, run1, _run2 = await _seed(s)
        sp = await seed_scene_packet(s, chapter=ch1, beat=None)

        out = await run_telemetry(run1, s)

        rows = [c for c in (await s.execute(select(LlmCall).where(LlmCall.run_id == run1))).scalars()]
        assert out.totals == TelemetryTotals(**_totals(rows))
        assert len(out.calls) == 2
        assert all(c.links.scene_packet_id == sp.id for c in out.calls)  # both calls are scene 1
        assert all(c.links.chapter_id == ch1.id and c.links.run_id == run1 for c in out.calls)
        assert all(c.links.scene_id is None and c.links.job_id is None for c in out.calls)


async def test_problems_prefilter_matches_full_scan_reference(db_factory):
    async with db_factory() as s:
        book, _ch1, _ch2, run1, _run2 = await _seed(s)
        # exercise the remaining prefilter arms: short prime, prime with a cache read (which makes
        # run1's zero-read author a cache_miss_after_prime), budget overage, token-count fallback, slow call
        s.add_all(
            [
                _call(book.id, run_id=run1, stage="scene_packet_author_prefix_prime", minute=30, input_tokens=100),
                _call(
                    book.id,
                    run_id=run1,
                    stage="scene_packet_qa_prefix_prime",
                    minute=31,
                    input_tokens=5000,
                    cache_read_tokens=10,
                ),
                _call(
                    book.id,
                    run_id=run1,
                    minute=32,
                    metadata_={
                        "budget_soft_exceeded": True,
                        "budget_used_after_charge": 60043,
                        "budget_soft_limit": 60000,
                    },
                ),
                _call(
                    book.id,
                    run_id=run1,
                    minute=33,
                    metadata_={"token_count_method": "local_estimate", "token_count_error": "api down"},
                ),
                _call(book.id, run_id=run1, minute=34, latency_ms=45_000),
            ]
        )
        await s.flush()
        rows = await _book_rows(s, book.id)

        out = await book_telemetry_problems(book.id, s)

        expected = build_problems(rows, [])
        draft = await detect_draft_not_ready(s, book.id)  # endpoint appends this too
        if draft:
            expected.append(draft)
        assert [p.model_dump() for p in out.problems] == [TelemetryProblemOut(**p).model_dump() for p in expected]
        assert {p.kind for p in out.problems} >= {
            "truncation",
            "error",
            "soft_work_budget_exceeded",
            "token_count_fallback",
            "cache_prime_short",
            "cache_miss_after_prime",
            "high_latency",
        }
        assert not out.healthy


async def test_compare_runs_sql_deltas_match_reference(db_factory):
    async with db_factory() as s:
        book, _ch1, _ch2, run1, run2 = await _seed(s)
        rows = await _book_rows(s, book.id)
        calls_a = [c for c in rows if c.run_id == run1]
        calls_b = [c for c in rows if c.run_id == run2]

        out = await compare_runs(book.id, s, run_a=run1, run_b=run2)

        assert out.run_a.model_dump(exclude=_RUN_FIELDS) == TelemetryTotals(**_totals(calls_a)).model_dump()
        assert out.run_b.model_dump(exclude=_RUN_FIELDS) == TelemetryTotals(**_totals(calls_b)).model_dump()
        assert out.run_b.chapter_no == 2 and out.run_b.title == "Two"
        stages_a = dict(group_calls(calls_a, lambda c: c.stage))
        stages_b = dict(group_calls(calls_b, lambda c: c.stage))
        assert {d.stage for d in out.stage_deltas} == set(stages_a) | set(stages_b)
        for d in out.stage_deltas:
            for field, delta in (
                ("calls", d.calls_delta),
                ("input_tokens", d.input_tokens_delta),
                ("output_tokens", d.output_tokens_delta),
                ("truncations", d.truncations_delta),
            ):
                assert delta == stages_b.get(d.stage, {}).get(field, 0) - stages_a.get(d.stage, {}).get(field, 0)

        with pytest.raises(HTTPException) as exc:
            await compare_runs(book.id, s, run_a=run1, run_b=uuid.uuid4())
        assert exc.value.status_code == 404
