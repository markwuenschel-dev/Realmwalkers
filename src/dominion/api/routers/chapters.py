"""Chapter read surfaces + gate-1 beat approval (DESIGN §4, §8, §9).

Listing chapters and their beats/scenes powers the planning and History views. Approving a chapter's
beats is the gate-1 commit: it flips every beat to `approved`, marks the chapter `drafting`, and
enqueues one DRAFT job per scene under the chapter's latest run. Nothing here drafts prose — the
worker does that, one scene at a time.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import BeatStatus, ChapterStatus, JobKind, JobStatus
from dominion.shared.models import Beat, Chapter, Job, Run, Scene
from dominion.shared.schemas import ApproveBeatsIn, BeatCreateIn, BeatOut, ChapterOut, SceneOut

router = APIRouter(prefix="/chapters", tags=["chapters"])


@router.get("", response_model=list[ChapterOut])
async def list_chapters(book_id: uuid.UUID, session: SessionDep) -> list[Chapter]:
    rows = (await session.execute(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)
    )).scalars().all()
    return list(rows)


@router.get("/{chapter_id}/beats", response_model=list[BeatOut])
async def list_beats(chapter_id: uuid.UUID, session: SessionDep) -> list[Beat]:
    rows = (await session.execute(
        select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)
    )).scalars().all()
    return list(rows)


@router.get("/{chapter_id}/scenes", response_model=list[SceneOut])
async def list_chapter_scenes(chapter_id: uuid.UUID, session: SessionDep) -> list[Scene]:
    """Every scene of a chapter, all statuses + versions (History browsing)."""
    rows = (await session.execute(
        select(Scene)
        .where(Scene.chapter_id == chapter_id)
        .order_by(Scene.scene_no, Scene.version)
    )).scalars().all()
    return list(rows)


@router.post("/{chapter_id}/beats", response_model=BeatOut)
async def create_beat(chapter_id: uuid.UUID, body: BeatCreateIn, session: SessionDep) -> Beat:
    """Add a beat by hand — a scene the planner didn't propose (gate 1)."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    beat = Beat(
        chapter_id=chapter_id, scene_no=body.scene_no, beat_text=body.beat_text,
        characters_present=body.characters_present, tags=body.tags,
        expected_state_changes=body.expected_state_changes,
        knowledge_injections=body.knowledge_injections, target_words=body.target_words,
        status=BeatStatus.PROPOSED,
    )
    session.add(beat)
    await session.commit()
    return beat


@router.post("/{chapter_id}/beats/approve")
async def approve_beats(
    chapter_id: uuid.UUID, session: SessionDep, body: ApproveBeatsIn | None = None
) -> dict[str, object]:
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")

    beats = (await session.execute(
        select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)
    )).scalars().all()
    if not beats:
        raise HTTPException(status_code=400, detail="no beats to approve for this chapter")

    # Optionally restrict to a chosen subset (the beats the author ticked to draft now).
    selected = set(body.beat_ids) if body and body.beat_ids else None
    to_approve = [b for b in beats if selected is None or b.id in selected]
    if not to_approve:
        raise HTTPException(status_code=400, detail="none of the given beat_ids belong to this chapter")

    run = (await session.execute(
        select(Run).where(Run.book_id == chapter.book_id).order_by(Run.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    job_ids: list[str] = []
    for beat in to_approve:
        beat.status = BeatStatus.APPROVED
        existing = (await session.execute(
            select(Job.id).join(Run, Job.run_id == Run.id).where(
                Run.book_id == chapter.book_id,
                Job.chapter_no == chapter.chapter_no,
                Job.scene_no == beat.scene_no,
                Job.status == JobStatus.QUEUED,
            )
        )).scalars().first()
        if existing is not None:
            job_ids.append(str(existing))
            continue
        job = Job(
            run_id=run.id if run else None,
            kind=JobKind.DRAFT,
            chapter_no=chapter.chapter_no,
            scene_no=beat.scene_no,
            token_budget=run.token_budget if run else settings.scene_token_budget,
            status=JobStatus.QUEUED,
        )
        session.add(job)
        await session.flush()
        job_ids.append(str(job.id))

    chapter.status = ChapterStatus.DRAFTING
    await session.commit()
    return {"chapter_id": str(chapter_id), "approved": len(to_approve), "jobs": job_ids}
