"""Chapter read surfaces + gate-1 beat approval (DESIGN §4, §8, §9).

Listing chapters and their beats/scenes powers the planning and History views. Approving a chapter's
beats is the gate-1 commit: it flips every beat to `approved`, marks the chapter `drafting`, and
enqueues one DRAFT job per scene under the chapter's latest run. Nothing here drafts prose — the
worker does that, one scene at a time.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import BeatStatus, ChapterStatus, JobKind, JobStatus, SceneStatus
from dominion.shared.models import Beat, Chapter, Job, Run, Scene
from dominion.shared.schemas import (
    ApproveBeatsIn,
    BeatCreateIn,
    BeatOut,
    ChapterOut,
    ChapterUpdateIn,
    HumanSceneIn,
    RedraftIn,
    SceneOut,
)
from dominion.workers.memory import summaries

log = structlog.get_logger()
router = APIRouter(prefix="/chapters", tags=["chapters"])


async def _fold_summary(scene_id: uuid.UUID) -> None:
    """Best-effort: fold a freshly-approved human section into the POV + omniscient summaries so later
    drafts inherit it. Runs as a background task (own session); a failure here never fails the write."""
    try:
        async with SessionFactory() as session:
            await summaries.refresh_on_approval(session, scene_id=scene_id)
    except Exception as exc:  # noqa: BLE001 — summary fold is advisory, not part of the request's contract
        log.warning("human_scene.summary_fold_failed", scene=str(scene_id), error=str(exc))


@router.get("", response_model=list[ChapterOut])
async def list_chapters(book_id: uuid.UUID, session: SessionDep) -> list[Chapter]:
    rows = (await session.execute(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)
    )).scalars().all()
    return list(rows)


@router.patch("/{chapter_id}", response_model=ChapterOut)
async def update_chapter(
    chapter_id: uuid.UUID, body: ChapterUpdateIn, session: SessionDep
) -> Chapter:
    """Edit a chapter's authored fields (currently the title). Only provided fields are applied, so
    the author can rename the plan-call's proposed title at any time without re-running the planner."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(chapter, key, value)
    await session.commit()
    return chapter


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


@router.post("/{chapter_id}/scenes", response_model=SceneOut)
async def create_human_scene(
    chapter_id: uuid.UUID, body: HumanSceneIn, session: SessionDep, background: BackgroundTasks,
) -> Scene:
    """Write a manuscript section by hand. It lands APPROVED (the human is the gate) as a `human`-sourced
    scene, supersedes any existing version at this scene_no, and folds into the POV summary in the
    background — so later drafts inherit it via the rolling summary + the in-chapter prior-scene tail."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    prose = body.prose.strip()
    if not prose:
        raise HTTPException(status_code=400, detail="prose is empty")

    prior = (await session.execute(
        select(Scene).where(Scene.chapter_id == chapter_id, Scene.scene_no == body.scene_no)
        .order_by(Scene.version.desc()).limit(1)
    )).scalar_one_or_none()
    scene = Scene(
        chapter_id=chapter_id, scene_no=body.scene_no,
        version=(prior.version + 1) if prior else 1,
        parent_scene_id=prior.id if prior else None,
        status=SceneStatus.APPROVED, prose=prose, prose_source="human",
    )
    if prior is not None:
        prior.status = SceneStatus.SUPERSEDED
    session.add(scene)
    await session.commit()
    await session.refresh(scene)
    background.add_task(_fold_summary, scene.id)
    return scene


@router.post("/{chapter_id}/scenes/redraft")
async def redraft_scenes(
    chapter_id: uuid.UUID, body: RedraftIn, session: SessionDep,
) -> dict[str, object]:
    """Re-draft existing scenes: queue a DRAFT job per selected scene that TARGETS that scene, so the
    worker version-ups and supersedes it (a clean regenerate, never a duplicate). Caller kicks the drain."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    scenes = (await session.execute(
        select(Scene).where(Scene.id.in_(body.scene_ids), Scene.chapter_id == chapter_id)
    )).scalars().all()
    if not scenes:
        raise HTTPException(status_code=400, detail="none of the given scene_ids belong to this chapter")

    run = (await session.execute(
        select(Run).where(Run.book_id == chapter.book_id).order_by(Run.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    job_ids: list[str] = []
    for scene in scenes:
        existing = (await session.execute(
            select(Job.id).join(Run, Job.run_id == Run.id).where(
                Run.book_id == chapter.book_id, Job.chapter_no == chapter.chapter_no,
                Job.scene_no == scene.scene_no, Job.status == JobStatus.QUEUED,
            )
        )).scalars().first()
        if existing is not None:
            job_ids.append(str(existing))
            continue
        job = Job(
            run_id=run.id if run else None, kind=JobKind.DRAFT,
            chapter_no=chapter.chapter_no, scene_no=scene.scene_no, target_scene_id=scene.id,
            token_budget=run.token_budget if run else settings.scene_token_budget,
            status=JobStatus.QUEUED,
        )
        session.add(job)
        await session.flush()
        job_ids.append(str(job.id))
    await session.commit()
    return {"chapter_id": str(chapter_id), "queued": len(job_ids), "jobs": job_ids}
