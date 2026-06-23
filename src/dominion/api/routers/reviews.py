"""Decision endpoints (DESIGN §9). The human's verdict is the only gate.

On approve: record the verdict, commit the beat's declared deltas to the ledger, fold the approved
text into the rolling summaries, and (in pause_each) auto-enqueue the next scene. On revise: queue a
revision job that re-drafts against the feedback. The continuity panel resolves a mismatch by either
correcting the ledger (prose was right) or queuing a targeted prose fix (ledger was right).
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import Decision, GateMode, JobKind, JobStatus, SceneStatus
from dominion.shared.models import (
    Approval,
    Beat,
    Chapter,
    CharacterState,
    Critique,
    EditPair,
    Job,
    Run,
    Scene,
)
from dominion.shared.schemas import ContinuityResolveIn, DecisionIn
from dominion.workers.memory import ledger, summaries
from dominion.workers.stat_render import render_stat_blocks

log = structlog.get_logger()
router = APIRouter(prefix="/scenes", tags=["reviews"])


async def _refresh_summaries_bg(scene_id: uuid.UUID) -> None:
    """Fold the approved scene into the rolling summaries OFF the request path — that's two
    review-model calls (several seconds), so Approve can respond instantly. Advisory: a failure just
    leaves summaries briefly stale; the next approval folds again."""
    from dominion.shared.db import SessionFactory

    async with SessionFactory() as session:
        try:
            await summaries.refresh_on_approval(session, scene_id=scene_id)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 — advisory; never surfaces to the user
            log.error("summary.refresh_failed", scene=str(scene_id), error=str(exc))


async def _capture_edit_pair(session: SessionDep, scene: Scene, human_text: str) -> None:
    """Snapshot the model's rendered draft next to the author's edit (LEARNING_FROM_EDITS Tier 1).

    `agent_text` is the RENDERED agent draft — we render the stored marker-form `agent_original` so the
    pair isn't noisy with ```stat``` markers (falling back to the current, pre-overwrite prose for older
    scenes with no `agent_original`). Upsert per `(scene, version)`: a re-edit refreshes only `human_text`,
    so we keep the true agent draft and never record a human→human diff. Advisory capture; never gates.
    """
    agent_text = render_stat_blocks(scene.agent_original) if scene.agent_original is not None else scene.prose
    pair = (await session.execute(
        select(EditPair).where(EditPair.scene_id == scene.id, EditPair.version == scene.version)
    )).scalar_one_or_none()
    if pair is None:
        pov = (await session.execute(
            select(Chapter.pov).where(Chapter.id == scene.chapter_id)
        )).scalar_one_or_none()
        session.add(EditPair(
            scene_id=scene.id, version=scene.version, pov=pov,
            agent_text=agent_text, human_text=human_text,
        ))
    else:
        pair.human_text = human_text  # keep the original agent draft; only the human side moved


@router.post("/{scene_id}/decision")
async def decide(
    scene_id: uuid.UUID, body: DecisionIn, session: SessionDep, background: BackgroundTasks
) -> dict[str, str | None]:
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    # Hand-edit in the inbox becomes the canonical text; all derivation reads this (DESIGN §9). Capture
    # the agent→human pair BEFORE overwriting prose — the pre-edit prose is the model's rendered draft.
    if body.edited_prose is not None:
        await _capture_edit_pair(session, scene, body.edited_prose)
        scene.prose = body.edited_prose
        scene.prose_source = "agent+human_edit"

    # An already-approved scene can be re-opened to edit or re-decide. Re-approval must NOT re-run the
    # one-shot side effects: relative ledger deltas ("+N") would double-count, and auto-advance could
    # re-enqueue an already-drafted next scene. Those fire only on the first pending -> approved cross.
    first_approval = scene.status != SceneStatus.APPROVED

    session.add(Approval(
        scene_id=scene.id, version=scene.version, decision=body.decision,
        target_pass=body.target_pass, feedback=body.feedback,
    ))

    next_job: uuid.UUID | None = None
    if body.decision == Decision.APPROVE:
        scene.status = SceneStatus.APPROVED
        if first_approval:
            await ledger.commit_declared_deltas(session, scene_id=scene.id)  # fast (DB) — keep inline
            next_job = await _auto_advance(session, scene)
        # Rolling-summary fold is two LLM calls — defer so the inbox responds instantly. A re-approval
        # re-folds the (edited) text, which is correct and idempotent.
        background.add_task(_refresh_summaries_bg, scene.id)
    elif body.decision == Decision.DENY:
        scene.status = SceneStatus.SUPERSEDED
    elif body.decision == Decision.REVISE:
        scene.status = SceneStatus.REVISION_REQUESTED
        next_job = await _enqueue_revision(session, scene, target_pass=body.target_pass)

    await session.commit()  # land the verdict before responding
    return {"scene": str(scene.id), "status": str(scene.status), "next_job": str(next_job) if next_job else None}


@router.post("/{scene_id}/continuity/resolve")
async def resolve_continuity(
    scene_id: uuid.UUID, body: ContinuityResolveIn, session: SessionDep
) -> dict[str, str | None]:
    critique = await session.get(Critique, body.critique_id)
    if critique is None or critique.scene_id != scene_id:
        raise HTTPException(status_code=404, detail="critique not found for this scene")
    scene = await session.get(Scene, scene_id)
    chapter = await session.get(Chapter, scene.chapter_id) if scene else None
    if scene is None or chapter is None:
        raise HTTPException(status_code=404, detail="scene not found")

    payload = critique.payload or {}
    character = str(payload.get("character", ""))
    attribute = str(payload.get("attribute", ""))
    prose_value = payload.get("prose_value")
    ledger_value = payload.get("ledger_value")

    if body.choice == "use_prose":
        # Prose is right -> correct the Oracle's ledger.
        row = (await session.execute(
            select(CharacterState).where(
                CharacterState.book_id == chapter.book_id, CharacterState.character == character
            )
        )).scalar_one_or_none()
        if row is None:
            row = CharacterState(book_id=chapter.book_id, character=character, stats_json={})
            session.add(row)
        stats = dict(row.stats_json or {})
        stats[attribute] = _coerce(prose_value)
        row.stats_json = stats
        await session.delete(critique)   # mismatch handled — clear it from the panel
        await session.commit()
        return {"resolved": "ledger_updated", "job": None}

    if body.choice == "use_ledger":
        # Ledger is right -> queue a targeted prose fix.
        feedback = (
            f"Continuity fix: {character}'s {attribute} must read {ledger_value!r}, "
            f"not {prose_value!r}. Correct the prose accordingly."
        )
        session.add(Approval(
            scene_id=scene.id, version=scene.version, decision=Decision.REVISE, feedback=feedback
        ))
        scene.status = SceneStatus.REVISION_REQUESTED
        job = await _enqueue_revision(session, scene, target_pass=None)
        await session.delete(critique)   # superseded by the queued revision — clear it
        await session.commit()
        return {"resolved": "revision_enqueued", "job": str(job) if job else None}

    if body.choice == "edit":
        return {"resolved": "edit_in_inbox", "job": None}

    raise HTTPException(status_code=422, detail="choice must be use_prose | use_ledger | edit")


# --- scheduling helpers ---------------------------------------------------------------------------

async def _latest_run(session: SessionDep, book_id: uuid.UUID) -> Run | None:
    return (await session.execute(
        select(Run).where(Run.book_id == book_id).order_by(Run.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _queued_job_id(
    session: SessionDep, *, book_id: uuid.UUID, chapter_no: int, scene_no: int
) -> uuid.UUID | None:
    return (await session.execute(
        select(Job.id).join(Run, Job.run_id == Run.id).where(
            Run.book_id == book_id,
            Job.chapter_no == chapter_no,
            Job.scene_no == scene_no,
            Job.status == JobStatus.QUEUED,
        )
    )).scalars().first()


async def _auto_advance(session: SessionDep, scene: Scene) -> uuid.UUID | None:
    """In pause_each, queue the next scene's draft if its beat exists and nothing is queued yet."""
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    if run is None or run.gate_mode != GateMode.PAUSE_EACH:
        return None
    next_no = scene.scene_no + 1
    beat = (await session.execute(
        select(Beat).where(Beat.chapter_id == scene.chapter_id, Beat.scene_no == next_no)
    )).scalar_one_or_none()
    if beat is None:
        return None  # no more authored beats — stop and let the human plan the next scene/chapter
    existing = await _queued_job_id(
        session, book_id=chapter.book_id, chapter_no=chapter.chapter_no, scene_no=next_no
    )
    if existing is not None:
        return existing
    job = Job(
        run_id=run.id, kind=JobKind.DRAFT, chapter_no=chapter.chapter_no, scene_no=next_no,
        token_budget=run.token_budget, status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.flush()
    return job.id


async def _enqueue_revision(
    session: SessionDep, scene: Scene, *, target_pass: str | None
) -> uuid.UUID | None:
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    run = await _latest_run(session, chapter.book_id)
    job = Job(
        run_id=run.id if run else None,
        kind=JobKind.REVISE_PASS if target_pass else JobKind.REVISE_FULL,
        target_scene_id=scene.id,
        target_pass=target_pass,
        chapter_no=chapter.chapter_no,
        scene_no=scene.scene_no,
        token_budget=run.token_budget if run else settings.scene_token_budget,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.flush()
    return job.id


def _coerce(value: object) -> object:
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value
