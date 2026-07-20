"""Shared aggregation helpers for telemetry API responses."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.model_pricing import pricing_for_model
from dominion.shared.models import AgentRun, Chapter, LlmCall, ProductionRun
from dominion.shared.reviewer_telemetry import LEGACY_REVIEWERS_STAGE, REVIEWER_TELEMETRY_STAGES
from dominion.workers.telemetry_cost import (
    estimate_cache_savings_usd,
    estimate_call_cost_usd,
    estimate_calls_cost_usd,
)

# Canonical pipeline order for scene timeline display.
PIPELINE_STAGE_ORDER: tuple[str, ...] = (
    "scene_packet_author_prefix_prime",
    "scene_packet_qa_prefix_prime",
    "scene_packet_author",
    "scene_packet_qa",
    "drafter",
    *REVIEWER_TELEMETRY_STAGES,
    LEGACY_REVIEWERS_STAGE,
    "enrichment",
    "length",
    "packet_author",
    "packet_qa",
    "beats",
    "chapter_title",
    "summary",
)

PRIME_STAGES = frozenset({"scene_packet_author_prefix_prime", "scene_packet_qa_prefix_prime"})


def call_metadata(call: LlmCall) -> dict[str, Any]:
    return dict(call.metadata_ or {})


def _totals(calls: Iterable[LlmCall]) -> dict[str, Any]:
    calls = list(calls)
    input_t = sum(c.input_tokens for c in calls)
    cc = sum(c.cache_creation_tokens for c in calls)
    cr = sum(c.cache_read_tokens for c in calls)
    prompt = input_t + cc + cr
    latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
    cost = estimate_calls_cost_usd(calls)
    cache_saved_usd = estimate_cache_savings_usd(calls)
    fallbacks = sum(1 for c in calls if call_metadata(c).get("fallback_attempt"))
    return dict(
        calls=len(calls),
        input_tokens=input_t,
        output_tokens=sum(c.output_tokens for c in calls),
        cache_creation_tokens=cc,
        cache_read_tokens=cr,
        cache_hit_ratio=round(cr / prompt, 3) if prompt else 0.0,
        cache_tokens_saved=int(cr * 0.9),
        truncations=sum(1 for c in calls if c.truncated),
        errors=sum(1 for c in calls if c.error),
        fallbacks=fallbacks,
        avg_latency_ms=int(sum(latencies) / len(latencies)) if latencies else None,
        estimated_cost_usd=round(cost, 4),
        cache_savings_usd=round(cache_saved_usd, 4),
    )


def totals_from_model_rows(rows: Iterable[Any]) -> dict[str, Any]:
    """`_totals` computed from SQL-side per-model aggregate rows instead of materialized calls.

    Each row must carry `model` plus the summed columns (calls, token sums, truncations/errors/
    fallbacks counts, latency_sum, latency_count). Pricing is linear in tokens, so applying each
    model's rates to that model's sums yields the same cost/savings as pricing every call.
    """
    rows = list(rows)
    input_t = sum(r.input_tokens for r in rows)
    cc = sum(r.cache_creation_tokens for r in rows)
    cr = sum(r.cache_read_tokens for r in rows)
    prompt = input_t + cc + cr
    latency_sum = sum(r.latency_sum or 0 for r in rows)
    latency_count = sum(r.latency_count for r in rows)
    cost = 0.0
    cache_saved_usd = 0.0
    for r in rows:
        tier = pricing_for_model(r.model)
        cost += (
            estimate_call_cost_usd(model=r.model, input_tokens=r.input_tokens, output_tokens=r.output_tokens)
            + r.cache_creation_tokens * tier.cache_write / 1_000_000
            + r.cache_read_tokens * tier.cache_read / 1_000_000
        )
        cache_saved_usd += max(0.0, r.cache_read_tokens * (tier.input - tier.cache_read) / 1_000_000)
    return dict(
        calls=sum(r.calls for r in rows),
        input_tokens=input_t,
        output_tokens=sum(r.output_tokens for r in rows),
        cache_creation_tokens=cc,
        cache_read_tokens=cr,
        cache_hit_ratio=round(cr / prompt, 3) if prompt else 0.0,
        cache_tokens_saved=int(cr * 0.9),
        truncations=sum(r.truncations for r in rows),
        errors=sum(r.errors for r in rows),
        fallbacks=sum(r.fallbacks for r in rows),
        avg_latency_ms=int(latency_sum / latency_count) if latency_count else None,
        estimated_cost_usd=round(cost, 4),
        cache_savings_usd=round(cache_saved_usd, 4),
    )


def group_calls(calls: list[LlmCall], key: Callable[[LlmCall], object]) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[LlmCall]] = {}
    for c in calls:
        k = key(c)
        if k:
            buckets.setdefault(str(k), []).append(c)
    groups = [(k, _totals(v)) for k, v in buckets.items()]
    return sorted(groups, key=lambda kv: kv[1]["calls"], reverse=True)


def scene_status(calls: list[LlmCall]) -> str:
    if any(c.error for c in calls):
        return "error"
    if any(c.truncated for c in calls):
        return "warn"
    return "ok"


def scene_stages(calls: list[LlmCall]) -> list[str]:
    present = {c.stage for c in calls}
    ordered = [s for s in PIPELINE_STAGE_ORDER if s in present]
    extras = sorted(present - set(ordered))
    return ordered + extras


def pipeline_steps(calls: list[LlmCall]) -> list[dict[str, Any]]:
    by_stage: dict[str, list[LlmCall]] = {}
    for c in calls:
        by_stage.setdefault(c.stage, []).append(c)
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stage in PIPELINE_STAGE_ORDER:
        if stage not in by_stage:
            continue
        seen.add(stage)
        sc = by_stage[stage]
        steps.append(
            {
                "stage": stage,
                "calls": len(sc),
                "truncations": sum(1 for x in sc if x.truncated),
                "errors": sum(1 for x in sc if x.error),
                **_totals(sc),
            }
        )
    for stage in sorted(by_stage.keys() - seen):
        sc = by_stage[stage]
        steps.append(
            {
                "stage": stage,
                "calls": len(sc),
                "truncations": sum(1 for x in sc if x.truncated),
                "errors": sum(1 for x in sc if x.error),
                **_totals(sc),
            }
        )
    return steps


def scene_stage_summary(calls: list[LlmCall]) -> str:
    """Compact per-scene status like 'author ok, QA ok, draft truncated'."""
    by_stage: dict[str, list[LlmCall]] = {}
    for c in calls:
        by_stage.setdefault(c.stage, []).append(c)
    labels: list[str] = []
    for stage in scene_stages(calls):
        short = stage.replace("scene_packet_", "").replace("_", " ")
        sc = by_stage[stage]
        if any(x.error for x in sc):
            labels.append(f"{short} err")
        elif any(x.truncated for x in sc):
            labels.append(f"{short} trunc")
        else:
            labels.append(f"{short} ok")
    return ", ".join(labels)


def sort_calls(calls: list[LlmCall]) -> list[LlmCall]:
    def _key(c: LlmCall) -> tuple[int, Any]:
        meta = call_metadata(c)
        idx = meta.get("call_index")
        return (idx if isinstance(idx, int) else 999999, c.created_at)

    return sorted(calls, key=_key)


# --- SQL-side rollups for the book/compare telemetry endpoints -----------------------------------
# These aggregate the append-only llm_calls exhaust in SQL (never materializing per-call rows) and
# return plain dicts / a small dataclass. The router wraps them into the `*Out` response schemas and
# owns all 404s, so this module stays fastapi-free and schema-free.

# Editorial-pipeline activity is a bounded recent feed (deterministic $0 agents can be numerous over a
# book's life; the tab only needs the recent ones to show the pipeline is working).
_EDITORIAL_RUN_LIMIT = 50

# Job kinds (metadata->>'job_kind') that make up each side of the draft-vs-revision split. Anything
# else (derive/planning calls with no job_kind) is neither and excluded — this is the original-drafting
# vs repair comparison, not the whole book's spend.
_KIND_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("draft", ("draft",)),
    ("revision", ("revise_full", "revise_pass")),
)


def agg_cols():
    """Aggregate columns for SQL-side rollups (the SELECT twin of `_totals`'s per-call sums). Grouping
    always keeps `model` as the innermost dimension so per-model pricing applies to the sums — cost is
    linear in tokens, so summed tokens price identically to per-call pricing (totals_from_model_rows)."""
    return (
        func.count().label("calls"),
        func.coalesce(func.sum(LlmCall.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmCall.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmCall.cache_creation_tokens), 0).label("cache_creation_tokens"),
        func.coalesce(func.sum(LlmCall.cache_read_tokens), 0).label("cache_read_tokens"),
        func.count().filter(LlmCall.truncated.is_(True)).label("truncations"),
        # Python treated empty-string errors as "no error" (`if c.error`), so the filter does too.
        func.count().filter(LlmCall.error.isnot(None), LlmCall.error != "").label("errors"),
        func.count().filter(LlmCall.metadata_["fallback_attempt"].astext == "true").label("fallbacks"),
        # Sum + count (NULLs excluded) instead of AVG so avg_latency_ms keeps `int(sum/len)` semantics.
        func.sum(LlmCall.latency_ms).label("latency_sum"),
        func.count(LlmCall.latency_ms).label("latency_count"),
    )


def group_model_rows(rows: Sequence[Any], key: str) -> list[tuple[str, dict[str, Any]]]:
    """SQL twin of `group_calls`: bucket per-model aggregate rows by `key` (skipping empty keys, like
    group_calls), ordered by call count descending. Returns the same `(key, totals)` shape as
    `group_calls`; the router wraps each into a `TelemetryGroupOut`."""
    buckets: dict[str, list[Any]] = {}
    for r in rows:
        k = getattr(r, key)
        if k:
            buckets.setdefault(str(k), []).append(r)
    return sorted(
        ((k, totals_from_model_rows(v)) for k, v in buckets.items()),
        key=lambda kv: kv[1]["calls"],
        reverse=True,
    )


@dataclass
class BookTelemetryRollups:
    """Plain-data result of `book_telemetry_rollups`. `totals` and each decorated list are dicts (the
    router maps them to `TelemetryTotals` / `*RollupOut`); `by_stage`/`by_model`/`by_kind` are
    `(key, totals)` tuples the router wraps into `TelemetryGroupOut`."""

    totals: dict[str, Any]
    by_chapter: list[dict[str, Any]]
    by_run: list[dict[str, Any]]
    run_total: int
    by_stage: list[tuple[str, dict[str, Any]]]
    by_model: list[tuple[str, dict[str, Any]]]
    by_production_run: list[dict[str, Any]]
    by_kind: list[tuple[str, dict[str, Any]]]
    editorial_runs: list[dict[str, Any]]


async def book_telemetry_rollups(
    session: AsyncSession,
    book_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> BookTelemetryRollups:
    """All book-level telemetry rollups, aggregated in SQL. Behaviour-preserving extraction of the
    former `book_telemetry` body; returns dicts/tuples the router wraps into `BookTelemetryOut`."""
    chapters = {
        ch.id: ch for ch in (await session.execute(select(Chapter).where(Chapter.book_id == book_id))).scalars()
    }

    # All rollups aggregate in SQL — the append-only exhaust is never materialized row-by-row. The
    # (stage, model) grouping serves by_stage, by_model, and (rolled all the way up) the book totals.
    stage_model_rows = (
        await session.execute(
            select(LlmCall.stage, LlmCall.model, *agg_cols())
            .where(LlmCall.book_id == book_id)
            .group_by(LlmCall.stage, LlmCall.model)
        )
    ).all()

    chapter_model_rows = (
        await session.execute(
            select(LlmCall.chapter_id, LlmCall.model, *agg_cols())
            .where(LlmCall.book_id == book_id, LlmCall.chapter_id.isnot(None))
            .group_by(LlmCall.chapter_id, LlmCall.model)
        )
    ).all()
    by_chapter_rows: dict[uuid.UUID, list[Any]] = {}
    for r in chapter_model_rows:
        by_chapter_rows.setdefault(r.chapter_id, []).append(r)
    by_chapter = [
        {
            "chapter_id": cid,
            "chapter_no": (ch.chapter_no if (ch := chapters.get(cid)) else None),
            "title": (chapters[cid].title if cid in chapters else None),
            **totals_from_model_rows(agg_rows),
        }
        for cid, agg_rows in by_chapter_rows.items()
    ]
    by_chapter.sort(key=lambda r: (r["chapter_no"] is None, r["chapter_no"]))

    # by_run pages in SQL: newest-started runs first (run_id tiebreak keeps pages stable), with the
    # unsliced group count as run_total — only the requested page's runs get aggregated.
    run_groups = (
        select(LlmCall.run_id, func.min(LlmCall.created_at).label("started_at"))
        .where(LlmCall.book_id == book_id)
        .group_by(LlmCall.run_id)
    )
    run_total = (await session.execute(select(func.count()).select_from(run_groups.subquery()))).scalar_one()
    page = (
        await session.execute(
            run_groups.order_by(func.min(LlmCall.created_at).desc().nulls_last(), LlmCall.run_id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    by_run: list[dict[str, Any]] = []
    if page:
        page_ids = [r.run_id for r in page]
        run_match = LlmCall.run_id.in_([rid for rid in page_ids if rid is not None])
        if None in page_ids:  # legacy rows predate run_id
            run_match = or_(run_match, LlmCall.run_id.is_(None))
        run_model_rows = (
            await session.execute(
                select(LlmCall.run_id, LlmCall.chapter_id, LlmCall.model, *agg_cols())
                .where(LlmCall.book_id == book_id, run_match)
                .group_by(LlmCall.run_id, LlmCall.chapter_id, LlmCall.model)
            )
        ).all()
        by_run_rows: dict[uuid.UUID | None, list[Any]] = {}
        for r in run_model_rows:
            by_run_rows.setdefault(r.run_id, []).append(r)
        for page_row in page:
            agg_rows = by_run_rows.get(page_row.run_id, [])
            cid = next((r.chapter_id for r in agg_rows if r.chapter_id is not None), None)
            ch = chapters.get(cid) if cid is not None else None
            by_run.append(
                {
                    "run_id": page_row.run_id,
                    "started_at": page_row.started_at,
                    "chapter_id": cid,
                    "chapter_no": ch.chapter_no if ch else None,
                    "title": ch.title if ch else None,
                    **totals_from_model_rows(agg_rows),
                }
            )

    # by_production_run: the already-captured draft+repair spend, grouped by the soft production_run_id
    # link. Same measures as by_run; status/chapter_no come from production_runs (cheap join in Python).
    prod_model_rows = (
        await session.execute(
            select(LlmCall.production_run_id, LlmCall.chapter_id, LlmCall.model, *agg_cols())
            .where(LlmCall.book_id == book_id, LlmCall.production_run_id.isnot(None))
            .group_by(LlmCall.production_run_id, LlmCall.chapter_id, LlmCall.model)
        )
    ).all()
    by_production_run: list[dict[str, Any]] = []
    if prod_model_rows:
        prod_runs = {
            pr.id: pr
            for pr in (await session.execute(select(ProductionRun).where(ProductionRun.book_id == book_id))).scalars()
        }
        by_prod_rows: dict[uuid.UUID, list[Any]] = {}
        for r in prod_model_rows:
            by_prod_rows.setdefault(r.production_run_id, []).append(r)
        for prid, agg_rows in by_prod_rows.items():
            pr = prod_runs.get(prid)
            cid = pr.chapter_id if pr else next((r.chapter_id for r in agg_rows if r.chapter_id is not None), None)
            ch = chapters.get(cid) if cid is not None else None
            by_production_run.append(
                {
                    "production_run_id": prid,
                    "chapter_id": cid,
                    "chapter_no": ch.chapter_no if ch else None,
                    "status": pr.status if pr else None,
                    **totals_from_model_rows(agg_rows),
                }
            )
        by_production_run.sort(key=lambda r: (r["estimated_cost_usd"], r["calls"]), reverse=True)

    # by_kind: draft vs revision (from metadata->>'job_kind'). One small per-bucket query grouped by
    # model only — the job_kind filter lives in WHERE, so nothing groups by a JSON expression (which
    # renders mismatched bound params in SELECT vs GROUP BY and trips Postgres). Empty buckets drop out.
    by_kind: list[tuple[str, dict[str, Any]]] = []
    for label, kinds in _KIND_BUCKETS:
        kind_rows = (
            await session.execute(
                select(LlmCall.model, *agg_cols())
                .where(LlmCall.book_id == book_id, LlmCall.metadata_["job_kind"].astext.in_(kinds))
                .group_by(LlmCall.model)
            )
        ).all()
        if kind_rows:
            by_kind.append((label, totals_from_model_rows(kind_rows)))
    by_kind.sort(key=lambda kv: kv[1]["calls"], reverse=True)

    # editorial_runs: the deterministic orchestration agents that ran for this book's production runs.
    # No LLM call, no cost — this is pipeline-activity visibility, bounded to the recent feed.
    editorial_rows = (
        await session.execute(
            select(
                AgentRun.production_run_id,
                AgentRun.agent_name,
                AgentRun.agent_role,
                AgentRun.stage,
                AgentRun.status,
                AgentRun.duration_ms,
                AgentRun.started_at,
            )
            .join(ProductionRun, ProductionRun.id == AgentRun.production_run_id)
            .where(ProductionRun.book_id == book_id)
            .order_by(func.coalesce(AgentRun.started_at, AgentRun.created_at).desc())
            .limit(_EDITORIAL_RUN_LIMIT)
        )
    ).all()
    editorial_runs = [
        {
            "production_run_id": r.production_run_id,
            "agent_name": r.agent_name,
            "agent_role": r.agent_role,
            "stage": r.stage,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "started_at": r.started_at,
            "cost_usd": 0.0,
        }
        for r in editorial_rows
    ]

    return BookTelemetryRollups(
        totals=totals_from_model_rows(stage_model_rows),
        by_chapter=by_chapter,
        by_run=by_run,
        run_total=run_total,
        by_stage=group_model_rows(stage_model_rows, "stage"),
        by_model=group_model_rows(stage_model_rows, "model"),
        by_production_run=by_production_run,
        by_kind=by_kind,
        editorial_runs=editorial_runs,
    )


async def compare_run_rows(session: AsyncSession, run_id: uuid.UUID) -> Sequence[Any]:
    """One grouped query per run, serving both the rollup and the per-stage deltas. Returns `[]` for a
    run with no calls — the router raises the 404, preserving the original endpoint's behaviour."""
    return (
        await session.execute(
            select(
                LlmCall.stage,
                LlmCall.chapter_id,
                LlmCall.model,
                *agg_cols(),
                func.min(LlmCall.created_at).label("started_at"),
            )
            .where(LlmCall.run_id == run_id)
            .group_by(LlmCall.stage, LlmCall.chapter_id, LlmCall.model)
        )
    ).all()


def compare_rollup(run_id: uuid.UUID, rows: Sequence[Any], chapters: dict[uuid.UUID, Any]) -> dict[str, Any]:
    """Run-level rollup dict (the RunRollupOut fields minus the schema wrap). `chapters` maps chapter
    id -> Chapter for the chapter_no/title decoration; the router wraps the result into RunRollupOut."""
    cid = next((r.chapter_id for r in rows if r.chapter_id is not None), None)
    ch = chapters.get(cid) if cid else None
    return dict(
        run_id=run_id,
        started_at=min(r.started_at for r in rows),
        chapter_id=cid,
        chapter_no=ch.chapter_no if ch else None,
        title=ch.title if ch else None,
        **totals_from_model_rows(rows),
    )


def compare_stage_delta_sums(rows: Sequence[Any]) -> dict[str, dict[str, int]]:
    """Per-stage sums for one run (calls/input/output/truncations), keyed by stage. The router diffs
    two of these into the `StageDeltaOut` list. Empty stage keys are skipped, like group_calls."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        if not r.stage:  # group_calls skipped empty keys
            continue
        t = out.setdefault(str(r.stage), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "truncations": 0})
        t["calls"] += r.calls
        t["input_tokens"] += r.input_tokens
        t["output_tokens"] += r.output_tokens
        t["truncations"] += r.truncations
    return out
