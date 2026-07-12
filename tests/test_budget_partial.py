"""Budget partial-persist (DESIGN §10): a job that blows its token budget after the spine exists
saves the partial as a flagged DRAFT instead of losing the work. DB-backed; the model is mocked."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.enums import BeatStatus, GateMode, JobKind, JobStatus, RunStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, CharacterState, Critique, Job, Run, Scene
from dominion.workers import llm, worker
from dominion.workers.budget import Usage
from dominion.workers.specialists import drafter as drafter_mod
from tests.conftest import seed_scene_packet

SPINE = "Marcus stands at the gate. The panel reads LEVEL 5, stark and certain."


async def _book(s, title="Dominion Realm"):
    book = Book(title=title)
    s.add(book)
    await s.flush()
    return book


async def _chapter(s, book, no=1, pov="Marcus"):
    ch = Chapter(book_id=book.id, chapter_no=no, pov=pov)
    s.add(ch)
    await s.flush()
    return ch


async def _run(s, book):
    run = Run(
        book_id=book.id,
        scope_json={"chapter": 1},
        gate_mode=GateMode.PAUSE_EACH,
        token_budget=40_000,
        status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    return run


async def _beat(s, ch, scene_no=1, *, chars=("Marcus",), text="Marcus opens his status panel."):
    b = Beat(
        chapter_id=ch.id,
        scene_no=scene_no,
        tags=[],
        characters_present=list(chars),
        expected_state_changes=None,
        status=BeatStatus.APPROVED,
        beat_text=text,
    )
    s.add(b)
    await s.flush()
    await seed_scene_packet(s, chapter=ch, beat=b)  # drafting is fail-closed on an approved packet
    return b


async def _fake_draft(self, prose, ctx):
    ctx.budget.charge(Usage(5, 5))  # the spine costs a little; stays under budget
    return SPINE


async def test_budget_exceeded_saves_partial_draft_with_flag(db_factory, monkeypatch):
    # The spine succeeds; the FIRST reviewer's model call tips the budget over -> partial DRAFT + flag.
    async def fake_complete(**kwargs):
        kwargs["budget"].charge(Usage(100, 100))
        return "[]", Usage(100, 100)

    monkeypatch.setattr(drafter_mod.Drafter, "run", _fake_draft)
    monkeypatch.setattr(llm, "complete", fake_complete)

    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        run = await _run(s, book)
        # a ledger value makes the continuity reviewer actually call the model (and blow the budget)
        s.add(CharacterState(book_id=book.id, character="Marcus", stats_json={"level": 5}))
        await _beat(s, ch, 1)
        s.add(
            Job(
                run_id=run.id,
                book_id=book.id,
                kind=JobKind.DRAFT,
                chapter_no=1,
                scene_no=1,
                token_budget=50,
                status=JobStatus.QUEUED,
            )
        )  # tiny budget: spine fits, reviewer doesn't
        await s.commit()

    assert await worker.run_once(session_factory=db_factory) is True

    async with db_factory() as s:
        sc = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalars().first()
        assert sc is not None
        assert sc.status == SceneStatus.DRAFT  # quarantined, never enters the inbox
        assert "Marcus stands at the gate" in (sc.prose or "")  # the spine was NOT lost
        crits = (await s.execute(select(Critique).where(Critique.scene_id == sc.id))).scalars().all()
        budget_flags = [c for c in crits if c.reviewer == "budget"]
        assert len(budget_flags) == 1 and budget_flags[0].severity == "block"
        # the worker still committed the partial and finished the job (not stuck running/queued)
        job = (await s.execute(select(Job))).scalar_one()
        assert job.status == JobStatus.DONE


async def test_within_budget_yields_pending_review_and_no_budget_flag(db_factory, monkeypatch):
    # Same shape, ample budget: normal pending_review scene, no budget flag.
    async def fake_complete(**kwargs):
        kwargs["budget"].charge(Usage(1, 1))
        return "[]", Usage(1, 1)

    monkeypatch.setattr(drafter_mod.Drafter, "run", _fake_draft)
    monkeypatch.setattr(llm, "complete", fake_complete)

    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        run = await _run(s, book)
        s.add(CharacterState(book_id=book.id, character="Marcus", stats_json={"level": 5}))
        await _beat(s, ch, 1)
        s.add(
            Job(
                run_id=run.id,
                book_id=book.id,
                kind=JobKind.DRAFT,
                chapter_no=1,
                scene_no=1,
                token_budget=40_000,
                status=JobStatus.QUEUED,
            )
        )
        await s.commit()

    assert await worker.run_once(session_factory=db_factory) is True

    async with db_factory() as s:
        sc = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalars().first()
        assert sc is not None
        assert sc.status == SceneStatus.PENDING_REVIEW
        crits = (await s.execute(select(Critique).where(Critique.scene_id == sc.id))).scalars().all()
        assert not [c for c in crits if c.reviewer == "budget"]
