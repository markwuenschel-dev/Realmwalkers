"""Production-run attribution + editorial visibility for the Telemetry tab.

Repair/production LLM spend already lands in `llm_calls` (repair revisions run through the same
generate_one_scene -> persist_sink path as drafts), so the dollars are already in the book totals —
these tests cover the *attribution*: the new `production_run_id` soft link and the `job_kind`
metadata tag produce the `by_production_run` and `by_kind` rollups, and `editorial_runs` surfaces the
deterministic ($0) orchestration agents. Router functions are called directly against real Postgres
(like tests/test_telemetry_api.py) and skip when Postgres isn't reachable (see tests/conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from dominion.api.routers.telemetry import book_telemetry
from dominion.shared.models import AgentRun, Book, Chapter, LlmCall, ProductionRun
from dominion.shared.schemas import TelemetryTotals
from dominion.workers.telemetry_agg import _totals

T0 = datetime(2026, 1, 1, tzinfo=UTC)

# ProductionRunRollupOut decoration fields; excluding them leaves exactly the TelemetryTotals.
_PROD_FIELDS = {"production_run_id", "chapter_id", "chapter_no", "status"}
# The endpoint's rollups price via the SQL path (totals_from_model_rows); the reference here prices
# per-call (_totals). Both are correct — cost is linear in tokens — but their intermediate round()s can
# differ by a sub-cent when several calls are summed, so compare the exact token/count fields and the
# USD fields separately (approx). This is the same parity the by_stage/by_model tests rely on.
_COST_FIELDS = {"estimated_cost_usd", "cache_savings_usd"}


def _assert_totals_match(actual, ref_calls: list[LlmCall], *, extra_exclude: set[str]) -> None:
    ref = TelemetryTotals(**_totals(ref_calls))
    assert actual.model_dump(exclude=_COST_FIELDS | extra_exclude) == ref.model_dump(exclude=_COST_FIELDS)
    assert actual.estimated_cost_usd == pytest.approx(ref.estimated_cost_usd, abs=1e-3)
    assert actual.cache_savings_usd == pytest.approx(ref.cache_savings_usd, abs=1e-3)


def _call(book_id: uuid.UUID, **kw) -> LlmCall:
    kw.setdefault("stage", "drafter")
    kw.setdefault("model", "claude-sonnet-4")
    minute = kw.pop("minute", 0)
    return LlmCall(book_id=book_id, created_at=T0 + timedelta(minutes=minute), **kw)


async def _book_calls(s, book_id: uuid.UUID) -> list[LlmCall]:
    return list((await s.execute(select(LlmCall).where(LlmCall.book_id == book_id))).scalars())


async def _seed(s):
    """One chapter, one production run: two draft calls + two revision calls attributed to it, plus a
    derive call with no production_run_id / no job_kind (the attribution must exclude it)."""
    book = Book(title="T")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=3, title="Three", pov="P")
    s.add(ch)
    await s.flush()
    pr = ProductionRun(book_id=book.id, chapter_id=ch.id, status="completed")
    s.add(pr)
    await s.flush()
    s.add_all(
        [
            _call(
                book.id,
                chapter_id=ch.id,
                production_run_id=pr.id,
                scene_no=1,
                minute=0,
                input_tokens=1000,
                output_tokens=300,
                metadata_={"job_kind": "draft"},
            ),
            _call(
                book.id,
                chapter_id=ch.id,
                production_run_id=pr.id,
                scene_no=2,
                minute=1,
                input_tokens=800,
                output_tokens=250,
                metadata_={"job_kind": "draft"},
            ),
            _call(
                book.id,
                chapter_id=ch.id,
                production_run_id=pr.id,
                scene_no=1,
                minute=2,
                model="claude-haiku-4",
                input_tokens=400,
                output_tokens=120,
                metadata_={"job_kind": "revise_full"},
            ),
            _call(
                book.id,
                chapter_id=ch.id,
                production_run_id=pr.id,
                scene_no=1,
                minute=3,
                input_tokens=200,
                output_tokens=60,
                metadata_={"job_kind": "revise_pass"},
            ),
            # Derive/legacy: no production run, no job_kind — must be excluded from both new rollups.
            _call(book.id, chapter_id=ch.id, stage="scene_packet_author", minute=5, input_tokens=50, output_tokens=10),
        ]
    )
    await s.flush()
    return book, ch, pr


async def test_by_production_run_and_by_kind_rollups(db_factory):
    async with db_factory() as s:
        book, _ch, pr = await _seed(s)
        rows = await _book_calls(s, book.id)
        assert len(rows) == 5

        out = await book_telemetry(book.id, s, limit=100, offset=0)

        # by_production_run: exactly the one run, its four attributed calls, labelled from production_runs.
        assert len(out.by_production_run) == 1
        pr_row = out.by_production_run[0]
        assert pr_row.production_run_id == pr.id
        assert pr_row.chapter_no == 3 and pr_row.status == "completed"
        pr_calls = [c for c in rows if c.production_run_id == pr.id]
        assert pr_row.calls == 4
        _assert_totals_match(pr_row, pr_calls, extra_exclude=_PROD_FIELDS)

        # by_kind: draft (2 calls) + revision (revise_full + revise_pass = 2 calls); the derive call
        # (no job_kind) is dropped, so the two buckets sum to 4, not 5.
        by_kind = {g.key: g for g in out.by_kind}
        assert set(by_kind) == {"draft", "revision"}
        draft_ref = [c for c in rows if (c.metadata_ or {}).get("job_kind") == "draft"]
        rev_ref = [c for c in rows if (c.metadata_ or {}).get("job_kind") in ("revise_full", "revise_pass")]
        assert by_kind["draft"].calls == 2
        assert by_kind["revision"].calls == 2
        _assert_totals_match(by_kind["draft"], draft_ref, extra_exclude={"key"})
        _assert_totals_match(by_kind["revision"], rev_ref, extra_exclude={"key"})


async def test_editorial_runs_lists_deterministic_agents_at_zero_cost(db_factory):
    async with db_factory() as s:
        book, _ch, pr = await _seed(s)
        s.add_all(
            [
                AgentRun(
                    production_run_id=pr.id,
                    agent_name="contract_classifier",
                    agent_role="deterministic",
                    stage="contract_classification",
                    status="completed",
                    duration_ms=12,
                    started_at=T0 + timedelta(minutes=1),
                ),
                AgentRun(
                    production_run_id=pr.id,
                    agent_name="repair_scheduler",
                    agent_role="deterministic",
                    stage="repair_scheduling",
                    status="completed",
                    duration_ms=8,
                    started_at=T0 + timedelta(minutes=2),
                ),
            ]
        )
        await s.flush()

        out = await book_telemetry(book.id, s, limit=100, offset=0)

        assert len(out.editorial_runs) == 2
        # Newest started first (repair_scheduler at minute 2 precedes contract_classifier at minute 1).
        assert [e.agent_name for e in out.editorial_runs] == ["repair_scheduler", "contract_classifier"]
        for e in out.editorial_runs:
            assert e.production_run_id == pr.id
            assert e.agent_role == "deterministic"
            assert e.cost_usd == 0.0
            assert e.duration_ms is not None


async def test_editorial_runs_empty_without_production_runs(db_factory):
    async with db_factory() as s:
        book = Book(title="Solo")
        s.add(book)
        await s.flush()
        s.add(_call(book.id, minute=0, input_tokens=10, output_tokens=2))
        await s.flush()

        out = await book_telemetry(book.id, s, limit=100, offset=0)

        assert out.editorial_runs == []
        assert out.by_production_run == []
        assert out.by_kind == []  # no job_kind on the lone derive-style call
