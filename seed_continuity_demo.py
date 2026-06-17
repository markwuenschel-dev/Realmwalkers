"""Seed one deterministic continuity conflict into the inbox so the ContinuityPanel buttons can be
tested end to end.

Safe by design: everything is written to a dedicated "Continuity Demo" book, never your real story.
Re-run any time for a fresh conflict; old demo scenes can just be rejected from the inbox.

    uv run python seed_continuity_demo.py

Then open the inbox (http://localhost:5173), click the newest "Continuity Demo" scene, and use the
"Keep prose · fix ledger" / "Keep ledger · fix prose" buttons. The script prints a direct link.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    BeatStatus,
    ChapterStatus,
    GateMode,
    RunStatus,
    SceneStatus,
)
from dominion.shared.models import Beat, Book, Chapter, CharacterState, Critique, Run, Scene

DEMO_BOOK = "Continuity Demo"
POV = "Soren"

PROSE = (
    "Soren let the gate's blue light wash over him and, for the first time in days, allowed "
    "himself to breathe. The status sigil flared at the edge of his vision, etching a single "
    "line of fire:\n\n"
    "    Level 7.\n\n"
    "He almost laughed. Seven. After everything the Hollow had taken from him, the number felt "
    "less like a reward than a dare."
)
CONTEXT_SENTENCE = "The status sigil flared, etching a single line of fire: Level 7."


async def main() -> None:
    async with SessionFactory() as s:
        book = (await s.execute(select(Book).where(Book.title == DEMO_BOOK))).scalar_one_or_none()
        if book is None:
            book = Book(title=DEMO_BOOK, premise="Throwaway book for testing the continuity panel.")
            s.add(book)
            await s.flush()

        # An active, pause_each run so "Keep ledger · fix prose" can queue a real revision job.
        run = Run(book_id=book.id, scope_json={"demo": True}, gate_mode=GateMode.PAUSE_EACH,
                  token_budget=20_000, status=RunStatus.ACTIVE)
        s.add(run)

        # A fresh chapter each run so repeats never collide.
        next_ch = (await s.execute(
            select(func.coalesce(func.max(Chapter.chapter_no), 0) + 1).where(Chapter.book_id == book.id)
        )).scalar_one()
        chapter = Chapter(book_id=book.id, chapter_no=next_ch, pov=POV, status=ChapterStatus.DRAFTING)
        s.add(chapter)
        await s.flush()

        # Beat carries the declared stat + beat_text (used if you redraft via "Keep ledger").
        s.add(Beat(
            chapter_id=chapter.id, scene_no=1, characters_present=[POV], tags=[],
            expected_state_changes={POV: {"level": 5}}, status=BeatStatus.APPROVED,
            beat_text="Soren reaches the gate and takes stock of his power. His true level is 5.",
        ))

        # Make the ledger genuinely say level 5 (one row per character/book).
        cs = (await s.execute(select(CharacterState).where(
            CharacterState.book_id == book.id, CharacterState.character == POV
        ))).scalar_one_or_none()
        if cs is None:
            s.add(CharacterState(book_id=book.id, character=POV, stats_json={"level": 5}))
        else:
            cs.stats_json = {**(cs.stats_json or {}), "level": 5}

        # The pending scene whose prose says Level 7 — contradicting the ledger's 5.
        scene = Scene(
            chapter_id=chapter.id, scene_no=1, version=1, status=SceneStatus.PENDING_REVIEW,
            prose=PROSE, prose_source="agent", agent_original=PROSE,
            passes_run=["drafter"], model="seed-demo",
        )
        s.add(scene)
        await s.flush()

        # The HARD continuity flag the panel renders and the buttons act on.
        s.add(Critique(
            scene_id=scene.id, version=1, reviewer="continuity", severity="hard",
            note="Prose states Level 7 but the ledger has Level 5.",
            payload={
                "character": POV, "attribute": "level",
                "prose_value": 7, "ledger_value": 5,
                "context_sentence": CONTEXT_SENTENCE,
            },
        ))
        await s.commit()

        print("Seeded a continuity conflict.")
        print(f"  book:     {DEMO_BOOK}  (chapter {next_ch}, scene 1)")
        print(f"  scene id: {scene.id}")
        print("  Open the inbox and click this scene, or go straight to:")
        print(f"    http://localhost:5173/scenes/{scene.id}")


if __name__ == "__main__":
    asyncio.run(main())
