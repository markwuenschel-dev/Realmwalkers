"""LLM-call telemetry endpoints (persisted cost/cache/health, aggregated for the Desk).

`llm_calls` is append-only per-call exhaust written by the instrumented orchestrators. These read-only
endpoints aggregate it for the global Telemetry tab and expose drill-down detail (runs, calls,
problems) for the debugging console.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
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
from dominion.shared.models import Chapter, Job, LlmCall, Scene, ScenePacket
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
    book_telemetry_rollups,
    call_metadata,
    compare_rollup,
    compare_run_rows,
    compare_stage_delta_sums,
    group_calls,
    pipeline_steps,
    scene_stage_summary,
    scene_stages,
    scene_status,
    sort_calls,
)
from dominion.workers.telemetry_cost import estimate_calls_cost_usd
from dominion.workers.telemetry_diagnostics import build_problems, problem_call_criteria
from dominion.workers.telemetry_draft_problems import detect_draft_not_ready

log = structlog.get_logger()
router = APIRouter(tags=["telemetry"])


def _group(calls: list[LlmCall], key: Callable[[LlmCall], object]) -> list[TelemetryGroupOut]:
    return [TelemetryGroupOut(key=k, **t) for k, t in group_calls(calls, key)]


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
    # Worker aggregates every rollup in SQL and returns plain dicts/tuples; the router owns the schema
    # wrapping (and, elsewhere, the 404s). Behaviour-preserving — see book_telemetry_rollups.
    r = await book_telemetry_rollups(session, book_id, limit=limit, offset=offset)
    return BookTelemetryOut(
        totals=TelemetryTotals(**r.totals),
        by_chapter=[ChapterRollupOut(**d) for d in r.by_chapter],
        by_run=[RunRollupOut(**d) for d in r.by_run],
        run_total=r.run_total,
        by_stage=[TelemetryGroupOut(key=k, **t) for k, t in r.by_stage],
        by_model=[TelemetryGroupOut(key=k, **t) for k, t in r.by_model],
        by_production_run=[ProductionRunRollupOut(**d) for d in r.by_production_run],
        by_kind=[TelemetryGroupOut(key=k, **t) for k, t in r.by_kind],
        editorial_runs=[EditorialAgentRunOut(**d) for d in r.editorial_runs],
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

    # Worker runs one grouped query per run and returns rows/dicts; the router raises the 404s (a run
    # with no calls comes back as [], exactly as the inline version raised) and owns the schema wrap.
    rows_a = await compare_run_rows(session, run_a)
    if not rows_a:
        raise HTTPException(status_code=404, detail=f"Run {run_a} not found")
    rows_b = await compare_run_rows(session, run_b)
    if not rows_b:
        raise HTTPException(status_code=404, detail=f"Run {run_b} not found")
    ra = RunRollupOut(**compare_rollup(run_a, rows_a, chapters))
    rb = RunRollupOut(**compare_rollup(run_b, rows_b, chapters))
    stages_a = compare_stage_delta_sums(rows_a)
    stages_b = compare_stage_delta_sums(rows_b)
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
