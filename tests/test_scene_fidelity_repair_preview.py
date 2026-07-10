"""Lane 6 — author-controlled repair previews (DB-backed).

A preview never changes the current Scene; only accept/edit create a NEW revision; reject leaves the
Critique/Issue intact. Reuses the triage setup to obtain a real repair-eligible Issue.
"""

from __future__ import annotations

from sqlalchemy import select
from test_scene_fidelity_production import PROSE, _issues, _setup

from dominion.shared.enums import IssueStatus, SceneStatus
from dominion.shared.models import DraftAttempt, Scene
from dominion.workers import production_repair
from dominion.workers.scene_fidelity.repair_preview import REPAIR_PREVIEW_ARTIFACT_TYPE

CANDIDATE = 'Marcus stepped back from the door. "Your call," he said, and she stayed.'


async def _issue_for_preview(s):
    run, scene, sp, da = await _setup(s)
    await production_repair.triage_scene_fidelity_for_production(s, run=run)
    issue = (await _issues(s, run))[0]
    return run, scene, sp, issue


async def _scene_versions(s, chapter_id):
    return (
        (await s.execute(select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.version))).scalars().all()
    )


async def test_preview_is_immutable_and_never_changes_the_scene(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, issue = await _issue_for_preview(s)
        preview = await production_repair.create_repair_preview(
            s, issue=issue, candidate_prose=CANDIDATE, rationale="restore Serra's agency"
        )
        assert preview.artifact_type == REPAIR_PREVIEW_ARTIFACT_TYPE
        assert preview.body["candidate_prose"] == CANDIDATE
        assert preview.body["clause_ids"] == ["cl-1"]
        assert preview.body["diff"]  # a non-empty unified diff
        assert preview.body["preservation_boundary"]
        # the current Scene is untouched — no revision, prose intact, still current.
        await s.refresh(scene)
        assert scene.prose == PROSE
        assert scene.status != SceneStatus.SUPERSEDED
        assert len(await _scene_versions(s, scene.chapter_id)) == 1


async def test_accept_creates_a_new_author_visible_revision(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, issue = await _issue_for_preview(s)
        preview = await production_repair.create_repair_preview(
            s, issue=issue, candidate_prose=CANDIDATE, rationale="x"
        )
        new_scene = await production_repair.accept_repair_preview(s, preview_artifact_id=preview.id)
        assert new_scene.version == scene.version + 1
        assert new_scene.parent_scene_id == scene.id
        assert new_scene.prose == CANDIDATE
        assert new_scene.prose_source == "agent"
        assert new_scene.status == SceneStatus.PENDING_REVIEW
        await s.refresh(scene)
        assert scene.status == SceneStatus.SUPERSEDED
        await s.refresh(issue)
        assert issue.status == IssueStatus.REPAIRED.value
        await s.refresh(preview)
        assert preview.status == "materialized"
        # a final DraftAttempt exists for the new revision (fresh evaluation has a target).
        da = (await s.execute(select(DraftAttempt).where(DraftAttempt.scene_id == new_scene.id))).scalars().all()
        assert len(da) == 1


async def test_edited_preview_records_human_edit_provenance(db_factory) -> None:
    edited = "Marcus watched her decide, and said nothing."
    async with db_factory() as s:
        run, scene, sp, issue = await _issue_for_preview(s)
        preview = await production_repair.create_repair_preview(
            s, issue=issue, candidate_prose=CANDIDATE, rationale="x"
        )
        new_scene = await production_repair.accept_repair_preview(
            s, preview_artifact_id=preview.id, edited_prose=edited
        )
        assert new_scene.prose == edited
        assert new_scene.prose_source == "agent+human_edit"


async def test_reject_leaves_the_issue_and_scene_intact(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, issue = await _issue_for_preview(s)
        preview = await production_repair.create_repair_preview(
            s, issue=issue, candidate_prose=CANDIDATE, rationale="x"
        )
        status_before = issue.status
        await production_repair.reject_repair_preview(s, preview_artifact_id=preview.id, reason="not in her voice")
        await s.refresh(preview)
        assert preview.status == "rejected"
        await s.refresh(issue)
        assert issue.status == status_before  # Issue untouched
        await s.refresh(scene)
        assert scene.prose == PROSE
        assert scene.status != SceneStatus.SUPERSEDED
        assert len(await _scene_versions(s, scene.chapter_id)) == 1
