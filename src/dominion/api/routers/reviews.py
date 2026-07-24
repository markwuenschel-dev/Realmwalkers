"""Decision endpoints (DESIGN §9). The human's verdict is the only gate.

On approve: record the verdict, commit the beat's declared deltas to the ledger, fold the approved
text into the rolling summaries, and (in pause_each) auto-enqueue the next scene. On revise: queue a
revision job that re-drafts against the feedback. The continuity panel resolves a mismatch by either
correcting the ledger (prose was right) or queuing a targeted prose fix (ledger was right).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.adoption_entry import reconcile_adoption_demand_locked
from dominion.shared.chapter_lock import run_under_chapter_workflow
from dominion.shared.enums import Decision, ReconcileDemandOutcome, RevisionRequestOrigin, SceneStatus
from dominion.shared.models import (
    Approval,
    Chapter,
    CharacterState,
    Critique,
    EditPair,
    RevisionRequest,
    Scene,
)
from dominion.shared.schemas import ContinuityResolveIn, DecisionIn, RevisionRequestOut
from dominion.workers import activity
from dominion.workers.job_scheduler import schedule_next_after_approval
from dominion.workers.memory import knowledge, ledger, summaries
from dominion.workers.revision import (
    _cancel_active_requests_for_scene_locked,
    accept_revision_request,
    derive_display_phase,
    prose_hash,
    revision_request_out,
)
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


@dataclass(frozen=True)
class ApprovalResult:
    """What the chapter-locked scene-approval command committed (ADR-0032 W2). `reconcile` records the
    reverse-reconciliation outcome for the chapter's adoption (D9); `next_job`, `status`, and the response
    body are byte-identical to the pre-W2 APPROVE response."""

    scene_id: uuid.UUID
    status: str
    next_job: uuid.UUID | None
    first_approval: bool
    reconcile: ReconcileDemandOutcome


async def accept_scene_approval(
    session: AsyncSession,
    *,
    scene_id: uuid.UUID,
    edited_prose: str | None,
    target_pass: str | None,
    feedback: str | None,
) -> ApprovalResult:
    """The chapter-locked scene-APPROVAL command (ADR-0032 W2, D4/D9). ONE clean unit of work: acquire the
    per-chapter workflow lock FIRST (subsuming the old per-scene DECIDE-LOCK), reload the scene UNDER the
    lock, apply the approval + any inbox hand-edit, cancel the scene's active revision requests,
    reverse-reconcile the chapter's adoption demand, run the first-approval effects, record Activity, and
    commit — atomically, or not at all. Takes identifiers + immutable command data, NOT a preloaded mutable
    Scene (the reload happens under the lock). OWNS the commit boundary; callers must NOT commit around it.

    DENY/REVISE stay on the legacy decide path in W2; W3 moves the forward Revise path onto its own
    coordinator (`accept_revision_intent`). Reverse cancellation is DORMANT on current data (no request_bound
    adoption exists before W3's minter) — wired now so the defense lands before the first live minter (D13)."""
    # Locate the chapter only — make no decision from this read (chapter_lock protocol step 1).
    chapter_id = (await session.execute(select(Scene.chapter_id).where(Scene.id == scene_id))).scalar_one_or_none()
    if chapter_id is None:
        raise HTTPException(status_code=404, detail="scene not found")

    async def _body() -> ApprovalResult:
        scene = await session.get(Scene, scene_id)  # first materialization, UNDER the held lock
        if scene is None:  # raced delete between locate and lock
            raise HTTPException(status_code=404, detail="scene not found")

        # Re-approval must NOT re-run the one-shot side effects (relative ledger deltas would double-count;
        # auto-advance could re-enqueue). They fire only on the first pending -> approved cross.
        first_approval = scene.status != SceneStatus.APPROVED

        # Hand-edit becomes canonical; capture the agent→human pair BEFORE overwriting prose (DESIGN §9).
        if edited_prose is not None:
            await _capture_edit_pair(session, scene, edited_prose)
            scene.prose = edited_prose
            scene.prose_source = "agent+human_edit"

        session.add(
            Approval(
                scene_id=scene.id,
                version=scene.version,
                decision=Decision.APPROVE,
                target_pass=target_pass,
                feedback=feedback,
            )
        )
        scene.status = SceneStatus.APPROVED

        # Demand removal (D9): cancel the scene's active requests (revision-owned), then let the adoption
        # owner decide the consequence for the chapter's adoption — BOTH inside this one locked transaction,
        # so a failure in either rolls the whole approval back with them.
        await _cancel_active_requests_for_scene_locked(session, scene.id)
        reconcile = await reconcile_adoption_demand_locked(session, chapter_id)

        next_job: uuid.UUID | None = None
        if first_approval:
            await ledger.commit_declared_deltas(session, scene_id=scene.id)  # fast (DB) — keep inline
            try:  # record the scene's reveals into the knowledge ledger (advisory, never gates)
                await knowledge.record_scene_reveals(session, scene_id=scene.id)
            except Exception as exc:  # noqa: BLE001
                log.warning("knowledge.record_failed", scene=str(scene.id), error=str(exc))
            next_job = await schedule_next_after_approval(session, scene)

        chapter_row = (
            await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == scene.chapter_id))
        ).first()
        place = (
            f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
        ) + f"Scene {scene.scene_no}"
        await activity.safe_record_activity(
            session,
            kind="scene_decision",
            title=f"{place} approved",
            source="reviews",
            severity="success",
            book_id=chapter_row[0] if chapter_row else None,
            chapter_id=scene.chapter_id,
            job_id=next_job,
            payload={"decision": str(Decision.APPROVE)},
        )
        return ApprovalResult(
            scene_id=scene.id,
            status=str(scene.status),
            next_job=next_job,
            first_approval=first_approval,
            reconcile=reconcile,
        )

    return await run_under_chapter_workflow(session, chapter_id, _body)


@router.post("/{scene_id}/decision")
async def decide(
    scene_id: uuid.UUID, body: DecisionIn, session: SessionDep, background: BackgroundTasks, response: Response
) -> dict[str, str | None]:
    # APPROVE is a chapter-locked, atomic demand-REMOVAL command (ADR-0032 W2, D4/D9): it acquires the
    # per-chapter workflow lock FIRST (subsuming the old per-scene DECIDE-LOCK), so it must be dispatched
    # BEFORE this handler takes any row lock or mutates prose. DENY/REVISE stay on the legacy per-scene path
    # below until W3 moves the forward Revise path onto its own coordinator.
    if body.decision == Decision.APPROVE:
        result = await accept_scene_approval(
            session,
            scene_id=scene_id,
            edited_prose=body.edited_prose,
            target_pass=body.target_pass,
            feedback=body.feedback,
        )
        # Rolling-summary fold is two LLM calls — defer so the inbox responds instantly (post-commit; a
        # re-approval re-folds the edited text, which is correct and idempotent).
        background.add_task(_refresh_summaries_bg, scene_id)
        return {
            "scene": str(scene_id),
            "status": result.status,
            "next_job": str(result.next_job) if result.next_job else None,
        }

    # ---- DENY / REVISE: legacy per-scene path (unchanged in W2) ----
    # DECIDE-LOCK: serialize concurrent decisions on this scene (SELECT ... FOR UPDATE). APPROVE no longer
    # reaches here — it serializes on the broader per-chapter workflow lock inside accept_scene_approval.
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id).with_for_update())).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    # Hand-edit in the inbox becomes the canonical text; all derivation reads this (DESIGN §9). Capture
    # the agent→human pair BEFORE overwriting prose — the pre-edit prose is the model's rendered draft.
    if body.edited_prose is not None:
        await _capture_edit_pair(session, scene, body.edited_prose)
        scene.prose = body.edited_prose
        scene.prose_source = "agent+human_edit"

    # DENY records its verdict here; REVISE routes through accept_revision_request, which owns the Approval
    # + durable RevisionRequest atomically (and persists neither on a 4xx).
    if body.decision != Decision.REVISE:
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
    revise_request: RevisionRequest | None = None
    if body.decision == Decision.DENY:
        scene.status = SceneStatus.SUPERSEDED
    elif body.decision == Decision.REVISE:
        # The single revise-intent seam (ADR 0028): a durable request instead of a 409 rollback. An
        # imported/uncontracted scene lands at awaiting_contract (202); a contracted one mints the linked
        # revise job (202, queued); an exact replay returns the existing request (200). 4xx persists nothing.
        result = await accept_revision_request(
            session,
            scene=scene,
            feedback=body.feedback,
            target_pass=body.target_pass,
            expected_prose_hash=body.expected_prose_hash,
            origin=RevisionRequestOrigin.REVIEW,
        )
        scene.status = SceneStatus.REVISION_REQUESTED
        revise_request = result.request
        next_job = revise_request.job_id
        response.status_code = 200 if result.replayed else 202

    # Land this review action in the central Activity feed too (best-effort; never blocks the verdict).
    chapter_row = (
        await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == scene.chapter_id))
    ).first()
    verb = {Decision.DENY: "denied", Decision.REVISE: "sent for revision"}.get(body.decision, str(body.decision))
    place = (
        f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
    ) + f"Scene {scene.scene_no}"
    await activity.safe_record_activity(
        session,
        kind="scene_decision",
        title=f"{place} {verb}",
        source="reviews",
        severity="info",
        book_id=chapter_row[0] if chapter_row else None,
        chapter_id=scene.chapter_id,
        job_id=next_job,
        payload={"decision": str(body.decision)},
    )

    await session.commit()  # land the verdict before responding
    result_body: dict[str, str | None] = {
        "scene": str(scene.id),
        "status": str(scene.status),
        "next_job": str(next_job) if next_job else None,
    }
    if revise_request is not None:
        phase, _action = derive_display_phase(revise_request.status)
        result_body["revision_request"] = str(revise_request.id)
        result_body["revision_status"] = str(revise_request.status)
        result_body["display_phase"] = phase
    return result_body


@router.get("/{scene_id}/revision-request", response_model=RevisionRequestOut)
async def get_revision_request(scene_id: uuid.UUID, session: SessionDep) -> RevisionRequestOut:
    """The scene's current (most-recent) durable revision request, with its server-derived phase — the
    Desk banner reads this to show an imported / awaiting-contract scene its next action (ADR 0028)."""
    request = (
        await session.execute(
            select(RevisionRequest)
            .where(RevisionRequest.target_scene_id == scene_id)
            .order_by(RevisionRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="no revision request for this scene")
    return revision_request_out(request)


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
        # Ledger is right -> record durable revise intent through the single seam (ADR 0028). The
        # continuity panel acts on the CURRENT scene, so its expected hash IS the current prose hash
        # (there is no client round-trip that could mismatch). An uncontracted scene lands at
        # awaiting_contract rather than raising; the seam owns the Approval + RevisionRequest.
        feedback = (
            f"Continuity fix: {character}'s {attribute} must read {ledger_value!r}, "
            f"not {prose_value!r}. Correct the prose accordingly."
        )
        result = await accept_revision_request(
            session,
            scene=scene,
            feedback=feedback,
            target_pass=None,
            expected_prose_hash=prose_hash(scene.prose),
            origin=RevisionRequestOrigin.CONTINUITY,
        )
        scene.status = SceneStatus.REVISION_REQUESTED
        await session.delete(critique)  # superseded by the durable revision request — clear it
        await session.commit()
        job_id = result.request.job_id
        return {"resolved": "revision_enqueued", "job": str(job_id) if job_id else None}

    if body.choice == "edit":
        return {"resolved": "edit_in_inbox", "job": None}

    raise HTTPException(status_code=422, detail="choice must be use_prose | use_ledger | edit")


def _coerce(value: object) -> object:
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value
