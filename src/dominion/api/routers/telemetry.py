"""LLM-call telemetry endpoints (persisted cost/cache/health, aggregated for the Desk).

`llm_calls` is append-only per-call exhaust written by the instrumented orchestrators (currently the
scene-packet derive). These read-only endpoints aggregate it two ways:

  * per chapter — chapter totals + a per-scene breakdown, for the panel under the scene packets, so a
    derive's cache efficiency and any truncations are visible right where the blocks show up;
  * per book — overall totals plus rollups across chapters, stages, and models, for the global
    Telemetry tab's cross-chapter/scene comparison.

No write paths: telemetry is produced by the workers, never by the Desk.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import APIRouter
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Chapter, LlmCall
from dominion.shared.schemas import (
    BookTelemetryOut,
    ChapterRollupOut,
    ChapterTelemetryOut,
    SceneTelemetryOut,
    TelemetryGroupOut,
    TelemetryTotals,
)

router = APIRouter(tags=["telemetry"])


def _totals(calls: Iterable[LlmCall]) -> dict:
    """Roll a set of calls into the shared totals fields (raw sums + derived cache/latency)."""
    calls = list(calls)
    input_t = sum(c.input_tokens for c in calls)
    cc = sum(c.cache_creation_tokens for c in calls)
    cr = sum(c.cache_read_tokens for c in calls)
    prompt = input_t + cc + cr
    latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
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
        avg_latency_ms=int(sum(latencies) / len(latencies)) if latencies else None,
    )


def _group(calls: list[LlmCall], key) -> list:
    """Group calls by `key(call)` (skipping None keys), each bucket rolled into TelemetryGroupOut,
    sorted by call volume descending."""
    buckets: dict[str, list[LlmCall]] = {}
    for c in calls:
        k = key(c)
        if k:
            buckets.setdefault(str(k), []).append(c)
    groups = [TelemetryGroupOut(key=k, **_totals(v)) for k, v in buckets.items()]
    return sorted(groups, key=lambda g: g.calls, reverse=True)


@router.get("/chapters/{chapter_id}/telemetry", response_model=ChapterTelemetryOut)
async def chapter_telemetry(chapter_id: uuid.UUID, session: SessionDep) -> ChapterTelemetryOut:
    """Per-scene derive telemetry for one chapter, plus chapter totals. Empty (zero totals) when the
    chapter has never been derived."""
    rows = list((await session.execute(
        select(LlmCall).where(LlmCall.chapter_id == chapter_id)
    )).scalars())

    by_scene: dict[int | None, list[LlmCall]] = {}
    for c in rows:
        by_scene.setdefault(c.scene_no, []).append(c)
    scenes = [
        SceneTelemetryOut(
            scene_no=scene_no,
            models=sorted({c.model for c in calls}),
            **_totals(calls),
        )
        for scene_no, calls in sorted(by_scene.items(), key=lambda kv: (kv[0] is None, kv[0]))
    ]
    return ChapterTelemetryOut(
        chapter_id=chapter_id, totals=TelemetryTotals(**_totals(rows)), scenes=scenes
    )


@router.get("/books/{book_id}/telemetry", response_model=BookTelemetryOut)
async def book_telemetry(book_id: uuid.UUID, session: SessionDep) -> BookTelemetryOut:
    """Global telemetry for a book: overall totals + rollups across chapters, stages, and models for
    cross-chapter/scene comparison."""
    rows = list((await session.execute(
        select(LlmCall).where(LlmCall.book_id == book_id)
    )).scalars())

    # Chapter rollup carries chapter_no/title so the tab can label and order chapters meaningfully.
    chapters = {
        ch.id: ch for ch in (await session.execute(
            select(Chapter).where(Chapter.book_id == book_id)
        )).scalars()
    }
    by_chapter_calls: dict[uuid.UUID, list[LlmCall]] = {}
    for c in rows:
        if c.chapter_id is not None:
            by_chapter_calls.setdefault(c.chapter_id, []).append(c)
    by_chapter = [
        ChapterRollupOut(
            chapter_id=cid,
            chapter_no=(ch.chapter_no if (ch := chapters.get(cid)) else None),
            title=(chapters[cid].title if cid in chapters else None),
            **_totals(calls),
        )
        for cid, calls in by_chapter_calls.items()
    ]
    by_chapter.sort(key=lambda r: (r.chapter_no is None, r.chapter_no))

    return BookTelemetryOut(
        totals=TelemetryTotals(**_totals(rows)),
        by_chapter=by_chapter,
        by_stage=_group(rows, lambda c: c.stage),
        by_model=_group(rows, lambda c: c.model),
    )
