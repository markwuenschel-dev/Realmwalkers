"""Phase 2 integration tests against real Postgres: ledger, RAG, summaries, auto-advance,
revise pipeline, continuity resolution. LLM calls (drafter/summaries) are mocked."""
from __future__ import annotations

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import reviews
from dominion.api.routers.scenes import scene_detail
from dominion.shared.enums import (
    BeatStatus,
    Decision,
    GateMode,
    JobKind,
    JobStatus,
    RunStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    CharacterState,
    Critique,
    Job,
    Run,
    Scene,
    Summary,
)
from dominion.shared.schemas import ContinuityResolveIn, DecisionIn
from dominion.workers import enqueue, llm, worker
from dominion.workers.budget import Usage
from dominion.workers.job_scheduler import schedule_next_after_approval
from dominion.workers.memory import canon_rag, ledger, summaries
from dominion.workers.specialists import drafter as drafter_mod
from tests.conftest import seed_scene_packet


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


async def _run(s, book, gate=GateMode.PAUSE_EACH):
    run = Run(book_id=book.id, scope_json={"chapter": 1}, gate_mode=gate,
              token_budget=40_000, status=RunStatus.ACTIVE)
    s.add(run)
    await s.flush()
    return run


async def _beat(s, ch, scene_no=1, *, esc=None, chars=("Marcus",), text="Marcus wakes."):
    b = Beat(chapter_id=ch.id, scene_no=scene_no, tags=[], characters_present=list(chars),
             expected_state_changes=esc, status=BeatStatus.APPROVED, beat_text=text)
    s.add(b)
    await s.flush()
    await seed_scene_packet(s, chapter=ch, beat=b)  # drafting is fail-closed on an approved packet
    return b


async def _scene(s, ch, scene_no=1, *, status=SceneStatus.PENDING_REVIEW, prose="Prose.", version=1):
    sc = Scene(chapter_id=ch.id, scene_no=scene_no, version=version, status=status,
               prose=prose, prose_source="agent", passes_run=["drafter"])
    s.add(sc)
    await s.flush()
    return sc


async def test_ledger_commits_and_accumulates(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        await _beat(s, ch, 1, esc={"Marcus": {"level": "+1", "hp": 100, "items": ["sword"]}})
        sc = await _scene(s, ch, 1)
        await ledger.commit_declared_deltas(s, scene_id=sc.id)
        await s.commit()
        row = (await s.execute(select(CharacterState))).scalar_one()
        assert row.stats_json == {"level": 1, "hp": 100, "items": ["sword"]}

    async with db_factory() as s:
        ch = (await s.execute(select(Chapter))).scalars().first()
        await _beat(s, ch, 2, esc={"Marcus": {"level": "+2"}})
        sc2 = await _scene(s, ch, 2)
        await ledger.commit_declared_deltas(s, scene_id=sc2.id)
        await s.commit()
        row = (await s.execute(select(CharacterState))).scalar_one()
        assert row.stats_json["level"] == 3  # relative delta applied on top


async def test_canon_rag_retrieves_relevant(db_factory, tmp_path):
    (tmp_path / "eyes.md").write_text(
        "The Eyes of Meszkhal let their bearer perceive spectral seams threaded through reality."
    )
    (tmp_path / "home.md").write_text(
        "Marcus grew up gutting cod in the cold fishing village of Dunmoor."
    )
    async with db_factory() as s:
        book = await _book(s)
        n = await canon_rag.ingest_path(s, book_id=book.id, root=tmp_path)
        await s.commit()
        assert n >= 2
        hits = await canon_rag.retrieve(s, book_id=book.id, query="spectral seams Meszkhal eyes", k=2)
        assert hits and "Meszkhal" in hits[0]
        hits2 = await canon_rag.retrieve(s, book_id=book.id, query="fishing village cod Dunmoor", k=2)
        assert hits2 and "Dunmoor" in hits2[0]


async def test_summaries_refresh_and_read(db_factory, monkeypatch):
    calls: list[str] = []

    async def fake_complete(**kwargs):
        calls.append(kwargs["user"])
        return "ROLLING SUMMARY: Marcus woke in the Realm.", Usage(50, 50)

    monkeypatch.setattr(llm, "complete", fake_complete)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1, status=SceneStatus.APPROVED,
                          prose="Marcus opened his eyes to a humming sky.")
        await summaries.refresh_on_approval(s, scene_id=sc.id)
        await s.commit()
        assert await summaries.pov_summary(s, book_id=book.id, pov="Marcus") == \
            "ROLLING SUMMARY: Marcus woke in the Realm."
        rows = (await s.execute(select(Summary))).scalars().all()
        assert sorted(r.scope for r in rows) == ["omniscient", "pov"]
    assert len(calls) == 2  # one pov-scoped, one omniscient


async def test_approve_commits_ledger_summary_and_autoadvances(db_factory, monkeypatch):
    async def fake_complete(**kwargs):
        return "summary", Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        await _run(s, book, GateMode.PAUSE_EACH)
        await _beat(s, ch, 1, esc={"Marcus": {"level": "+1"}})
        await _beat(s, ch, 2)  # the next scene's beat exists -> auto-advance fires
        sc1 = await _scene(s, ch, 1, prose="Marcus wakes.")
        result = await reviews.decide(sc1.id, DecisionIn(decision=Decision.APPROVE), s, BackgroundTasks())
        await s.commit()
        assert result["status"] == "approved"
        assert result["next_job"] is not None
        assert (await s.execute(select(CharacterState))).scalar_one().stats_json["level"] == 1
        job = (await s.execute(
            select(Job).where(Job.scene_no == 2, Job.status == JobStatus.QUEUED)
        )).scalar_one()
        assert job.kind == JobKind.DRAFT

    async with db_factory() as s:  # auto-advance is idempotent
        sc1 = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalars().first()
        await schedule_next_after_approval(s, sc1)
        await schedule_next_after_approval(s, sc1)
        await s.commit()
        jobs = (await s.execute(
            select(Job).where(Job.scene_no == 2, Job.status == JobStatus.QUEUED)
        )).scalars().all()
        assert len(jobs) == 1


async def test_reapprove_does_not_double_apply_deltas_or_readvance(db_factory, monkeypatch):
    """An already-approved scene can be re-opened and re-approved (e.g. after a hand-edit). The
    one-shot side effects must not repeat: a relative ledger delta must not double-count, and the
    next scene must not be re-enqueued — only the edited prose is re-committed."""
    async def fake_complete(**kwargs):
        return "summary", Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        await _run(s, book, GateMode.PAUSE_EACH)
        await _beat(s, ch, 1, esc={"Marcus": {"level": "+1"}})
        await _beat(s, ch, 2)
        sc1 = await _scene(s, ch, 1, prose="Marcus wakes.")
        await reviews.decide(sc1.id, DecisionIn(decision=Decision.APPROVE), s, BackgroundTasks())
        await s.commit()

        result = await reviews.decide(
            sc1.id,
            DecisionIn(decision=Decision.APPROVE, edited_prose="Marcus wakes, edited."),
            s, BackgroundTasks(),
        )
        await s.commit()

        assert result["next_job"] is None  # re-approval does not re-advance
        # the relative delta applied exactly once, not twice
        assert (await s.execute(select(CharacterState))).scalar_one().stats_json["level"] == 1
        sc1b = (await s.execute(select(Scene).where(Scene.id == sc1.id))).scalar_one()
        assert sc1b.prose == "Marcus wakes, edited."          # hand-edit landed
        assert sc1b.prose_source == "agent+human_edit"
        jobs = (await s.execute(
            select(Job).where(Job.scene_no == 2, Job.status == JobStatus.QUEUED)
        )).scalars().all()
        assert len(jobs) == 1                                  # still exactly one queued draft


async def test_revise_enqueues_and_pipeline_versions(db_factory, monkeypatch):
    async def fake_draft(self, prose, ctx):
        assert ctx.revise_feedback == "Cut the throat-clearing; open mid-action."
        assert "slowly woke" in (ctx.prior_prose or "")
        return "Revised: Marcus was already on his feet when the sky screamed."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        await _run(s, book)
        await _beat(s, ch, 1)
        sc1 = await _scene(s, ch, 1, prose="Marcus slowly woke up. It was a sky.")
        out = await reviews.decide(
            sc1.id,
            DecisionIn(decision=Decision.REVISE, feedback="Cut the throat-clearing; open mid-action."),
            s,
            BackgroundTasks(),
        )
        await s.commit()
        assert out["status"] == "revision_requested" and out["next_job"] is not None
        job = (await s.execute(select(Job).where(Job.kind == JobKind.REVISE_FULL))).scalar_one()
        assert job.target_scene_id == sc1.id

    assert await worker.run_once(session_factory=db_factory) is True

    async with db_factory() as s:
        scenes = (await s.execute(
            select(Scene).where(Scene.scene_no == 1).order_by(Scene.version)
        )).scalars().all()
        assert len(scenes) == 2
        assert scenes[0].version == 1 and scenes[0].status == SceneStatus.SUPERSEDED
        assert scenes[1].version == 2 and scenes[1].status == SceneStatus.PENDING_REVIEW
        assert scenes[1].parent_scene_id == scenes[0].id
        assert "Revised" in (scenes[1].prose or "")


async def test_continuity_resolve_use_prose_corrects_ledger(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        crit = Critique(
            scene_id=sc.id, version=1, reviewer="continuity", severity="hard",
            payload={"character": "Marcus", "attribute": "level", "prose_value": "7", "ledger_value": "5"},
        )
        s.add(crit)
        await s.flush()
        out = await reviews.resolve_continuity(
            sc.id, ContinuityResolveIn(critique_id=crit.id, choice="use_prose"), s
        )
        await s.commit()
        assert out["resolved"] == "ledger_updated"
        assert (await s.execute(select(CharacterState))).scalar_one().stats_json["level"] == 7
        assert (await s.execute(select(Critique))).scalar_one_or_none() is None  # flag cleared


async def test_continuity_resolve_use_ledger_enqueues_and_clears(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        await _run(s, book)
        sc = await _scene(s, ch, 1)
        crit = Critique(
            scene_id=sc.id, version=1, reviewer="continuity", severity="hard",
            payload={"character": "Marcus", "attribute": "level", "prose_value": "9", "ledger_value": "5"},
        )
        s.add(crit)
        await s.flush()
        out = await reviews.resolve_continuity(
            sc.id, ContinuityResolveIn(critique_id=crit.id, choice="use_ledger"), s
        )
        await s.commit()
        assert out["resolved"] == "revision_enqueued" and out["job"] is not None
        assert (await s.get(Scene, sc.id)).status == SceneStatus.REVISION_REQUESTED
        job = (await s.execute(select(Job).where(Job.kind == JobKind.REVISE_FULL))).scalar_one()
        assert job.target_scene_id == sc.id
        assert (await s.execute(select(Critique))).scalar_one_or_none() is None  # flag cleared


def test_enqueue_parses_expected_state_changes():
    assert enqueue._parse_esc(None) is None
    assert enqueue._parse_esc('{"Marcus": {"level": "+1", "hp": 100}}') == {"Marcus": {"level": "+1", "hp": 100}}
    with pytest.raises(SystemExit):
        enqueue._parse_esc("not json")
    with pytest.raises(SystemExit):
        enqueue._parse_esc("[1, 2, 3]")  # must be an object, not an array


async def test_continuity_flag_fires_through_pipeline(db_factory, monkeypatch):
    # The model is mocked (no key here); this proves the wiring — a drafted scene whose prose asserts
    # a value the ledger contradicts produces a persisted HARD continuity critique reachable from the
    # inbox. The only mocked piece is the model itself.
    extraction = (
        '[{"character": "Marcus", "attribute": "level", "value": "7", '
        '"context_sentence": "The panel read LEVEL 7."}]'
    )

    async def fake_complete(**kwargs):
        return extraction, Usage(10, 10)

    async def fake_draft(self, prose, ctx):
        return "Marcus glanced at the status panel. LEVEL 7, it read, stark and undeniable."

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)

    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus
        run = await _run(s, book)
        # ledger already holds Marcus level 5, as if a prior scene had been approved
        s.add(CharacterState(book_id=book.id, character="Marcus", stats_json={"level": 5}))
        await _beat(s, ch, 1, chars=("Marcus",), text="Marcus opens his status panel.")
        s.add(Job(run_id=run.id, kind=JobKind.DRAFT, chapter_no=1, scene_no=1,
                  token_budget=20_000, status=JobStatus.QUEUED))
        await s.commit()

    assert await worker.run_once(session_factory=db_factory) is True  # drafts + reviews the scene

    async with db_factory() as s:
        sc = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalars().first()
        detail = await scene_detail(sc.id, s)
        hard = [c for c in detail.critiques if c.severity == "hard" and c.reviewer == "continuity"]
        assert len(hard) == 1  # the reviewer flagged it on its own
        assert hard[0].payload["attribute"] == "level"
        assert hard[0].payload["prose_value"] == "7"
        assert hard[0].payload["ledger_value"] == "5"


async def test_pipeline_renders_stat_blocks_into_prose_keeps_markers_in_agent_original(
    db_factory, monkeypatch
):
    # The drafter emits a ```stat``` block; the pipeline draws the box into Scene.prose and keeps the
    # raw marker form in Scene.agent_original. Only the drafter model is mocked (no key here).
    stat_prose = (
        "Marcus blinked the panel into focus.\n\n"
        "```stat\nPerception: 15\nReflexes: 11\n```\n\n"
        "He let the numbers settle."
    )

    async def fake_draft(self, prose, ctx):
        return stat_prose

    async def fake_complete(**kwargs):  # reviewers, if any fire, get a no-op empty result
        kwargs["budget"].charge(Usage(1, 1))
        return "[]", Usage(1, 1)

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    monkeypatch.setattr(llm, "complete", fake_complete)

    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus, no PovProfile -> no voice spec
        run = await _run(s, book)
        await _beat(s, ch, 1, chars=("Marcus",), text="Marcus checks his status panel.")
        s.add(Job(run_id=run.id, kind=JobKind.DRAFT, chapter_no=1, scene_no=1,
                  token_budget=20_000, status=JobStatus.QUEUED))
        await s.commit()

    assert await worker.run_once(session_factory=db_factory) is True

    async with db_factory() as s:
        sc = (await s.execute(select(Scene).where(Scene.scene_no == 1))).scalars().first()
        # prose carries the drawn, aligned box; the raw marker is gone from prose.
        assert "┌" in (sc.prose or "")
        assert "│ Perception  15 │" in (sc.prose or "")
        assert "│ Reflexes    11 │" in (sc.prose or "")
        assert "```stat" not in (sc.prose or "")
        assert "Marcus blinked the panel into focus." in (sc.prose or "")
        # agent_original keeps the editable marker form, never the box.
        assert "```stat" in (sc.agent_original or "")
        assert "┌" not in (sc.agent_original or "")
