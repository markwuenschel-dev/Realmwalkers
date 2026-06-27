"""Read endpoints for the review inbox (DESIGN §9), plus the voice-exemplar toggle. All real."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select, update

from dominion.api.deps import SessionDep
from dominion.shared.enums import Decision, SceneStatus
from dominion.shared.models import (
    Annotation,
    Approval,
    Chapter,
    CharacterState,
    Critique,
    DraftAttempt,
    EditPair,
    Job,
    KnowledgeFact,
    PovProfile,
    Scene,
    Suggestion,
    Summary,
)
from dominion.shared.schemas import (
    CritiqueOut,
    DraftAttemptOut,
    ExemplarIn,
    SceneDetail,
    SceneOut,
    SceneVersionOut,
)

router = APIRouter(prefix="/scenes", tags=["scenes"])


async def _pov_profile_for_scene(
    session: SessionDep, scene: Scene, *, create_if_missing: bool = False
) -> PovProfile | None:
    """The PovProfile that owns this scene's voice — keyed by the scene's (book, chapter POV)."""
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return None
    profile = (await session.execute(
        select(PovProfile).where(
            PovProfile.book_id == chapter.book_id, PovProfile.character == chapter.pov
        )
    )).scalar_one_or_none()
    if profile is None and create_if_missing:
        profile = PovProfile(book_id=chapter.book_id, character=chapter.pov)
        session.add(profile)
        await session.flush()
    return profile


@router.get("/pending", response_model=list[SceneOut])
async def pending(session: SessionDep) -> list[Scene]:
    rows = (
        await session.execute(
            select(Scene)
            .where(Scene.status == SceneStatus.PENDING_REVIEW)
            .order_by(Scene.created_at)
        )
    ).scalars().all()
    return list(rows)


@router.get("/{scene_id}", response_model=SceneDetail)
async def scene_detail(scene_id: uuid.UUID, session: SessionDep) -> SceneDetail:
    scene = (
        await session.execute(select(Scene).where(Scene.id == scene_id))
    ).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    crits = (
        await session.execute(select(Critique).where(Critique.scene_id == scene_id))
    ).scalars().all()
    detail = SceneDetail.model_validate(scene)
    detail.critiques = [CritiqueOut.model_validate(c) for c in crits]
    profile = await _pov_profile_for_scene(session, scene)
    detail.is_exemplar = bool(profile and str(scene.id) in (profile.exemplar_scene_ids or []))
    return detail


@router.post("/{scene_id}/exemplar")
async def set_exemplar(
    scene_id: uuid.UUID, body: ExemplarIn, session: SessionDep
) -> dict[str, str | bool]:
    """Mark/unmark this scene as a voice exemplar for its POV (LEARNING_FROM_EDITS Tier 2).

    Adds/removes the scene id on the POV's `PovProfile.exemplar_scene_ids` — the list the drafter
    few-shots on. Idempotent; disabling on a scene with no profile is a no-op.
    """
    scene = (
        await session.execute(select(Scene).where(Scene.id == scene_id))
    ).scalar_one_or_none()
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
    rows = (await session.execute(
        select(DraftAttempt)
        .where(DraftAttempt.scene_id == scene_id)
        .order_by(DraftAttempt.created_at)
    )).scalars().all()
    return list(rows)


@router.get("/{scene_id}/versions", response_model=list[SceneVersionOut])
async def scene_versions(scene_id: uuid.UUID, session: SessionDep) -> list[Scene]:
    """Full lineage of a scene: every version sharing its (chapter, scene_no), oldest first."""
    scene = (
        await session.execute(select(Scene).where(Scene.id == scene_id))
    ).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")
    rows = (
        await session.execute(
            select(Scene)
            .where(Scene.chapter_id == scene.chapter_id, Scene.scene_no == scene.scene_no)
            .order_by(Scene.version)
        )
    ).scalars().all()
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
    versions = (await session.execute(
        select(Scene)
        .where(Scene.chapter_id == target.chapter_id, Scene.scene_no == target.scene_no)
        .order_by(Scene.version)
    )).scalars().all()
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
    session.add(Approval(
        scene_id=reverted.id, version=reverted.version, decision=Decision.APPROVE,
        feedback=f"reverted to v{target.version}",
    ))
    await session.commit()
    return reverted


@router.delete("/{scene_id}")
async def delete_scene(scene_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    """Hard-delete one scene version and everything that points at it. Scenes are referenced by
    critiques / annotations / approvals / suggestions and edit-pairs (NOT NULL) and softly by the
    ledger, summaries, jobs, child versions, draft-attempt provenance, and knowledge facts — so we
    remove the hard dependents and null the soft refs first, then the row, or the FK constraints
    (a nullable FK still blocks a delete; there is no ON DELETE SET NULL) would fail the delete.
    Used by the inbox's bulk 'delete selected'."""
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id))).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    # Hard dependents: NOT NULL scene_id, meaningless without this scene version. EditPair is training
    # data keyed to (scene_id, version) — once the version is gone the before/after pair is dangling.
    for model in (Critique, Annotation, Suggestion, Approval, EditPair):
        await session.execute(delete(model).where(model.scene_id == scene_id))
    # Soft references: keep the rows, just detach them from the scene being removed.
    await session.execute(
        update(CharacterState).where(CharacterState.as_of_scene_id == scene_id)
        .values(as_of_scene_id=None)
    )
    await session.execute(
        update(Summary).where(Summary.up_to_scene_id == scene_id).values(up_to_scene_id=None)
    )
    await session.execute(
        update(Job).where(Job.target_scene_id == scene_id).values(target_scene_id=None)
    )
    await session.execute(
        update(Scene).where(Scene.parent_scene_id == scene_id).values(parent_scene_id=None)
    )
    # DraftAttempt is append-only provenance ("never destroyed") — detach, don't delete.
    await session.execute(
        update(DraftAttempt).where(DraftAttempt.scene_id == scene_id).values(scene_id=None)
    )
    # KnowledgeFact is a book-level ledger; it can reference the deleted scene from three columns.
    for col in (
        KnowledgeFact.source_scene_id,
        KnowledgeFact.known_by_reader_after_scene_id,
        KnowledgeFact.known_by_character_after_scene_id,
    ):
        await session.execute(update(KnowledgeFact).where(col == scene_id).values({col: None}))
    await session.execute(delete(Scene).where(Scene.id == scene_id))
    await session.commit()
    return {"deleted": str(scene_id)}
