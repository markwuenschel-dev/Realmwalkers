"""Chapter read surfaces + contract-first draft queueing.

Listing chapters and their beats/scenes powers the planning and History views. Draft jobs are
queued only via contract-first scheduling after approved ScenePackets exist.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.db import SessionFactory
from dominion.shared.enums import BeatStatus, ChapterStatus, ScenePacketStatus, SceneStatus
from dominion.shared.models import Beat, Chapter, Scene, ScenePacket, Summary
from dominion.shared.schemas import (
    ApproveBeatsIn,
    BeatCreateIn,
    BeatOut,
    ChapterCreateIn,
    ChapterOut,
    ChapterUpdateIn,
    DraftReadinessOut,
    DraftScheduleOut,
    HumanSceneIn,
    RedraftIn,
    SceneOut,
)
from dominion.workers import background_work, planner, telemetry, telemetry_db
from dominion.workers.draft_queue import DraftScheduleResult, schedule_contract_first_draft_jobs
from dominion.workers.draft_readiness import blocker_out, compute_draft_readiness
from dominion.workers.job_scheduler import (
    _latest_run,
    schedule_scene_redrafts,
    schedule_undrafted_beats,
)
from dominion.workers.memory import summaries
from dominion.workers.scene_packet import approval_policy as sp_approval

log = structlog.get_logger()
router = APIRouter(prefix="/chapters", tags=["chapters"])


def _schedule_out(chapter_id: uuid.UUID, result: DraftScheduleResult) -> DraftScheduleOut:
    return DraftScheduleOut(
        chapter_id=chapter_id,
        queued_job_ids=result.queued_job_ids,
        queued=len(result.queued_job_ids),
        skipped=[blocker_out(b) for b in result.skipped],
        repaired_beats=result.repaired_beats,
    )


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
    rows = (
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )
    return list(rows)


@router.post("", response_model=ChapterOut)
async def create_chapter(body: ChapterCreateIn, session: SessionDep) -> Chapter:
    """Create/update a chapter's POV + outline with NO LLM beat-proposal call — the contract-first
    entry point (create the chapter, then POST its /packet to author the chapter packet). Upserts
    by (book_id, chapter_no), same shape as the legacy gate-1 upsert in runs.py's _propose_chapter,
    minus the beat-authoring call. A best-effort title is still generated (same bounded, never-raising
    planner.propose_chapter_title call the old flow used), so chapters created this way aren't left
    untitled."""
    chapter = (
        await session.execute(
            select(Chapter).where(Chapter.book_id == body.book_id, Chapter.chapter_no == body.chapter_no)
        )
    ).scalar_one_or_none()
    if chapter is None:
        # New chapters are plain "chapter" kind (model default); an author marks a prologue/interlude/
        # epilogue afterward via PATCH /chapters/{id} (ChapterUpdateIn.kind), never clobbered on re-create.
        chapter = Chapter(book_id=body.book_id, chapter_no=body.chapter_no, pov=body.pov)
        session.add(chapter)
    chapter.pov = body.pov
    chapter.outline = body.outline
    chapter.status = ChapterStatus.PLANNED
    await session.flush()

    # Stamp a generated title only when the chapter has none yet — never clobber an author's rename
    # (made via PATCH /chapters/{id}); re-creating with a new outline leaves an existing title intact.
    if not (chapter.title or "").strip():
        omniscient = (
            await session.execute(
                select(Summary.rolling_summary).where(
                    Summary.book_id == body.book_id, Summary.scope == "omniscient", Summary.pov.is_(None)
                )
            )
        ).scalar_one_or_none()
        sink = telemetry.TelemetrySink()
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink, stage="chapter_title", book_id=str(body.book_id), chapter_id=str(chapter.id)
            )
        ):
            title = await planner.propose_chapter_title(
                outline=body.outline, pov=body.pov, omniscient_summary=omniscient
            )
        telemetry_db.persist_sink(session, sink, run_id=uuid.uuid4(), book_id=body.book_id, chapter_id=chapter.id)
        if title:
            chapter.title = title

    await session.commit()
    await session.refresh(chapter)
    return chapter


@router.patch("/{chapter_id}", response_model=ChapterOut)
async def update_chapter(chapter_id: uuid.UUID, body: ChapterUpdateIn, session: SessionDep) -> Chapter:
    """Edit a chapter's authored fields (title, structural kind, epigraph). Only provided fields are
    applied, so the author can rename the plan-call's proposed title, mark a prologue/interlude/epilogue,
    or add an epigraph at any time without re-running the planner."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    # mode="json" so a ChapterKind value serializes to its plain string before hitting the Text column.
    for key, value in body.model_dump(exclude_unset=True, mode="json").items():
        setattr(chapter, key, value)
    await session.commit()
    return chapter


@router.get("/{chapter_id}/beats", response_model=list[BeatOut])
async def list_beats(chapter_id: uuid.UUID, session: SessionDep) -> list[Beat]:
    rows = (
        (await session.execute(select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)))
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/{chapter_id}/scenes", response_model=list[SceneOut])
async def list_chapter_scenes(chapter_id: uuid.UUID, session: SessionDep) -> list[Scene]:
    """Every scene of a chapter, all statuses + versions (History browsing)."""
    rows = (
        (
            await session.execute(
                select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.scene_no, Scene.version)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/{chapter_id}/draft/readiness", response_model=DraftReadinessOut)
async def draft_readiness(chapter_id: uuid.UUID, session: SessionDep) -> DraftReadinessOut:
    """Read-only contract-first draft prerequisites for this chapter."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return await compute_draft_readiness(session, chapter_id)


@router.post("/{chapter_id}/beats", response_model=BeatOut)
async def create_beat(chapter_id: uuid.UUID, body: BeatCreateIn, session: SessionDep) -> Beat:
    """Add a beat by hand — a scene the planner didn't propose (gate 1)."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    beat = Beat(
        chapter_id=chapter_id,
        scene_no=body.scene_no,
        beat_text=body.beat_text,
        characters_present=body.characters_present,
        tags=body.tags,
        expected_state_changes=body.expected_state_changes,
        knowledge_injections=body.knowledge_injections,
        target_words=body.target_words,
        pov=body.pov,
        status=BeatStatus.PROPOSED,
    )
    session.add(beat)
    await session.commit()
    return beat


@router.post("/{chapter_id}/beats/approve")
async def approve_beats(
    chapter_id: uuid.UUID, session: SessionDep, body: ApproveBeatsIn | None = None
) -> dict[str, object]:
    """Approve beats only — does not queue draft jobs under contract-first drafting."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")

    beats = (
        (await session.execute(select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)))
        .scalars()
        .all()
    )
    if not beats:
        raise HTTPException(status_code=400, detail="no beats to approve for this chapter")

    selected = set(body.beat_ids) if body and body.beat_ids else None
    to_approve = [b for b in beats if selected is None or b.id in selected]
    if not to_approve:
        raise HTTPException(status_code=400, detail="none of the given beat_ids belong to this chapter")

    for beat in to_approve:
        beat.status = BeatStatus.APPROVED
    await session.commit()
    return {
        "chapter_id": str(chapter_id),
        "approved": len(to_approve),
        "message": "ScenePackets must be approved before drafting. Use Draft Chapter.",
    }


@router.post("/{chapter_id}/scenes", response_model=SceneOut)
async def create_human_scene(
    chapter_id: uuid.UUID,
    body: HumanSceneIn,
    session: SessionDep,
    background: BackgroundTasks,
) -> Scene:
    """Write a manuscript section by hand as a `human`-sourced scene that supersedes any existing
    version at this scene_no. By default it lands APPROVED (the human is the gate) and folds into the
    POV summary in the background — so later drafts inherit it via the rolling summary + the
    in-chapter prior-scene tail. When ``approve_directly=False`` it instead lands PENDING_REVIEW and
    enters the review inbox; the summary fold is deferred to approval (the /decision APPROVE branch),
    so an unaccepted draft never leaks into later scenes' context."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    prose = body.prose.strip()
    if not prose:
        raise HTTPException(status_code=400, detail="prose is empty")

    prior = (
        await session.execute(
            select(Scene)
            .where(Scene.chapter_id == chapter_id, Scene.scene_no == body.scene_no)
            .order_by(Scene.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    scene = Scene(
        chapter_id=chapter_id,
        scene_no=body.scene_no,
        version=(prior.version + 1) if prior else 1,
        parent_scene_id=prior.id if prior else None,
        status=SceneStatus.APPROVED if body.approve_directly else SceneStatus.PENDING_REVIEW,
        prose=prose,
        prose_source="human",
    )
    if prior is not None:
        prior.status = SceneStatus.SUPERSEDED
    session.add(scene)
    await session.commit()
    await session.refresh(scene)
    # Fold only when approved directly; a review-first scene folds when the human approves it in the
    # inbox (the /decision APPROVE branch schedules the fold), so unaccepted text can't leak forward.
    if body.approve_directly:
        background.add_task(_fold_summary, scene.id)
    return scene


@router.post("/{chapter_id}/scenes/redraft", response_model=DraftScheduleOut)
async def redraft_scenes(
    chapter_id: uuid.UUID,
    body: RedraftIn,
    session: SessionDep,
) -> DraftScheduleOut:
    """Re-draft existing scenes via contract-first scheduling."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    scenes = (
        (await session.execute(select(Scene).where(Scene.id.in_(body.scene_ids), Scene.chapter_id == chapter_id)))
        .scalars()
        .all()
    )
    if not scenes:
        raise HTTPException(status_code=400, detail="none of the given scene_ids belong to this chapter")

    run = await _latest_run(session, chapter.book_id)
    result = await schedule_scene_redrafts(session, chapter, list(scenes), run)
    out = _schedule_out(chapter_id, result)
    if not out.queued_job_ids and out.skipped:
        # Same UUID-serialization hazard as draft_chapter below: model_dump(mode="json") keeps the
        # blocker detail JSON-native (raw UUIDs would 500 via Starlette's json.dumps, not this 409).
        raise HTTPException(status_code=409, detail={"blockers": [s.model_dump(mode="json") for s in out.skipped]})
    await session.commit()
    return out


@router.post("/{chapter_id}/scenes/{scene_no}/redraft", response_model=DraftScheduleOut)
async def redraft_scene(
    chapter_id: uuid.UUID,
    scene_no: int,
    session: SessionDep,
    background: BackgroundTasks,
) -> DraftScheduleOut:
    """One-click re-draft of a single deleted/undrafted scene, scoped to ONE scene_no.

    Deleting a scene keeps its beat but marks the slot's ScenePacket STALE ("scene deleted"), and
    contract-first drafting is fail-closed on an approved, non-stale packet — so the beat is left
    "undrafted" yet unqueueable. This re-approves that STALE packet (flip STALE → APPROVED, clear
    stale_reason), then queues a draft job for just this scene's approved beat and kicks the drain.
    Never touches the rest of the chapter, and never force-approves a BLOCKED/RATE_LIMITED packet."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")

    # The slot's scene packet(s). The delete flow normally leaves exactly one, marked STALE.
    packets = (
        (
            await session.execute(
                select(ScenePacket)
                .where(ScenePacket.chapter_id == chapter_id, ScenePacket.scene_no == scene_no)
                .order_by(ScenePacket.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not packets:
        raise HTTPException(
            status_code=409,
            detail=f"no scene packet for scene {scene_no} — derive and approve scene packets first",
        )

    # This action is for a MISSING scene. If the slot already has drafted prose the scene wasn't
    # deleted — the user wants the supersede-in-place path (POST /chapters/{id}/scenes/redraft).
    existing = (
        await session.execute(
            select(Scene)
            .where(Scene.chapter_id == chapter_id, Scene.scene_no == scene_no)
            .order_by(Scene.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None and (existing.prose or "").strip():
        raise HTTPException(
            status_code=409,
            detail=(
                f"scene {scene_no} already has prose (v{existing.version}) — use redraft to supersede "
                "it, not re-draft this scene"
            ),
        )

    # Re-approve the slot's contract so contract-first scheduling can resolve an approved packet. An
    # already-approved (non-stale) packet needs nothing; otherwise re-approve a STALE one (STALE is
    # re-approvable — assert_draft_ready's own remedy is "re-derive or re-approve"). can_approve() is
    # the real approval gate: it refuses BLOCKED/RATE_LIMITED, so we never force-approve one of those.
    target = next((p for p in packets if p.status == ScenePacketStatus.APPROVED and not p.stale_reason), None)
    if target is None:
        for p in packets:
            if p.status == ScenePacketStatus.STALE and sp_approval.can_approve(p) is None:
                p.status = ScenePacketStatus.APPROVED
                p.stale_reason = None
                target = p
                break
    if target is None:
        # Everything at this slot is blocked/rate-limited/proposed — surface the clearest refusal.
        refusal = next((r for p in packets if (r := sp_approval.can_approve(p)) is not None), None)
        raise HTTPException(
            status_code=409,
            detail=(
                refusal.detail
                if refusal
                else f"scene packet for scene {scene_no} is not approved — approve or re-derive it first"
            ),
        )

    # Queue a draft for JUST this scene's approved, undrafted beat — a scoped schedule_undrafted_beats.
    beat = (
        await session.execute(
            select(Beat).where(
                Beat.chapter_id == chapter_id,
                Beat.scene_no == scene_no,
                Beat.status == BeatStatus.APPROVED,
            )
        )
    ).scalar_one_or_none()
    if beat is None:
        raise HTTPException(
            status_code=409,
            detail=f"no approved beat for scene {scene_no} — approve scene packets to derive beats first",
        )

    run = await _latest_run(session, chapter.book_id)
    result = await schedule_contract_first_draft_jobs(
        session, chapter=chapter, beats=[beat], run=run, skip_drafted=True
    )
    out = _schedule_out(chapter_id, result)
    if not out.queued_job_ids:
        if out.skipped:
            # Same UUID-serialization hazard as draft_chapter: mode="json" keeps the blocker detail
            # JSON-native so HTTPException renders a 409 instead of a 500 on a raw UUID.
            raise HTTPException(status_code=409, detail={"blockers": [s.model_dump(mode="json") for s in out.skipped]})
        raise HTTPException(status_code=409, detail=f"nothing to draft for scene {scene_no}")
    await session.commit()
    # One-click: kick the drain so the queued job starts without a separate Draft-next call. The drain
    # single-flights (process-global lock) and honors the pause switch, so an unconditional kick is safe.
    background.add_task(background_work.drain_queued_jobs)
    return out


@router.post("/{chapter_id}/draft", response_model=DraftScheduleOut)
async def draft_chapter(chapter_id: uuid.UUID, session: SessionDep) -> DraftScheduleOut:
    """Queue draft jobs for approved beats with validated ScenePackets — canonical contract-first entry."""
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    beats = (
        (
            await session.execute(
                select(Beat)
                .where(Beat.chapter_id == chapter_id, Beat.status == BeatStatus.APPROVED)
                .order_by(Beat.scene_no)
            )
        )
        .scalars()
        .all()
    )
    if not beats:
        raise HTTPException(status_code=400, detail="no approved beats — approve ScenePackets first")

    run = await _latest_run(session, chapter.book_id)
    result = await schedule_undrafted_beats(session, chapter, run)
    out = _schedule_out(chapter_id, result)
    if not out.queued_job_ids and out.skipped:
        # mode="json" is load-bearing: plain model_dump() leaves chapter_id/beat_id/scene_packet_id as
        # raw UUID objects, and HTTPException.detail skips FastAPI's jsonable_encoder — Starlette's
        # JSONResponse hits stdlib json.dumps() directly, which can't serialize UUID and raises
        # TypeError while rendering the response (an unhandled 500 instead of this 409).
        raise HTTPException(status_code=409, detail={"blockers": [s.model_dump(mode="json") for s in out.skipped]})
    if out.queued_job_ids:
        chapter.status = ChapterStatus.DRAFTING
    await session.commit()
    return out
