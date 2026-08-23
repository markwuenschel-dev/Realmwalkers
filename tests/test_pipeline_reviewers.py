"""The advisory reviewers run concurrently (perf): a scene's reviewers fan out in parallel rather
than one-after-another. We prove it deterministically with probes that detect simultaneous in-flight
calls, and confirm a reviewer's BudgetExceeded still downgrades the scene to a partial DRAFT.
DB-backed; the drafter + reviewers are faked (no network)."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from dominion.shared.enums import (
    BeatStatus,
    DraftStage,
    GateMode,
    JobKind,
    JobStatus,
    RunStatus,
    SceneStatus,
    Severity,
)
from dominion.shared.models import Beat, Book, Chapter, Critique, DraftAttempt, Job, Run
from dominion.workers import pipeline
from dominion.workers.budget import BudgetExceeded
from dominion.workers.reviewers.base import Flag
from dominion.workers.specialists import drafter as drafter_mod
from tests.conftest import seed_scene_packet


async def _setup_draft_job(s):
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    run = Run(
        book_id=book.id,
        scope_json={"chapter": 1},
        gate_mode=GateMode.PAUSE_EACH,
        token_budget=40_000,
        status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    beat = Beat(
        chapter_id=ch.id,
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        status=BeatStatus.APPROVED,
        beat_text="Marcus presses on.",
    )
    s.add(beat)
    await s.flush()
    await seed_scene_packet(s, chapter=ch, beat=beat)
    job = Job(
        run_id=run.id,
        book_id=book.id,
        kind=JobKind.DRAFT,
        chapter_no=1,
        scene_no=1,
        token_budget=40_000,
        status=JobStatus.QUEUED,
    )
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

        # one reviewer blew the budget -> quarantined DRAFT + a block budget flag; the spine survives
        assert scene.status == SceneStatus.DRAFT
        assert "A short spine of prose." in (scene.prose or "")
        crits = (await s.execute(select(Critique).where(Critique.scene_id == scene.id))).scalars().all()
        assert any(c.reviewer == "budget" and c.severity == "block" for c in crits)


async def test_non_budget_reviewer_error_lands_a_flag_not_a_failure(db_factory, monkeypatch):
    """An advisory reviewer that crashes must never fail the job or discard the drafted spine — a
    raise would propagate to run_once, whose rollback nukes the good prose. It lands a WARN flag
    (same as a failed enrichment pass) and the scene still enters the inbox for review."""

    async def fake_draft(self, prose, ctx):
        return "A short spine of prose."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)

    class _Crash:
        name = "continuity"

        async def review(self, prose, ctx):
            raise RuntimeError("reviewer bug")

    class _Ok:
        name = "pacing"

        async def review(self, prose, ctx):
            return [Flag(reviewer="pacing", severity="info", note="fine")]

    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [_Crash(), _Ok()])

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        scene = await pipeline.generate_one_scene(s, job)  # no raise — the crash is absorbed
        await s.commit()

        # the spine survived and the scene is reviewable, not lost to a rollback
        assert scene.status == SceneStatus.PENDING_REVIEW
        assert "A short spine of prose." in (scene.prose or "")
        crits = (await s.execute(select(Critique).where(Critique.scene_id == scene.id))).scalars().all()
        # the crash became an advisory WARN naming the reviewer; the healthy reviewer's flag is kept too
        crash_flag = next(c for c in crits if c.reviewer == "continuity")
        assert crash_flag.severity == Severity.WARN and "reviewer bug" in crash_flag.note
        assert any(c.reviewer == "pacing" for c in crits)


class _CitingReviewer:
    """Returns one supported citation, one fabricated one, and one finding that cites nothing.

    The supported quote is sliced from the prose it is actually handed, so the test holds regardless
    of what enrichment/length/rendering did to the drafted spine before review.
    """

    name = "voice"

    def __init__(self) -> None:
        self.seen_prose = ""

    async def review(self, prose: str, ctx: object) -> list[Flag]:
        self.seen_prose = prose
        real = prose[:14]
        return [
            Flag(reviewer=self.name, severity=Severity.INFO, note="grounded", payload={"quote": real}),
            Flag(
                reviewer=self.name,
                severity=Severity.WARN,
                note="fabricated",
                payload={"quote": "a dragon landed on the balcony and roared"},
            ),
            Flag(reviewer=self.name, severity=Severity.INFO, note="no citation offered", payload=None),
        ]


async def test_unsupported_citation_is_dropped_at_the_persistence_funnel(db_factory, monkeypatch):
    """A reviewer that quotes prose the scene does not contain has fabricated its evidence; that
    finding must never reach the author's panel. A finding that quotes nothing is a legitimate shape
    and must survive — the guard judges citations that were MADE, it does not require one."""

    async def fake_draft(self, prose, ctx):
        return "She turned toward the window, and the rain came down hard."

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    reviewer = _CitingReviewer()
    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [reviewer])

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        scene = await pipeline.generate_one_scene(s, job)
        await s.commit()

        crits = (
            (await s.execute(select(Critique).where(Critique.scene_id == scene.id).order_by(Critique.id)))
            .scalars()
            .all()
        )
        notes = [c.note for c in crits]
        assert "fabricated" not in notes, "a finding citing prose the scene never contained was persisted"
        assert "grounded" in notes, "a correctly-cited finding must survive the guard"
        assert "no citation offered" in notes, "a finding that cites nothing is legitimate, not unsupported"

        # The counter has a persisted reader, so the fabrication rate is answerable from the database.
        attempt = (
            (
                await s.execute(
                    select(DraftAttempt).where(
                        DraftAttempt.scene_id == scene.id,
                        DraftAttempt.stage == DraftStage.FINAL_RENDERED,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert attempt is not None
        assert (attempt.metadata_json or {}).get("unsupported_citations_dropped") == 1


async def test_guard_records_zero_when_every_citation_holds(db_factory, monkeypatch):
    """Recorded unconditionally, including 0 — an absent key means the guard did not run, which is a
    different fact from 'nothing was dropped', and a rate needs the denominator."""

    async def fake_draft(self, prose, ctx):
        return "She turned toward the window, and the rain came down hard."

    class _Grounded:
        name = "voice"

        async def review(self, prose: str, ctx: object) -> list[Flag]:
            return [Flag(reviewer=self.name, severity=Severity.INFO, note="ok", payload={"quote": prose[:10]})]

    monkeypatch.setattr(drafter_mod.Drafter, "run", fake_draft)
    monkeypatch.setattr(pipeline, "reviewers_for", lambda tags: [_Grounded()])

    async with db_factory() as s:
        job = await _setup_draft_job(s)
        await s.commit()
        scene = await pipeline.generate_one_scene(s, job)
        await s.commit()

        attempt = (
            (
                await s.execute(
                    select(DraftAttempt).where(
                        DraftAttempt.scene_id == scene.id,
                        DraftAttempt.stage == DraftStage.FINAL_RENDERED,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert attempt is not None
        assert (attempt.metadata_json or {}).get("unsupported_citations_dropped") == 0
