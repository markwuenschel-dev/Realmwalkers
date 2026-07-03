"""Gate-1 + read-surface integration tests against real Postgres (DESIGN §4, §8, §9).

Call the router functions directly (as tests/test_phase2.py does); the planner LLM is mocked. These
skip automatically when Postgres isn't reachable (see tests/conftest.py)."""

from __future__ import annotations

from sqlalchemy import select

from dominion.api.routers import books as books_router
from dominion.api.routers import chapters as chapters_router
from dominion.api.routers import runs as runs_router
from dominion.api.routers import scenes as scenes_router
from dominion.shared.enums import (
    BeatStatus,
    ChapterStatus,
    JobStatus,
    SceneStatus,
)
from dominion.shared.models import Beat, Book, Chapter, Job, Run, Scene
from dominion.shared.schemas import (
    ApproveBeatsIn,
    RunStartIn,
    SceneVersionOut,
)
from dominion.workers import planner

_PROPOSED = [
    {
        "scene_no": 1,
        "beat_text": "Marcus wakes.",
        "characters_present": ["Marcus"],
        "tags": [],
        "expected_state_changes": None,
        "knowledge_injections": [],
    },
    {
        "scene_no": 2,
        "beat_text": "He explores.",
        "characters_present": ["Marcus"],
        "tags": ["combat"],
        "expected_state_changes": {"Marcus": {"level": "+1"}},
        "knowledge_injections": ["the bridge is cursed"],
    },
]


async def _book(s, title="Dominion Realm"):
    book = Book(title=title)
    s.add(book)
    await s.flush()
    return book


async def test_start_run_proposes_and_persists_beats(db_factory, monkeypatch):
    async def fake_propose(**kwargs):
        assert kwargs["outline"] and kwargs["pov"] == "Marcus"
        return _PROPOSED

    monkeypatch.setattr(planner, "propose_beats", fake_propose)

    async with db_factory() as s:
        book = await _book(s)
        out = await runs_router.start_run(
            RunStartIn(book_id=book.id, chapter_no=1, pov="Marcus", outline="Marcus wakes, then explores."),
            s,
        )
        await s.commit()

        assert out.pov == "Marcus" and len(out.beats) == 2
        ch = (await s.execute(select(Chapter).where(Chapter.id == out.chapter_id))).scalar_one()
        assert ch.outline == "Marcus wakes, then explores."
        assert ch.status == ChapterStatus.BEATS_PROPOSED
        rows = (
            (await s.execute(select(Beat).where(Beat.chapter_id == out.chapter_id).order_by(Beat.scene_no)))
            .scalars()
            .all()
        )
        assert [b.scene_no for b in rows] == [1, 2]
        assert rows[1].tags == ["combat"]
        assert rows[1].expected_state_changes == {"Marcus": {"level": "+1"}}
        assert all(b.status == BeatStatus.PROPOSED for b in rows)
        # a Run was created for this book
        assert (await s.execute(select(Run).where(Run.id == out.run_id))).scalar_one()


async def test_approve_subset_enqueues_only_selected(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        run = Run(book_id=book.id, scope_json={"chapter": 1}, gate_mode="pause_each", token_budget=40_000)
        s.add(run)
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        b1 = Beat(chapter_id=ch.id, scene_no=1, beat_text="one", status=BeatStatus.PROPOSED)
        b2 = Beat(chapter_id=ch.id, scene_no=2, beat_text="two", status=BeatStatus.PROPOSED)
        s.add_all([b1, b2])
        await s.flush()

        out = await chapters_router.approve_beats(ch.id, s, ApproveBeatsIn(beat_ids=[b1.id]))
        await s.commit()
        assert out["approved"] == 1
        jobs = (await s.execute(select(Job).where(Job.status == JobStatus.QUEUED))).scalars().all()
        assert jobs == []
        assert (await s.get(Beat, b1.id)).status == BeatStatus.APPROVED
        assert (await s.get(Beat, b2.id)).status == BeatStatus.PROPOSED  # unselected stays proposed


async def test_scene_versions_returns_lineage(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        v1 = Scene(
            chapter_id=ch.id,
            scene_no=1,
            version=1,
            status=SceneStatus.SUPERSEDED,
            prose="first",
            prose_source="agent",
            agent_original="first",
        )
        s.add(v1)
        await s.flush()
        v2 = Scene(
            chapter_id=ch.id,
            scene_no=1,
            version=2,
            status=SceneStatus.PENDING_REVIEW,
            prose="second",
            prose_source="agent",
            parent_scene_id=v1.id,
            agent_original="second",
        )
        s.add(v2)
        await s.flush()

        out = await scenes_router.scene_versions(v2.id, s)
        assert [sc.version for sc in out] == [1, 2]
        # the DTO carries the preserved pre-edit text for diffing
        assert SceneVersionOut.model_validate(out[1]).agent_original == "second"


async def test_manuscript_assembles_latest_approved_in_order(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch1 = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        ch2 = Chapter(book_id=book.id, chapter_no=2, pov="Serra")
        s.add_all([ch1, ch2])
        await s.flush()
        s.add_all(
            [
                Scene(
                    chapter_id=ch1.id,
                    scene_no=1,
                    version=1,
                    status=SceneStatus.APPROVED,
                    prose="A",
                    prose_source="agent",
                ),
                Scene(
                    chapter_id=ch1.id,
                    scene_no=1,
                    version=2,
                    status=SceneStatus.APPROVED,
                    prose="A2",
                    prose_source="agent",
                ),  # latest approved version wins
                Scene(
                    chapter_id=ch1.id,
                    scene_no=2,
                    version=1,
                    status=SceneStatus.APPROVED,
                    prose="B",
                    prose_source="agent",
                ),
                Scene(
                    chapter_id=ch1.id,
                    scene_no=3,
                    version=1,
                    status=SceneStatus.PENDING_REVIEW,
                    prose="draft",
                    prose_source="agent",
                ),  # unapproved -> excluded
                Scene(
                    chapter_id=ch2.id,
                    scene_no=1,
                    version=1,
                    status=SceneStatus.APPROVED,
                    prose="C",
                    prose_source="agent",
                ),
            ]
        )
        await s.flush()

        out = await books_router.manuscript(book.id, s)
        assert out.title == "Dominion Realm"
        assert [c.chapter_no for c in out.chapters] == [1, 2]
        assert [(sc.scene_no, sc.prose) for sc in out.chapters[0].scenes] == [(1, "A2"), (2, "B")]
        assert [(sc.scene_no, sc.prose) for sc in out.chapters[1].scenes] == [(1, "C")]
