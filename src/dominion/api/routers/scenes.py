"""Read endpoints for the review inbox (DESIGN §9), plus the voice-exemplar toggle. All real."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.api.scene_delete import hard_delete_scene
from dominion.shared.enums import ArtifactType, Decision, SceneStatus
from dominion.shared.models import Approval, Artifact, Chapter, Critique, DraftAttempt, PovProfile, Scene, ScenePacket
from dominion.shared.schemas import (
    ClauseEvaluationOut,
    CritiqueOut,
    DeleteSceneOut,
    DraftAttemptOut,
    ExemplarIn,
    SceneDetail,
    SceneFidelityOut,
    SceneOut,
    SceneVersionOut,
)
from dominion.workers.scene_fidelity import fidelity_contract_fingerprint, is_fidelity_active
from dominion.workers.scene_fidelity.models import SceneFidelityReport
from dominion.workers.scene_fidelity.policy import policy_outcome_for_clause_evaluation, report_is_current

_FIDELITY_REPORT_TYPE = ArtifactType.SCENE_FIDELITY_REPORT.value

router = APIRouter(prefix="/scenes", tags=["scenes"])


async def _pov_profile_for_scene(
    session: SessionDep, scene: Scene, *, create_if_missing: bool = False
) -> PovProfile | None:
    """The PovProfile that owns this scene's voice — keyed by the scene's (book, chapter POV)."""
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    profile = (
        await session.execute(
            select(PovProfile).where(PovProfile.book_id == chapter.book_id, PovProfile.character == chapter.pov)
        )
    ).scalar_one_or_none()
    if profile is None and create_if_missing:
        profile = PovProfile(book_id=chapter.book_id, character=chapter.pov)
        session.add(profile)
        await session.flush()
    return profile


@router.get("/pending", response_model=list[SceneOut])
async def pending(session: SessionDep) -> list[Scene]:
    rows = (
        (
            await session.execute(
                select(Scene)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(Scene.status == SceneStatus.PENDING_REVIEW)
                .order_by(Chapter.position, Scene.scene_no, Scene.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/{scene_id}", response_model=SceneDetail)
async def scene_detail(scene_id: uuid.UUID, session: SessionDep) -> SceneDetail:
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    crits = (await session.execute(select(Critique).where(Critique.scene_id == scene_id))).scalars().all()
    detail = SceneDetail.model_validate(scene)
    detail.critiques = [CritiqueOut.model_validate(c) for c in crits]
    profile = await _pov_profile_for_scene(session, scene)
    detail.is_exemplar = bool(profile and str(scene.id) in (profile.exemplar_scene_ids or []))
    return detail


@router.post("/{scene_id}/exemplar")
async def set_exemplar(scene_id: uuid.UUID, body: ExemplarIn, session: SessionDep) -> dict[str, str | bool]:
    """Mark/unmark this scene as a voice exemplar for its POV (LEARNING_FROM_EDITS Tier 2).

    Adds/removes the scene id on the POV's `PovProfile.exemplar_scene_ids` — the list the drafter
    few-shots on. Idempotent; disabling on a scene with no profile is a no-op.
    """
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    profile = await _pov_profile_for_scene(session, scene, create_if_missing=body.enabled)
    if profile is None:  # disabling with no profile yet — nothing to do
        return {"scene": str(scene_id), "is_exemplar": False}

    ids = list(profile.exemplar_scene_ids or [])
    sid = str(scene_id)
    if body.enabled and sid not in ids:
        ids.append(sid)
    elif not body.enabled and sid in ids:
        ids.remove(sid)
    profile.exemplar_scene_ids = ids or None
    await session.commit()
    return {"scene": str(scene_id), "is_exemplar": body.enabled}


@router.get("/{scene_id}/draft-attempts", response_model=list[DraftAttemptOut])
async def scene_draft_attempts(scene_id: uuid.UUID, session: SessionDep) -> list[DraftAttempt]:
    """Provenance: every preserved stage of this scene's prose pipeline (raw draft, each enrichment
    pass, length compress/expand, final rendered), oldest first."""
    rows = (
        (
            await session.execute(
                select(DraftAttempt).where(DraftAttempt.scene_id == scene_id).order_by(DraftAttempt.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get("/{scene_id}/versions", response_model=list[SceneVersionOut])
async def scene_versions(scene_id: uuid.UUID, session: SessionDep) -> list[Scene]:
    """Full lineage of a scene: every version sharing its (chapter, scene_no), oldest first."""
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    rows = (
        (
            await session.execute(
                select(Scene)
                .where(Scene.chapter_id == scene.chapter_id, Scene.scene_no == scene.scene_no)
                .order_by(Scene.version)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{scene_id}/revert", response_model=SceneOut)
async def revert_scene(scene_id: uuid.UUID, session: SessionDep) -> Scene:
    """Roll a scene back to an earlier version: clone that version's prose into a NEW top version
    (versioning is rows, never destructive — DESIGN §3) and supersede the current one.

    The new version lands APPROVED directly — reverting is itself the human's decision, so it skips the
    inbox. It deliberately does NOT re-commit the beat's declared deltas (the ledger already reflects
    the approved state; re-applying relative '+N' deltas would double-count). `scene_id` is the version
    to revert TO."""
    target = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="scene not found")
    versions = (
        (
            await session.execute(
                select(Scene)
                .where(Scene.chapter_id == target.chapter_id, Scene.scene_no == target.scene_no)
                .order_by(Scene.version)
            )
        )
        .scalars()
        .all()
    )
    current = versions[-1]
    if current.id == target.id:
        raise HTTPException(status_code=409, detail="that version is already the current one")

    reverted = Scene(
        chapter_id=target.chapter_id,
        scene_no=target.scene_no,
        version=current.version + 1,
        parent_scene_id=current.id,
        status=SceneStatus.APPROVED,
        prose=target.prose,
        prose_source=target.prose_source,
        agent_original=target.agent_original,
        passes_run=target.passes_run,
        token_count=target.token_count,
        model=target.model,
    )
    current.status = SceneStatus.SUPERSEDED
    session.add(reverted)
    await session.flush()
    session.add(
        Approval(
            scene_id=reverted.id,
            version=reverted.version,
            decision=Decision.APPROVE,
            feedback=f"reverted to v{target.version}",
        )
    )
    await session.commit()
    return reverted


@router.delete("/{scene_id}", response_model=DeleteSceneOut)
async def delete_scene(scene_id: uuid.UUID, session: SessionDep) -> DeleteSceneOut:
    """Hard-delete one scene version and everything that points at it. Also purges draft jobs for
    the same chapter/scene slot. Used by the inbox's bulk 'delete selected'."""
    deleted_id, jobs_purged = await hard_delete_scene(session, scene_id)
    await session.commit()
    return DeleteSceneOut(deleted=deleted_id, jobs_purged=jobs_purged)


@router.get("/{scene_id}/fidelity", response_model=SceneFidelityOut)
async def scene_fidelity(scene_id: uuid.UUID, session: SessionDep) -> SceneFidelityOut:
    """Decision-ready fidelity status for a scene: whether a CURRENT report exists, its clause
    evaluations, and any operational (incomplete-evaluation) holds. Read-only and deterministic — it
    never triggers evaluation (that is the explicit manual rerun)."""
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    packet = await session.get(ScenePacket, scene.scene_packet_id) if scene.scene_packet_id else None
    if packet is None or not is_fidelity_active(dict(packet.body or {})):
        return SceneFidelityOut(
            scene_id=scene_id, has_report=False, is_current=False, currentness_reason="no_active_contract"
        )

    final_attempt = (
        (
            await session.execute(
                select(DraftAttempt)
                .where(DraftAttempt.scene_id == scene_id, DraftAttempt.stage == "final_rendered")
                .order_by(DraftAttempt.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if final_attempt is None:
        return SceneFidelityOut(
            scene_id=scene_id, has_report=False, is_current=False, currentness_reason="no_draft_attempt"
        )

    report_artifact = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.artifact_type == _FIDELITY_REPORT_TYPE, Artifact.domain_id == final_attempt.id)
                .order_by(Artifact.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if report_artifact is None:
        return SceneFidelityOut(scene_id=scene_id, has_report=False, is_current=False, currentness_reason="no_report")

    current, reason = report_is_current(
        report_artifact.body or {},
        scene_packet_id=packet.id,
        packet_fingerprint=fidelity_contract_fingerprint(dict(packet.body or {})),
        draft_attempt_id=final_attempt.id,
        prose=final_attempt.prose or scene.prose or "",
    )
    report = SceneFidelityReport.model_validate(report_artifact.body or {})
    evaluations = [
        ClauseEvaluationOut(
            requirement_id=e.requirement_id,
            clause_id=e.clause_id,
            mode=e.mode.value,
            result=e.result.value,
            enforcement=e.enforcement.value,
            post_draft_policy=e.post_draft_policy.value,
            evidence_valid=e.evidence_valid,
            explanation=e.explanation,
        )
        for e in report.clause_evaluations
    ]
    holds = [
        f"{e.clause_id}: {policy_outcome_for_clause_evaluation(e).reason}"
        for e in report.clause_evaluations
        if policy_outcome_for_clause_evaluation(e).kind == "operational_hold"
    ]
    return SceneFidelityOut(
        scene_id=scene_id,
        has_report=True,
        is_current=current,
        currentness_reason=reason,
        report_artifact_id=report_artifact.id,
        clause_evaluations=evaluations,
        operational_holds=holds,
    )
