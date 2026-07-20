"""DECIDE-LOCK: concurrent APPROVE decisions on one scene serialize on the scene row (SELECT ... FOR
UPDATE), so the one-shot approval side effects fire exactly once instead of racing."""

from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks, Response
from sqlalchemy import select

from dominion.api.routers import reviews
from dominion.shared.enums import BeatStatus, Decision, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, CharacterState, Scene
from dominion.shared.schemas import DecisionIn
from dominion.workers.oracle import Oracle


async def test_concurrent_approve_fires_side_effects_once(db_factory):
    async with db_factory() as setup:
        book = Book(title="D")
        setup.add(book)
        await setup.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        setup.add(ch)
        await setup.flush()
        setup.add(
            Beat(
                chapter_id=ch.id,
                scene_no=1,
                beat_text="b",
                status=BeatStatus.APPROVED,
                expected_state_changes={"Marcus": {"hp": "+5"}},
            )
        )
        sc = Scene(
            chapter_id=ch.id,
            scene_no=1,
            version=1,
            status=SceneStatus.PENDING_REVIEW,
            prose="p",
            prose_source="agent",
            agent_original="p",
        )
        setup.add(sc)
        await setup.flush()
        scene_id, book_id = sc.id, book.id
        await setup.commit()

    async def _approve() -> None:
        async with db_factory() as s:
            await reviews.decide(scene_id, DecisionIn(decision=Decision.APPROVE), s, BackgroundTasks(), Response())
            await s.commit()

    # Two APPROVEs race on the same scene; the FOR UPDATE lock serializes them, so the second sees the
    # first's committed APPROVED status and skips the one-shot effects.
    await asyncio.gather(_approve(), _approve())

    async with db_factory() as s:
        assert (await Oracle(s).current(book_id=book_id, character="Marcus"))["hp"] == 5  # applied once, not 10
        rows = (await s.execute(select(CharacterState).where(CharacterState.book_id == book_id))).scalars().all()
        assert len(rows) == 1  # one CharacterState row — no duplicate insert under the race
        assert (await s.get(Scene, scene_id)).status == SceneStatus.APPROVED
