"""LLM-call telemetry endpoints (persisted cost/cache/health, aggregated for the Desk).

`llm_calls` is append-only per-call exhaust written by the instrumented orchestrators. These read-only
endpoints aggregate it for the global Telemetry tab and expose drill-down detail (runs, calls,
problems) for the debugging console.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select, tuple_

from dominion.api.deps import SessionDep
from dominion.api.telemetry_delete import (
    delete_all_telemetry,
    delete_book_telemetry,
    delete_run_telemetry,
)
from dominion.shared.enums import JobStatus
from dominion.shared.job_policy import scope_jobs_to_book
from dominion.shared.models import AgentRun, Chapter, Job, LlmCall, ProductionRun, Scene, ScenePacket
from dominion.shared.schemas import (
    BookTelemetryOut,
    ChapterRollupOut,
    ChapterTelemetryOut,
    EditorialAgentRunOut,
    GlobalTelemetryDeleteIn,
    LlmCallLinksOut,
    LlmCallListOut,
    LlmCallOut,
    PipelineStepOut,
    ProductionRunRollupOut,
    RunCompareOut,
    RunRollupOut,
    RunTelemetryOut,
    SceneTelemetryOut,
    StageDeltaOut,
    TelemetryDeleteOut,
    TelemetryGroupOut,
    TelemetryProblemOut,
    TelemetryProblemsOut,
    TelemetryTotals,
)
from dominion.workers.telemetry_agg import (
    _totals,
    call_metadata,
    group_calls,
    pipeline_steps,
    scene_stage_summary,
    scene_stages,
    scene_status,
    sort_calls,
    totals_from_model_rows,
)
from dominion.workers.telemetry_cost import estimate_calls_cost_usd
from dominion.workers.telemetry_diagnostics import build_problems, problem_call_criteria
from dominion.workers.telemetry_draft_problems import detect_draft_not_ready

log = structlog.get_logger()
router = APIRouter(tags=["telemetry"])

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


def _group(calls: list[LlmCall], key: Callable[[LlmCall], object]) -> list[TelemetryGroupOut]:
    return [TelemetryGroupOut(key=k, **t) for k, t in group_calls(calls, key)]


def _agg_cols():
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


def _group_rows(rows: Sequence[Any], key: str) -> list[TelemetryGroupOut]:
    """SQL twin of `_group`: bucket per-model aggregate rows by `key` (skipping empty keys, like
    group_calls), ordered by call count descending."""
    buckets: dict[str, list[Any]] = {}
    for r in rows:
        k = getattr(r, key)
        if k:
            buckets.setdefault(str(k), []).append(r)
    groups = sorted(
        ((k, totals_from_model_rows(v)) for k, v in buckets.items()),
        key=lambda kv: kv[1]["calls"],
        reverse=True,
    )
    return [TelemetryGroupOut(key=k, **t) for k, t in groups]


def _scene_out(scene_no: int | None, calls: list[LlmCall]) -> SceneTelemetryOut:
    latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
    return SceneTelemetryOut(
        scene_no=scene_no,
        models=sorted({c.model for c in calls}),
        status=scene_status(calls),
        stages=scene_stages(calls),
        worst_latency_ms=max(latencies) if latencies else None,
        stage_summary=scene_stage_summary(calls),
        pipeline=[
            PipelineStepOut(stage=s["stage"], **{k: v for k, v in s.items() if k != "stage"})
            for s in pipeline_steps(calls)
        ],
        **_totals(calls),
    )


async def _resolve_links(session: SessionDep, call: LlmCall) -> LlmCallLinksOut:
    links = LlmCallLinksOut(
        chapter_id=call.chapter_id,
        run_id=call.run_id,
    )
    if call.chapter_id is not None and call.scene_no is not None:
        sp = (
            await session.execute(
                select(ScenePacket.id)
                .where(
                    ScenePacket.chapter_id == call.chapter_id,
                    ScenePacket.scene_no == call.scene_no,
                )
                .order_by(ScenePacket.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if sp:
            links.scene_packet_id = sp
        scene_id = (
            await session.execute(
                select(Scene.id)
                .where(
                    Scene.chapter_id == call.chapter_id,
                    Scene.scene_no == call.scene_no,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if scene_id:
            links.scene_id = scene_id
        job_id = (
            await session.execute(
                select(Job.id)
                .where(
                    Job.chapter_id == call.chapter_id,
                    Job.scene_no == call.scene_no,
                    Job.kind == "draft",
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if job_id:
            links.job_id = job_id
    return links


async def _links_for_calls(session: SessionDep, calls: list[LlmCall]) -> dict[uuid.UUID, LlmCallLinksOut]:
    """Batched `_resolve_links`: three DISTINCT ON lookups over the calls' (chapter_id, scene_no)
    pairs instead of three queries per call. Same picks — latest packet, any scene, latest draft job."""
    pairs = sorted({(c.chapter_id, c.scene_no) for c in calls if c.chapter_id is not None and c.scene_no is not None})
    sp_by_pair: dict[tuple[uuid.UUID, int], uuid.UUID] = {}
    scene_by_pair: dict[tuple[uuid.UUID, int], uuid.UUID] = {}
    job_by_pair: dict[tuple[uuid.UUID, int], uuid.UUID] = {}
    if pairs:
        sp_by_pair = {
            (r[0], r[1]): r[2]
            for r in await session.execute(
                select(ScenePacket.chapter_id, ScenePacket.scene_no, ScenePacket.id)
                .where(tuple_(ScenePacket.chapter_id, ScenePacket.scene_no).in_(pairs))
                .distinct(ScenePacket.chapter_id, ScenePacket.scene_no)
                .order_by(ScenePacket.chapter_id, ScenePacket.scene_no, ScenePacket.created_at.desc())
            )
        }
        scene_by_pair = {
            (r[0], r[1]): r[2]
            for r in await session.execute(
                select(Scene.chapter_id, Scene.scene_no, Scene.id)
                .where(tuple_(Scene.chapter_id, Scene.scene_no).in_(pairs))
                .distinct(Scene.chapter_id, Scene.scene_no)
                .order_by(Scene.chapter_id, Scene.scene_no)
            )
        }
        job_by_pair = {
            (r[0], r[1]): r[2]
            for r in await session.execute(
                select(Job.chapter_id, Job.scene_no, Job.id)
                .where(tuple_(Job.chapter_id, Job.scene_no).in_(pairs), Job.kind == "draft")
                .distinct(Job.chapter_id, Job.scene_no)
                .order_by(Job.chapter_id, Job.scene_no, Job.created_at.desc())
            )
        }
    out: dict[uuid.UUID, LlmCallLinksOut] = {}
    for c in calls:
        links = LlmCallLinksOut(chapter_id=c.chapter_id, run_id=c.run_id)
        if c.chapter_id is not None and c.scene_no is not None:
            key = (c.chapter_id, c.scene_no)
            links.scene_packet_id = sp_by_pair.get(key)
            links.scene_id = scene_by_pair.get(key)
            links.job_id = job_by_pair.get(key)
        out[c.id] = links
    return out


def _call_out(call: LlmCall, links: LlmCallLinksOut | None = None) -> LlmCallOut:
    meta = call_metadata(call)
    return LlmCallOut(
        id=call.id,
        run_id=call.run_id,
        production_run_id=call.production_run_id,
        job_kind=meta.get("job_kind"),
        book_id=call.book_id,
        chapter_id=call.chapter_id,
        scene_no=call.scene_no,
        scene_seed_id=call.scene_seed_id,
        stage=call.stage,
        model=call.model,
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cache_creation_tokens=call.cache_creation_tokens,
        cache_read_tokens=call.cache_read_tokens,
        truncated=call.truncated,
        latency_ms=call.latency_ms,
        error=call.error,
        created_at=call.created_at,
        metadata=meta or None,
        estimated_cost_usd=round(estimate_calls_cost_usd([call]), 6),
        links=links or LlmCallLinksOut(chapter_id=call.chapter_id, run_id=call.run_id),
    )


def _settings_snapshot(calls: list[LlmCall]) -> dict[str, Any] | None:
    for c in sort_calls(calls):
        snap = call_metadata(c).get("settings_snapshot")
        if isinstance(snap, dict):
            return snap
    return None


def _apply_call_filters(
    stmt,
    *,
    book_id: uuid.UUID | None,
    chapter_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    scene_no: int | None,
    stage: str | None,
    stage_prefix: str | None,
    stages: str | None,
    model: str | None,
    truncated: bool | None,
    errors_only: bool | None,
    problems_only: bool | None,
    fallbacks_only: bool | None,
    min_latency_ms: int | None,
    min_input_tokens: int | None,
    cache_miss_only: bool | None,
):
    if book_id is not None:
        stmt = stmt.where(LlmCall.book_id == book_id)
    if chapter_id is not None:
        stmt = stmt.where(LlmCall.chapter_id == chapter_id)
    if run_id is not None:
        stmt = stmt.where(LlmCall.run_id == run_id)
    if scene_no is not None:
        stmt = stmt.where(LlmCall.scene_no == scene_no)
    if stage is not None:
        stmt = stmt.where(LlmCall.stage == stage)
    if stage_prefix:
        stmt = stmt.where(LlmCall.stage.startswith(stage_prefix))
    if stages:
        names = [s.strip() for s in stages.split(",") if s.strip()]
        if names:
            stmt = stmt.where(LlmCall.stage.in_(names))
    if model is not None:
        stmt = stmt.where(LlmCall.model == model)
    if truncated is True:
        stmt = stmt.where(LlmCall.truncated.is_(True))
    if errors_only:
        stmt = stmt.where(LlmCall.error.isnot(None))
    if problems_only:
        stmt = stmt.where(or_(LlmCall.truncated.is_(True), LlmCall.error.isnot(None)))
    if min_latency_ms is not None:
        stmt = stmt.where(LlmCall.latency_ms >= min_latency_ms)
    if min_input_tokens is not None:
        stmt = stmt.where(LlmCall.input_tokens >= min_input_tokens)
    if fallbacks_only:
        stmt = stmt.where(LlmCall.metadata_["fallback_attempt"].astext == "true")
    if cache_miss_only:
        stmt = stmt.where(LlmCall.cache_read_tokens == 0, LlmCall.cache_creation_tokens > 0)
    return stmt


@router.get("/chapters/{chapter_id}/telemetry", response_model=ChapterTelemetryOut)
async def chapter_telemetry(chapter_id: uuid.UUID, session: SessionDep) -> ChapterTelemetryOut:
    # Latest-run scoping in SQL: find the newest call's run_id, then fetch only that run's rows —
    # every older run stays in the database instead of being loaded and discarded.
    latest = (
        await session.execute(
            select(LlmCall.run_id).where(LlmCall.chapter_id == chapter_id).order_by(LlmCall.created_at.desc()).limit(1)
        )
    ).first()
    rows: list[LlmCall] = []
    if latest is not None:
        run_match = LlmCall.run_id == latest[0] if latest[0] is not None else LlmCall.run_id.is_(None)
        rows = list(
            (await session.execute(select(LlmCall).where(LlmCall.chapter_id == chapter_id, run_match))).scalars()
        )
    by_scene: dict[int | None, list[LlmCall]] = {}
    for c in rows:
        by_scene.setdefault(c.scene_no, []).append(c)
    scenes = [
        _scene_out(scene_no, calls)
        for scene_no, calls in sorted(by_scene.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]
    run_id = rows[0].run_id if rows else None
    return ChapterTelemetryOut(
        chapter_id=chapter_id, run_id=run_id, totals=TelemetryTotals(**_totals(rows)), scenes=scenes
    )


@router.get("/books/{book_id}/telemetry", response_model=BookTelemetryOut)
async def book_telemetry(
    book_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 5,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookTelemetryOut:
    chapters = {
        ch.id: ch for ch in (await session.execute(select(Chapter).where(Chapter.book_id == book_id))).scalars()
    }

    # All rollups aggregate in SQL — the append-only exhaust is never materialized row-by-row. The
    # (stage, model) grouping serves by_stage, by_model, and (rolled all the way up) the book totals.
    stage_model_rows = (
        await session.execute(
            select(LlmCall.stage, LlmCall.model, *_agg_cols())
            .where(LlmCall.book_id == book_id)
            .group_by(LlmCall.stage, LlmCall.model)
        )
    ).all()

    chapter_model_rows = (
        await session.execute(
            select(LlmCall.chapter_id, LlmCall.model, *_agg_cols())
            .where(LlmCall.book_id == book_id, LlmCall.chapter_id.isnot(None))
            .group_by(LlmCall.chapter_id, LlmCall.model)
        )
    ).all()
    by_chapter_rows: dict[uuid.UUID, list[Any]] = {}
    for r in chapter_model_rows:
        by_chapter_rows.setdefault(r.chapter_id, []).append(r)
    by_chapter = [
        ChapterRollupOut(
            chapter_id=cid,
            chapter_no=(ch.chapter_no if (ch := chapters.get(cid)) else None),
            title=(chapters[cid].title if cid in chapters else None),
            **totals_from_model_rows(agg_rows),
        )
        for cid, agg_rows in by_chapter_rows.items()
    ]
    by_chapter.sort(key=lambda r: (r.chapter_no is None, r.chapter_no))

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
    by_run: list[RunRollupOut] = []
    if page:
        page_ids = [r.run_id for r in page]
        run_match = LlmCall.run_id.in_([rid for rid in page_ids if rid is not None])
        if None in page_ids:  # legacy rows predate run_id
            run_match = or_(run_match, LlmCall.run_id.is_(None))
        run_model_rows = (
            await session.execute(
                select(LlmCall.run_id, LlmCall.chapter_id, LlmCall.model, *_agg_cols())
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
                RunRollupOut(
                    run_id=page_row.run_id,
                    started_at=page_row.started_at,
                    chapter_id=cid,
                    chapter_no=ch.chapter_no if ch else None,
                    title=ch.title if ch else None,
                    **totals_from_model_rows(agg_rows),
                )
            )

    # by_production_run: the already-captured draft+repair spend, grouped by the soft production_run_id
    # link. Same measures as by_run; status/chapter_no come from production_runs (cheap join in Python).
    prod_model_rows = (
        await session.execute(
            select(LlmCall.production_run_id, LlmCall.chapter_id, LlmCall.model, *_agg_cols())
            .where(LlmCall.book_id == book_id, LlmCall.production_run_id.isnot(None))
            .group_by(LlmCall.production_run_id, LlmCall.chapter_id, LlmCall.model)
        )
    ).all()
    by_production_run: list[ProductionRunRollupOut] = []
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
                ProductionRunRollupOut(
                    production_run_id=prid,
                    chapter_id=cid,
                    chapter_no=ch.chapter_no if ch else None,
                    status=pr.status if pr else None,
                    **totals_from_model_rows(agg_rows),
                )
            )
        by_production_run.sort(key=lambda r: (r.estimated_cost_usd, r.calls), reverse=True)

    # by_kind: draft vs revision (from metadata->>'job_kind'). One small per-bucket query grouped by
    # model only — the job_kind filter lives in WHERE, so nothing groups by a JSON expression (which
    # renders mismatched bound params in SELECT vs GROUP BY and trips Postgres). Empty buckets drop out.
    by_kind: list[TelemetryGroupOut] = []
    for label, kinds in _KIND_BUCKETS:
        kind_rows = (
            await session.execute(
                select(LlmCall.model, *_agg_cols())
                .where(LlmCall.book_id == book_id, LlmCall.metadata_["job_kind"].astext.in_(kinds))
                .group_by(LlmCall.model)
            )
        ).all()
        if kind_rows:
            by_kind.append(TelemetryGroupOut(key=label, **totals_from_model_rows(kind_rows)))
    by_kind.sort(key=lambda g: g.calls, reverse=True)

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
        EditorialAgentRunOut(
            production_run_id=r.production_run_id,
            agent_name=r.agent_name,
            agent_role=r.agent_role,
            stage=r.stage,
            status=r.status,
            duration_ms=r.duration_ms,
            started_at=r.started_at,
            cost_usd=0.0,
        )
        for r in editorial_rows
    ]

    return BookTelemetryOut(
        totals=TelemetryTotals(**totals_from_model_rows(stage_model_rows)),
        by_chapter=by_chapter,
        by_run=by_run,
        run_total=run_total,
        by_stage=_group_rows(stage_model_rows, "stage"),
        by_model=_group_rows(stage_model_rows, "model"),
        by_production_run=by_production_run,
        by_kind=by_kind,
        editorial_runs=editorial_runs,
    )


@router.delete("/books/{book_id}/telemetry", response_model=TelemetryDeleteOut)
async def clear_book_telemetry(book_id: uuid.UUID, session: SessionDep) -> TelemetryDeleteOut:
    """Delete all llm_calls rows for one book."""
    deleted = await delete_book_telemetry(session, book_id)
    await session.commit()
    return TelemetryDeleteOut(deleted_calls=deleted)


@router.delete("/books/{book_id}/telemetry/runs/{run_id}", response_model=TelemetryDeleteOut)
async def clear_run_telemetry(book_id: uuid.UUID, run_id: uuid.UUID, session: SessionDep) -> TelemetryDeleteOut:
    """Delete llm_calls for one run scoped to a book."""
    deleted = await delete_run_telemetry(session, book_id, run_id)
    await session.commit()
    return TelemetryDeleteOut(deleted_calls=deleted)


@router.delete("/telemetry", response_model=TelemetryDeleteOut)
async def clear_all_telemetry(body: GlobalTelemetryDeleteIn, session: SessionDep) -> TelemetryDeleteOut:
    """Delete every llm_calls row. Requires confirm phrase to prevent accidents."""
    if body.confirm != "DELETE_ALL_TELEMETRY":
        raise HTTPException(status_code=400, detail="confirm must be DELETE_ALL_TELEMETRY")
    deleted = await delete_all_telemetry(session)
    await session.commit()
    return TelemetryDeleteOut(deleted_calls=deleted)


@router.get("/runs/{run_id}/telemetry", response_model=RunTelemetryOut)
async def run_telemetry(run_id: uuid.UUID, session: SessionDep) -> RunTelemetryOut:
    rows = list((await session.execute(select(LlmCall).where(LlmCall.run_id == run_id))).scalars())
    if not rows:
        raise HTTPException(status_code=404, detail="Run not found")
    cid = next((c.chapter_id for c in rows if c.chapter_id is not None), None)
    ch = (await session.get(Chapter, cid)) if cid else None
    by_scene: dict[int | None, list[LlmCall]] = {}
    for c in rows:
        by_scene.setdefault(c.scene_no, []).append(c)
    scenes = [
        _scene_out(scene_no, calls)
        for scene_no, calls in sorted(by_scene.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]
    sorted_rows = sort_calls(rows)
    links_by_call = await _links_for_calls(session, sorted_rows)
    call_outs = [_call_out(c, links_by_call[c.id]) for c in sorted_rows]
    return RunTelemetryOut(
        run_id=run_id,
        started_at=min((c.created_at for c in rows), default=None),
        chapter_id=cid,
        chapter_no=ch.chapter_no if ch else None,
        title=ch.title if ch else None,
        totals=TelemetryTotals(**_totals(rows)),
        by_stage=_group(rows, lambda c: c.stage),
        by_model=_group(rows, lambda c: c.model),
        scenes=scenes,
        calls=call_outs,
        settings_snapshot=_settings_snapshot(rows),
    )


@router.get("/llm-calls", response_model=LlmCallListOut)
async def list_llm_calls(
    session: SessionDep,
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    scene_no: int | None = None,
    stage: str | None = None,
    stage_prefix: str | None = None,
    stages: str | None = None,
    model: str | None = None,
    truncated: bool | None = None,
    errors_only: bool | None = None,
    problems_only: bool | None = None,
    fallbacks_only: bool | None = None,
    min_latency_ms: int | None = None,
    min_input_tokens: int | None = None,
    cache_miss_only: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LlmCallListOut:
    base = select(LlmCall)
    filtered = _apply_call_filters(
        base,
        book_id=book_id,
        chapter_id=chapter_id,
        run_id=run_id,
        scene_no=scene_no,
        stage=stage,
        stage_prefix=stage_prefix,
        stages=stages,
        model=model,
        truncated=truncated,
        errors_only=errors_only,
        problems_only=problems_only,
        fallbacks_only=fallbacks_only,
        min_latency_ms=min_latency_ms,
        min_input_tokens=min_input_tokens,
        cache_miss_only=cache_miss_only,
    )
    count_stmt = select(func.count()).select_from(filtered.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    rows = list(
        (await session.execute(filtered.order_by(LlmCall.created_at.desc()).offset(offset).limit(limit))).scalars()
    )
    out: list[LlmCallOut] = []
    for c in rows:
        out.append(_call_out(c, await _resolve_links(session, c)))
    return LlmCallListOut(calls=out, total=total, limit=limit, offset=offset)


@router.get("/llm-calls/{call_id}", response_model=LlmCallOut)
async def llm_call_detail(call_id: uuid.UUID, session: SessionDep) -> LlmCallOut:
    call = await session.get(LlmCall, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return _call_out(call, await _resolve_links(session, call))


@router.get("/books/{book_id}/telemetry/problems", response_model=TelemetryProblemsOut)
async def book_telemetry_problems(book_id: uuid.UUID, session: SessionDep) -> TelemetryProblemsOut:
    # Fetch only candidate rows (the detectors' union prefilter) instead of the book's whole exhaust;
    # build_problems re-filters per detector, so the output is identical. Ordered for stable samples.
    rows = list(
        (
            await session.execute(
                select(LlmCall).where(LlmCall.book_id == book_id, problem_call_criteria()).order_by(LlmCall.created_at)
            )
        ).scalars()
    )
    failed_rows = (
        await session.execute(
            scope_jobs_to_book(
                select(Job.id, Job.chapter_no, Job.scene_no, Job.last_error).where(Job.status == JobStatus.FAILED),
                book_id,
            )
        )
    ).all()
    failed = [(row[0], row[1], row[2], row[3]) for row in failed_rows]
    raw = build_problems(rows, failed)
    draft = await detect_draft_not_ready(session, book_id)
    if draft:
        raw.append(draft)
    problems = [TelemetryProblemOut(**p) for p in raw]
    return TelemetryProblemsOut(problems=problems, healthy=len(problems) == 0)


@router.get("/books/{book_id}/telemetry/compare", response_model=RunCompareOut)
async def compare_runs(
    book_id: uuid.UUID,
    session: SessionDep,
    run_a: uuid.UUID,
    run_b: uuid.UUID,
) -> RunCompareOut:
    chapters = {
        ch.id: ch for ch in (await session.execute(select(Chapter).where(Chapter.book_id == book_id))).scalars()
    }

    async def _run_rows(rid: uuid.UUID) -> Sequence[Any]:
        # One grouped query per run serves both the rollup and the per-stage deltas.
        rows = (
            await session.execute(
                select(
                    LlmCall.stage,
                    LlmCall.chapter_id,
                    LlmCall.model,
                    *_agg_cols(),
                    func.min(LlmCall.created_at).label("started_at"),
                )
                .where(LlmCall.run_id == rid)
                .group_by(LlmCall.stage, LlmCall.chapter_id, LlmCall.model)
            )
        ).all()
        if not rows:
            raise HTTPException(status_code=404, detail=f"Run {rid} not found")
        return rows

    def _rollup(rid: uuid.UUID, rows: Sequence[Any]) -> RunRollupOut:
        cid = next((r.chapter_id for r in rows if r.chapter_id is not None), None)
        ch = chapters.get(cid) if cid else None
        return RunRollupOut(
            run_id=rid,
            started_at=min(r.started_at for r in rows),
            chapter_id=cid,
            chapter_no=ch.chapter_no if ch else None,
            title=ch.title if ch else None,
            **totals_from_model_rows(rows),
        )

    def _stage_sums(rows: Sequence[Any]) -> dict[str, dict[str, int]]:
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

    rows_a = await _run_rows(run_a)
    rows_b = await _run_rows(run_b)
    ra = _rollup(run_a, rows_a)
    rb = _rollup(run_b, rows_b)
    stages_a = _stage_sums(rows_a)
    stages_b = _stage_sums(rows_b)
    all_stages = sorted(set(stages_a) | set(stages_b))
    deltas = [
        StageDeltaOut(
            stage=s,
            calls_delta=stages_b.get(s, {}).get("calls", 0) - stages_a.get(s, {}).get("calls", 0),
            input_tokens_delta=(
                stages_b.get(s, {}).get("input_tokens", 0) - stages_a.get(s, {}).get("input_tokens", 0)
            ),
            output_tokens_delta=(
                stages_b.get(s, {}).get("output_tokens", 0) - stages_a.get(s, {}).get("output_tokens", 0)
            ),
            truncations_delta=stages_b.get(s, {}).get("truncations", 0) - stages_a.get(s, {}).get("truncations", 0),
        )
        for s in all_stages
    ]
    return RunCompareOut(run_a=ra, run_b=rb, stage_deltas=deltas)
