"""Run + gate-1 beat proposal (DESIGN §4, §8).

Starting a run outlines one chapter and fires the single bounded plan-call, which PROPOSES per-scene
beats for your approval. Nothing is drafted here — the beats land as `proposed` for you to edit and
approve (gate 1) before any scene-draft job is enqueued.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import BeatStatus, ChapterStatus, RunStatus
from dominion.shared.models import Beat, Chapter, Run, Summary
from dominion.shared.schemas import BeatOut, RunStartIn, RunStartOut
from dominion.workers import planner
from dominion.workers.memory import canon_rag

router = APIRouter(prefix="/runs", tags=["runs"])


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

    # Upsert the chapter: this run owns its POV + outline; mark it as having beats proposed.
    chapter = (await session.execute(
        select(Chapter).where(Chapter.book_id == body.book_id, Chapter.chapter_no == body.chapter_no)
    )).scalar_one_or_none()
    if chapter is None:
        chapter = Chapter(book_id=body.book_id, chapter_no=body.chapter_no, pov=body.pov)
        session.add(chapter)
    chapter.pov = body.pov
    chapter.outline = body.outline
    chapter.status = ChapterStatus.BEATS_PROPOSED
    await session.flush()

    # Gate-1 plan-call: grounded in beat-scoped canon + the omniscient summary (DESIGN §7).
    omniscient = (await session.execute(
        select(Summary.rolling_summary).where(
            Summary.book_id == body.book_id, Summary.scope == "omniscient", Summary.pov.is_(None)
        )
    )).scalar_one_or_none()
    canon = await canon_rag.retrieve(session, book_id=body.book_id, query=body.outline, k=6)
    try:
        proposed = await planner.propose_beats(
            outline=body.outline, pov=body.pov, omniscient_summary=omniscient, canon=canon,
            max_beats=body.max_beats or 12,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    if proposed:
        # Replace rather than stack: clear the old proposed beats, then write the new ones. This
        # delete runs ONLY when the new proposal produced beats — an empty result (bad parse,
        # refusal) must never silently wipe the author's existing beats.
        await session.execute(
            delete(Beat).where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.PROPOSED)
        )
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
                target_words=body.target_words,
                status=BeatStatus.PROPOSED,
            )
            session.add(beat)
            beats.append(beat)
    else:
        # No beats came back — keep whatever proposed beats already exist and return them, so a
        # failed re-propose is a harmless no-op instead of a silent wipe. The UI flags the empty result.
        beats = list((await session.execute(
            select(Beat)
            .where(Beat.chapter_id == chapter.id, Beat.status == BeatStatus.PROPOSED)
            .order_by(Beat.scene_no)
        )).scalars().all())
    await session.commit()

    return RunStartOut(
        run_id=run.id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        pov=chapter.pov,
        beats=[BeatOut.model_validate(b) for b in beats],
    )
