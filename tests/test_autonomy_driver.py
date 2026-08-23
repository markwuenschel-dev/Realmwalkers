"""The AutonomyDriver acts if and ONLY if the chapter is AUTONOMY_READY, and the funnel tells the truth.

The safety property is one line and everything else serves it: an unattended loop that can act while a
human-required hold is open walks straight past every gate #277 and #285 built. So these tests drive the
REAL driver against REAL rows and assert it stops — not that a predicate returns False somewhere.

The funnel tests are about honesty rather than arithmetic. A metric that flatters a run that did nothing
is worse than no metric, because it gets believed: an empty chapter must not report a 100% approved rate,
and a run with zero approved scenes must not report "0 interventions per approved scene" as though it
had achieved perfect autonomy.
"""

from __future__ import annotations

import uuid

import pytest

from dominion.shared.enums import (
    AuthorizationRequirement,
    IssueStatus,
    PacketStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
)
from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    Issue,
    ProductionRun,
    RepairTask,
    Scene,
)
from dominion.workers.autonomy_driver import (
    STOP_BLOCKED,
    STOP_MAX_TICKS,
    STOP_NOTHING_TO_DO,
    AutonomyDriver,
)
from dominion.workers.autonomy_funnel import ChapterFunnel, read_chapter_funnel
from dominion.workers.autonomy_status import ChapterAutonomyState
from dominion.workers.packet import open_questions as oq


class _Recorder:
    """A ChapterAction that records every call. `budget` actions, then nothing left to do."""

    def __init__(self, budget: int = 1) -> None:
        self.calls: list[uuid.UUID] = []
        self.budget = budget

    async def __call__(self, session, chapter_id: uuid.UUID) -> str | None:
        self.calls.append(chapter_id)
        if len(self.calls) > self.budget:
            return None
        return f"drafted something (call {len(self.calls)})"


async def _chapter(s, *, drafted: bool = True, approved: bool = False) -> tuple[Book, Chapter]:
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    if drafted:
        s.add(
            Scene(
                chapter_id=ch.id,
                scene_no=1,
                version=1,
                status="approved" if approved else "pending_review",
                word_count=8,
                prose="She turned toward the window, and the rain came down.",
                prose_source="agent",
            )
        )
        await s.flush()
    return book, ch


async def _hold(s, book: Book, ch: Chapter) -> Issue:
    """An open issue that needs an explicit human grant — the #285 shape."""
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, mode="full_chapter")
    s.add(run)
    await s.flush()
    issue = Issue(
        production_run_id=run.id,
        chapter_id=ch.id,
        artifact_type="scene_fidelity_report",
        artifact_id=uuid.uuid4(),
        validator="scene_fidelity",
        issue_kind="fidelity",
        severity="block",
        claim="the scene contradicts locked canon",
        recommended_action="rewrite the passage",
        status=IssueStatus.ACCEPTED.value,
    )
    s.add(issue)
    await s.flush()
    s.add(
        RepairTask(
            production_run_id=run.id,
            chapter_id=ch.id,
            repair_kind="fidelity",
            authority_level=RepairAuthorityLevel.HUMAN_REQUIRED,
            authorization_requirement=AuthorizationRequirement.MANUAL_GRANT.value,
            status=RepairTaskStatus.WAITING_FOR_HUMAN,
            issue_ids=[str(issue.id)],
            instructions="author-controlled repair",
        )
    )
    await s.flush()
    return issue


# =================================================================================================
# THE safety rule
# =================================================================================================


async def test_the_driver_refuses_to_act_while_a_human_grant_is_outstanding(db_factory):
    """The one that matters. #285 withdrew the model's power to VERIFY a hold; if the driver could act
    anyway, that withdrawal would be decorative."""
    async with db_factory() as s:
        book, ch = await _chapter(s, drafted=False)
        await _hold(s, book, ch)
        await s.commit()

        action = _Recorder(budget=99)
        run = await AutonomyDriver(action=action).run_chapter(s, ch.id)

        assert action.calls == [], "the action must never have been invoked"
        assert run.actions_taken == 0
        assert run.stopped_because == STOP_BLOCKED
        assert run.final_status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED
        assert "may never verify" in run.final_status.reason
        assert run.final_status.next_human_action


async def test_the_driver_refuses_to_act_while_open_questions_are_unresolved(db_factory):
    """#277's half of the foundation, enforced by the same single predicate."""
    async with db_factory() as s:
        book, ch = await _chapter(s, drafted=False)
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status=PacketStatus.APPROVED,
                confidence="green",
                body={"scene_seeds": []},
                open_questions=oq.normalize({"items": ["who hired the courier?"]}, mint=True),
            )
        )
        await s.commit()

        action = _Recorder(budget=99)
        run = await AutonomyDriver(action=action).run_chapter(s, ch.id)

        assert action.calls == []
        assert run.stopped_because == STOP_BLOCKED
        assert run.final_status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED
        assert "open question" in run.final_status.reason


async def test_an_operational_failure_stops_the_loop_and_says_authoring_will_not_help(db_factory):
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=False)
        await s.commit()

        async def down() -> str | None:
            return "LiteLLM gateway unreachable"

        action = _Recorder(budget=99)
        run = await AutonomyDriver(action=action, operational_probe=down).run_chapter(s, ch.id)

        assert action.calls == []
        assert run.final_status.state is ChapterAutonomyState.OPERATIONAL_BLOCKED
        assert "No authoring change will clear this" in run.final_status.next_human_action


async def test_the_driver_acts_when_the_chapter_is_autonomy_ready(db_factory):
    """The positive case — otherwise "never acts" would pass every test above."""
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=False)
        await s.commit()

        action = _Recorder(budget=2)
        run = await AutonomyDriver(action=action).run_chapter(s, ch.id)

        assert len(action.calls) >= 1, "an unblocked chapter must actually be worked"
        assert run.actions_taken == 2
        assert run.stopped_because == STOP_NOTHING_TO_DO


async def test_a_hold_appearing_mid_run_stops_the_loop_on_the_next_tick(db_factory):
    """The gate is re-read every tick, not once at the start. A hold raised while the loop is running
    must stop it — a driver that checked once and then ran free would be the same defect with extra
    steps."""
    async with db_factory() as s:
        book, ch = await _chapter(s, drafted=False)
        await s.commit()

        state: dict[str, bool] = {"raised": False}

        async def raise_a_hold_after_one_action(session, chapter_id):
            if not state["raised"]:
                state["raised"] = True
                await _hold(session, book, ch)
                return "did one thing, then a hold appeared"
            return "should never get here"

        run = await AutonomyDriver(action=raise_a_hold_after_one_action).run_chapter(s, ch.id)

        assert run.actions_taken == 1
        assert run.stopped_because == STOP_BLOCKED
        assert run.final_status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED


async def test_max_ticks_stops_a_non_converging_loop_and_says_so(db_factory):
    """An unattended loop with no stop condition is a runaway. Hitting the ceiling is reported as its
    own outcome — letting it read as completion is how a stalled loop is mistaken for a converged one."""
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=False)
        await s.commit()

        action = _Recorder(budget=10_000)  # never finishes on its own
        run = await AutonomyDriver(action=action, max_ticks=4).run_chapter(s, ch.id)

        assert run.stopped_because == STOP_MAX_TICKS
        assert run.actions_taken == 4
        assert len(action.calls) == 4


def test_a_driver_that_cannot_tick_is_rejected_at_construction():
    with pytest.raises(ValueError, match="at least 1"):
        AutonomyDriver(action=_Recorder(), max_ticks=0)


# =================================================================================================
# Funnel honesty
# =================================================================================================


def test_an_empty_chapter_reports_a_zero_rate_not_a_perfect_one():
    """'Nothing to approve' must never read as 'everything was approved'. That is the shape of a metric
    that flatters a run which did nothing."""
    funnel = ChapterFunnel(chapter_id=uuid.uuid4())
    assert funnel.approved_scene_rate == 0.0
    assert funnel.interventions_per_approved_scene is None, "a ratio over zero is undefined, not perfect"


def test_interventions_per_approved_scene_is_none_rather_than_zero_when_nothing_landed():
    funnel = ChapterFunnel(chapter_id=uuid.uuid4(), scenes_total=3, scenes_approved=0, interventions=7)
    assert funnel.approved_scene_rate == 0.0
    assert funnel.interventions_per_approved_scene is None
    assert funnel.as_dict()["interventions_per_approved_scene"] is None


def test_cost_is_reported_as_absent_rather_than_invented():
    """There is no price table in this repo. `cost_usd: None` says 'not measured here'; a number would
    be a confident figure nobody could reconcile against a provider invoice."""
    funnel = ChapterFunnel(chapter_id=uuid.uuid4(), provider_calls=12, provider_input_tokens=9000)
    assert funnel.cost_usd is None
    body = funnel.as_dict()
    assert "cost_usd" in body and body["cost_usd"] is None, "present-and-null, not silently absent"
    assert body["provider_calls"] == 12


async def test_the_funnel_counts_real_rows(db_factory):
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=True, approved=True)
        await s.commit()
        funnel = await read_chapter_funnel(s, ch.id)

    assert funnel.scenes_total == 1
    assert funnel.scenes_approved == 1
    assert funnel.approved_scene_rate == 1.0
    assert funnel.failure_reasons == {}


async def test_a_superseded_scene_version_is_not_counted_as_a_second_scene(db_factory):
    """Counting every row would inflate the denominator and understate the approved rate — the metric
    would get worse every time a scene was revised, which is precisely backwards."""
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=True, approved=False)
        s.add(
            Scene(
                chapter_id=ch.id,
                scene_no=1,
                version=2,
                status="approved",
                word_count=9,
                prose="She turned toward the window. The rain had not stopped.",
                prose_source="agent",
            )
        )
        await s.commit()
        funnel = await read_chapter_funnel(s, ch.id)

    assert funnel.scenes_total == 1, "two versions of one scene are one scene"
    assert funnel.scenes_approved == 1
    assert funnel.revisions >= 1, "but the revision itself IS counted"


async def test_a_run_records_its_funnel(db_factory):
    """Outcome 4 is only delivered if the driver actually emits these figures at the end of a run."""
    async with db_factory() as s:
        _book, ch = await _chapter(s, drafted=True, approved=True)
        await s.commit()
        run = await AutonomyDriver(action=_Recorder(budget=0)).run_chapter(s, ch.id)

    assert run.funnel is not None
    body = run.as_dict()["funnel"]
    for required in (
        "approved_scene_rate",
        "interventions",
        "revisions",
        "provider_calls",
        "cost_usd",
        "failure_reasons",
    ):
        assert required in body, f"the funnel must record {required}"
