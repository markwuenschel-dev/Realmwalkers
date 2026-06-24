"""Read endpoints for the review inbox (DESIGN §9), plus the voice-exemplar toggle. All real."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.enums import Decision, SceneStatus
from dominion.shared.models import Approval, Chapter, Critique, PovProfile, Scene
from dominion.shared.schemas import CritiqueOut, ExemplarIn, SceneDetail, SceneOut, SceneVersionOut

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
