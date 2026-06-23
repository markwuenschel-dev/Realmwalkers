"""The advisory reviewers run concurrently (perf): a scene's reviewers fan out in parallel rather
than one-after-another. We prove it deterministically with probes that detect simultaneous in-flight
calls, and confirm a reviewer's BudgetExceeded still downgrades the scene to a partial DRAFT.
DB-backed; the drafter + reviewers are faked (no network)."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from dominion.shared.enums import BeatStatus, GateMode, JobKind, JobStatus, RunStatus, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, Critique, Job, Run
from dominion.workers import pipeline
from dominion.workers.budget import BudgetExceeded
from dominion.workers.reviewers.base import Flag
from dominion.workers.specialists import drafter as drafter_mod


async def _setup_draft_job(s):
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    run = Run(book_id=book.id, scope_json={"chapter": 1}, gate_mode=GateMode.PAUSE_EACH,
              token_budget=40_000, status=RunStatus.ACTIVE)
    s.add(run)
    await s.flush()
    s.add(Beat(chapter_id=ch.id, scene_no=1, tags=[], characters_present=["Marcus"],
               status=BeatStatus.APPROVED, beat_text="Marcus presses on."))
    job = Job(run_id=run.id, kind=JobKind.DRAFT, chapter_no=1, scene_no=1,
              token_budget=40_000, status=JobStatus.QUEUED)
    s.add(job)
    await s.flush()
    return job


class _Probe:
    """Records peak concurrency: if reviewers run in parallel, all are 'live' at once."""
    def __init__(self, state: dict[str, int], name: str) -> None:
        self.state, self.name = state, name

    async def review(self, prose: str, ctx: object) -> list[Flag]:
        self.state["live"] += 1
        self.state["peak"] = max(self.state["peak"], self.state["live"])
        await asyncio.sleep(0.02)  # hold the slot so genuinely-parallel calls overlap
        self.state["live"] -= 1
        return [Flag(reviewer=self.name, severity="info", note=f"{self.name} ok")]


async def test_reviewers_run_concurrently(db_factory, monkeypatch):
    async def fake_draft(self, prose, ctx):
        return "A short spine of prose."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    state = {"live": 0, "peak": 0}
    probes = [_Probe(state, f"r{i}") for i in range(3)]
    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: probes)

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        scene = await pipeline.generate_one_scene(s, job)
        await s.commit()

        assert state["peak"] == 3  # all three in flight at once — not serialized
        crits = (await s.execute(select(Critique).where(Critique.scene_id == scene.id))).scalars().all()
        # every reviewer's flag persisted, in reviewer order (continuity-first convention preserved)
        assert [c.reviewer for c in crits] == ["r0", "r1", "r2"]
        assert scene.status == SceneStatus.PENDING_REVIEW


async def test_reviewer_budget_exceeded_downgrades_to_partial_draft(db_factory, monkeypatch):
    async def fake_draft(self, prose, ctx):
        return "A short spine of prose."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)

    class _Boom:
        async def review(self, prose, ctx):
            raise BudgetExceeded("over")

    class _Ok:
        async def review(self, prose, ctx):
            return [Flag(reviewer="ok", severity="info", note="fine")]

    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [_Ok(), _Boom()])

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        scene = await pipeline.generate_one_scene(s, job)
        await s.commit()

        # one reviewer blew the budget -> quarantined DRAFT + a hard budget flag; the spine survives
        assert scene.status == SceneStatus.DRAFT
        assert "A short spine of prose." in (scene.prose or "")
        crits = (await s.execute(select(Critique).where(Critique.scene_id == scene.id))).scalars().all()
        assert any(c.reviewer == "budget" and c.severity == "hard" for c in crits)


async def test_non_budget_reviewer_error_fails_the_job(db_factory, monkeypatch):
    async def fake_draft(self, prose, ctx):
        return "A short spine of prose."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)

    class _Crash:
        async def review(self, prose, ctx):
            raise RuntimeError("reviewer bug")

    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [_Crash()])

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        try:
            await pipeline.generate_one_scene(s, job)
            raise AssertionError("expected the reviewer error to propagate")
        except RuntimeError as e:
            assert "reviewer bug" in str(e)
