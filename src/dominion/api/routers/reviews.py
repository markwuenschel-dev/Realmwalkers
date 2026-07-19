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
from sqlalchemy import func, select

from dominion.api.deps import SessionDep
from dominion.shared.enums import Decision, SceneStatus
from dominion.shared.models import (
    Approval,
    Chapter,
    CharacterState,
    Critique,
    EditPair,
    Scene,
)
from dominion.shared.schemas import ContinuityResolveIn, DecisionIn
from dominion.workers import activity
from dominion.workers.draft_queue import DraftQueueBlocker
from dominion.workers.draft_readiness import blocker_out
from dominion.workers.job_scheduler import schedule_next_after_approval, schedule_revision
from dominion.workers.memory import knowledge, ledger, summaries
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
    pair = (
        await session.execute(select(EditPair).where(EditPair.scene_id == scene.id, EditPair.version == scene.version))
    ).scalar_one_or_none()
    if pair is None:
        pov = (await session.execute(select(Chapter.pov).where(Chapter.id == scene.chapter_id))).scalar_one_or_none()
        session.add(
            EditPair(
                scene_id=scene.id,
                version=scene.version,
                pov=pov,
                agent_text=agent_text,
                human_text=human_text,
            )
        )
    else:
        pair.human_text = human_text  # keep the original agent draft; only the human side moved


@router.post("/{scene_id}/decision")
async def decide(
    scene_id: uuid.UUID, body: DecisionIn, session: SessionDep, background: BackgroundTasks
) -> dict[str, str | None]:
    # DECIDE-LOCK: serialize concurrent decisions on this scene (SELECT ... FOR UPDATE), matching the
    # hardening apply_repair_task uses for the same cross-request race. Without it, two concurrent APPROVEs
    # both read status != APPROVED, both enter the first_approval block, and race the one-shot side effects
    # (the LEDGER beat-marker check-then-set, the CharacterState insert, the next-draft enqueue). The lock
    # makes the second decision see the first's committed APPROVED status, so first_approval fires once.
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id).with_for_update())).scalar_one_or_none()
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

    session.add(
        Approval(
            scene_id=scene.id,
            version=scene.version,
            decision=body.decision,
            target_pass=body.target_pass,
            feedback=body.feedback,
        )
    )

    next_job: uuid.UUID | None = None
    if body.decision == Decision.APPROVE:
        scene.status = SceneStatus.APPROVED
        if first_approval:
            await ledger.commit_declared_deltas(session, scene_id=scene.id)  # fast (DB) — keep inline
            try:  # record the scene's reveals into the knowledge ledger (advisory, never gates)
                await knowledge.record_scene_reveals(session, scene_id=scene.id)
            except Exception as exc:  # noqa: BLE001
                log.warning("knowledge.record_failed", scene=str(scene.id), error=str(exc))
            next_job = await schedule_next_after_approval(session, scene)
        # Rolling-summary fold is two LLM calls — defer so the inbox responds instantly. A re-approval
        # re-folds the (edited) text, which is correct and idempotent.
        background.add_task(_refresh_summaries_bg, scene.id)
    elif body.decision == Decision.DENY:
        scene.status = SceneStatus.SUPERSEDED
    elif body.decision == Decision.REVISE:
        scene.status = SceneStatus.REVISION_REQUESTED
        revision = await schedule_revision(session, scene, target_pass=body.target_pass)
        if isinstance(revision, DraftQueueBlocker):
            # No resolvable contract (e.g. an imported scene): don't queue a job the resolver will
            # reject at drain time. Raise an actionable 409 the Desk renders via draftBlockerMessage;
            # the raise rolls back the pending Approval + status change, so the scene stays reviewable.
            raise HTTPException(status_code=409, detail={"blockers": [blocker_out(revision).model_dump(mode="json")]})
        next_job = revision

    # Land this review action in the central Activity feed too, so the drawer is the single pane for
    # "what happened" across pages (best-effort; never blocks the verdict).
    chapter_row = (
        await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == scene.chapter_id))
    ).first()
    verb = {Decision.APPROVE: "approved", Decision.DENY: "denied", Decision.REVISE: "sent for revision"}.get(
        body.decision, str(body.decision)
    )
    place = (
        f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
    ) + f"Scene {scene.scene_no}"
    await activity.safe_record_activity(
        session,
        kind="scene_decision",
        title=f"{place} {verb}",
        source="reviews",
        severity="success" if body.decision == Decision.APPROVE else "info",
        book_id=chapter_row[0] if chapter_row else None,
        chapter_id=scene.chapter_id,
        job_id=next_job,
        payload={"decision": str(body.decision)},
    )

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
        row = (
            await session.execute(
                select(CharacterState).where(
                    CharacterState.book_id == chapter.book_id,
                    func.lower(CharacterState.character) == character.lower(),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CharacterState(book_id=chapter.book_id, character=character, stats_json={})
            session.add(row)
        stats = dict(row.stats_json or {})
        stats[attribute] = _coerce(prose_value)
        row.stats_json = stats
        await session.delete(critique)  # mismatch handled — clear it from the panel
        await session.commit()
        return {"resolved": "ledger_updated", "job": None}

    if body.choice == "use_ledger":
        # Ledger is right -> queue a targeted prose fix.
        feedback = (
            f"Continuity fix: {character}'s {attribute} must read {ledger_value!r}, "
            f"not {prose_value!r}. Correct the prose accordingly."
        )
        session.add(Approval(scene_id=scene.id, version=scene.version, decision=Decision.REVISE, feedback=feedback))
        scene.status = SceneStatus.REVISION_REQUESTED
        job = await schedule_revision(session, scene, target_pass=None)
        if isinstance(job, DraftQueueBlocker):
            raise HTTPException(status_code=409, detail={"blockers": [blocker_out(job).model_dump(mode="json")]})
        await session.delete(critique)  # superseded by the queued revision — clear it
        await session.commit()
        return {"resolved": "revision_enqueued", "job": str(job) if job else None}

    if body.choice == "edit":
        return {"resolved": "edit_in_inbox", "job": None}

    raise HTTPException(status_code=422, detail="choice must be use_prose | use_ledger | edit")


def _coerce(value: object) -> object:
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value
