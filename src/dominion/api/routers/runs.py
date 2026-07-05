"""Run + gate-1 beat proposal (DESIGN §4, §8).

Starting a run outlines one chapter and fires the single bounded plan-call, which PROPOSES per-scene
beats for your approval. Nothing is drafted here — the beats land as `proposed` for you to edit and
approve (gate 1) before any scene-draft job is enqueued. POST /runs/batch proposes several chapters in
one request, sharing one Run, and can optionally auto-approve + queue drafts for each.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import BeatStatus, ChapterStatus, RunStatus
from dominion.shared.models import Beat, Chapter, Run, Summary
from dominion.shared.schemas import (
    BatchChapterResultOut,
    BatchRunOut,
    BatchRunStartIn,
    BeatOut,
    RunStartIn,
    RunStartOut,
)
from dominion.workers import activity, planner, telemetry, telemetry_db
from dominion.workers.memory import canon_rag

router = APIRouter(prefix="/runs", tags=["runs"])


async def _propose_chapter(
    session: SessionDep,
    *,
    run: Run,
    book_id: uuid.UUID,
    chapter_no: int,
    pov: str,
    outline: str,
    max_beats: int | None,
    target_words: int | None,
) -> tuple[Chapter, list[Beat]]:
    """Upsert the chapter, fire the gate-1 plan-call (beats + title, concurrently) and replace its
    PROPOSED beats with the new proposal; returns (chapter, beats). Telemetry for both planner calls
    is persisted under this run. Does NOT commit — the caller owns the transaction.
    """
    # Upsert the chapter: this run owns its POV + outline; mark it as having beats proposed.
    chapter = (
        await session.execute(select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_no == chapter_no))
    ).scalar_one_or_none()
    if chapter is None:
        chapter = Chapter(book_id=book_id, chapter_no=chapter_no, pov=pov)
        session.add(chapter)
    chapter.pov = pov
    chapter.outline = outline
    chapter.status = ChapterStatus.BEATS_PROPOSED
    await session.flush()

    # Gate-1 plan-call: grounded in beat-scoped canon + the omniscient summary (DESIGN §7).
    omniscient = (
        await session.execute(
            select(Summary.rolling_summary).where(
                Summary.book_id == book_id, Summary.scope == "omniscient", Summary.pov.is_(None)
            )
        )
    ).scalar_one_or_none()
    canon = await canon_rag.retrieve(session, book_id=book_id, query=outline, k=6)

    # Beats + a chapter title in one round-trip: title generation is best-effort and never raises, so
    # it adds ~no latency and can't fail the run; only the (bounded) beat proposal can time out -> 504.
    # Each planner call runs in its OWN coroutine wrapping its OWN telemetry call_context: the contextvar
    # stage must not bleed across the two gather tasks (each task copies the active context at creation).
    # The calls use asyncio.wait_for internally, so this is the same seam the scene-packet derive uses.
    sink = telemetry.TelemetrySink()

    async def _beats() -> list[dict[str, Any]]:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink,
                stage="beats",
                book_id=str(book_id),
                chapter_id=str(chapter.id),
            )
        ):
            return await planner.propose_beats(
                outline=outline,
                pov=pov,
                omniscient_summary=omniscient,
                canon=canon,
                max_beats=max_beats or 24,
            )

    async def _title() -> str | None:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink,
                stage="chapter_title",
                book_id=str(book_id),
                chapter_id=str(chapter.id),
            )
        ):
            return await planner.propose_chapter_title(
                outline=outline,
                pov=pov,
                omniscient_summary=omniscient,
            )

    try:
        proposed, title = await asyncio.gather(_beats(), _title())
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    # Flush the two calls' telemetry to llm_calls under this run (the caller commits). chapter.id is
    # already populated (flushed above) so each CallRecord is tagged with the right chapter.
    telemetry_db.persist_sink(session, sink, run_id=run.id, book_id=book_id, chapter_id=chapter.id)

    # Stamp a generated title only when the chapter has none yet — never clobber an author's rename
    # (which they make via PATCH /chapters/{id}); re-proposing beats leaves an existing title intact.
    if title and not (chapter.title or "").strip():
        chapter.title = title

    if proposed:
        # Replace rather than stack: clear the old proposed beats, then write the new ones. This
        # delete runs ONLY when the new proposal produced beats — an empty result (bad parse,
        # refusal) must never silently wipe the author's existing beats.
        await session.execute(delete(Beat).where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.PROPOSED))
        await session.flush()
        beats: list[Beat] = []
        for item in proposed:
            beat = Beat(
                chapter_id=chapter.id,
                scene_no=item["scene_no"],
                beat_text=item["beat_text"],
                characters_present=item["characters_present"],
                tags=item["tags"],
                expected_state_changes=item["expected_state_changes"],
                knowledge_injections=item["knowledge_injections"],
                target_words=target_words,
                status=BeatStatus.PROPOSED,
            )
            session.add(beat)
            beats.append(beat)
    else:
        # No beats came back — keep whatever proposed beats already exist and return them, so a
        # failed re-propose is a harmless no-op instead of a silent wipe. The UI flags the empty result.
        beats = list(
            (
                await session.execute(
                    select(Beat)
                    .where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.PROPOSED)
                    .order_by(Beat.scene_no)
                )
            )
            .scalars()
            .all()
        )

    return chapter, beats


@router.post("", response_model=RunStartOut)
async def start_run(body: RunStartIn, session: SessionDep) -> RunStartOut:
    token_budget = body.token_budget or settings.scene_token_budget
    run = Run(
        book_id=body.book_id,
        scope_json={"chapter": body.chapter_no},
        gate_mode=body.gate_mode,
        token_budget=token_budget,
        status=RunStatus.ACTIVE,
    )
    session.add(run)

    chapter, beats = await _propose_chapter(
        session,
        run=run,
        book_id=body.book_id,
        chapter_no=body.chapter_no,
        pov=body.pov,
        outline=body.outline,
        max_beats=body.max_beats,
        target_words=body.target_words,
    )
    await activity.safe_record_activity(
        session,
        kind="run_started",
        title=f"Ch {chapter.chapter_no} — planning run started",
        source="runs",
        severity="info",
        book_id=body.book_id,
        chapter_id=chapter.id,
        payload={"gate_mode": str(body.gate_mode), "chapter_no": body.chapter_no},
    )
    await session.commit()

    return RunStartOut(
        run_id=run.id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        pov=chapter.pov,
        beats=[BeatOut.model_validate(b) for b in beats],
    )


@router.post("/batch", response_model=BatchRunOut)
async def start_batch_run(body: BatchRunStartIn, session: SessionDep) -> BatchRunOut:
    """Propose beats for SEVERAL chapters in one request, sharing a single Run. `auto_draft` is disabled
    under contract-first drafting — derive and approve ScenePackets first."""
    if body.auto_draft:
        raise HTTPException(
            status_code=409,
            detail="auto_draft is disabled under contract-first drafting. Derive and approve ScenePackets first.",
        )
    token_budget = body.token_budget or settings.scene_token_budget
    run = Run(
        book_id=body.book_id,
        scope_json={"chapters": [spec.chapter_no for spec in body.chapters]},
        gate_mode=body.gate_mode,
        token_budget=token_budget,
        status=RunStatus.ACTIVE,
    )
    session.add(run)

    results: list[BatchChapterResultOut] = []
    # Plan each chapter sequentially: every spec is its own bounded plan-call, and the contextvar
    # telemetry seam in _propose_chapter wants one chapter's two tasks in flight at a time.
    for spec in body.chapters:
        chapter, beats = await _propose_chapter(
            session,
            run=run,
            book_id=body.book_id,
            chapter_no=spec.chapter_no,
            pov=spec.pov,
            outline=spec.outline,
            max_beats=spec.max_beats,
            target_words=spec.target_words,
        )
        queued_jobs = 0
        results.append(
            BatchChapterResultOut(
                chapter_id=chapter.id,
                chapter_no=chapter.chapter_no,
                pov=chapter.pov,
                beat_count=len(beats),
                queued_jobs=queued_jobs,
            )
        )

    await session.commit()
    return BatchRunOut(run_id=run.id, results=results)
