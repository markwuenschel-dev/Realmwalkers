"""Author-content endpoints: write a manuscript section by hand (approved human scene, version-ups +
supersedes), and re-draft existing scenes (a DRAFT job that TARGETS the scene, so the worker versions
up instead of duplicating). Router functions called directly with a session (see tests/conftest.py)."""
from __future__ import annotations

from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import chapters
from dominion.shared.enums import JobKind, JobStatus, SceneStatus
from dominion.shared.models import Book, Chapter, Job, Scene
from dominion.shared.schemas import HumanSceneIn, RedraftIn


async def _book_chapter(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    return ch


async def test_create_human_scene_is_approved_and_versions_up(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        first = await chapters.create_human_scene(
            ch.id, HumanSceneIn(scene_no=1, prose="hand-written one"), s, BackgroundTasks(),
        )
        assert first.status == SceneStatus.APPROVED
        assert first.prose_source == "human"
        assert first.version == 1

        # writing the same scene_no again supersedes the prior and versions up
        second = await chapters.create_human_scene(
            ch.id, HumanSceneIn(scene_no=1, prose="hand-written two"), s, BackgroundTasks(),
        )
        assert second.version == 2
        assert second.parent_scene_id == first.id
        prior = (await s.execute(select(Scene).where(Scene.id == first.id))).scalar_one()
        assert prior.status == SceneStatus.SUPERSEDED


async def test_redraft_queues_a_draft_targeting_the_scene(db_factory):
    async with db_factory() as s:
        ch = await _book_chapter(s)
        scene = Scene(chapter_id=ch.id, scene_no=3, prose="drafted", version=1,
                      status=SceneStatus.APPROVED)
        s.add(scene)
        await s.flush()

        out = await chapters.redraft_scenes(ch.id, RedraftIn(scene_ids=[scene.id]), s)
        assert out["queued"] == 1
        job = (await s.execute(select(Job).where(Job.target_scene_id == scene.id))).scalar_one()
        # targets the existing scene so the worker version-ups + supersedes (no duplicate)
        assert job.kind == JobKind.DRAFT
        assert job.status == JobStatus.QUEUED
        assert job.scene_no == 3
