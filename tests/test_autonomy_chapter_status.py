"""The four chapter autonomy states must be authoritative, mutually intelligible, and self-explaining.

"Mutually intelligible" is the load-bearing word. Four labels that can overlap are four opinions; these
are ordered by a total precedence, so exactly one applies and two callers cannot disagree. And every
blocked state names a concrete reason AND the next human action — a chapter reported as "blocked" with
no stated next step is precisely the failure this projection replaced.

Before it, the whole answer was one inline boolean in the middle of `production_sequence`::

    ready_for_human = not open_issues and not missing_scene_nos and not qa_block

which could not tell an author who must read a draft from an operator whose provider is down.
"""

from __future__ import annotations

import uuid

import pytest

from dominion.workers.autonomy_status import (
    BLOCKED_STATES,
    ChapterAutonomyInputs,
    ChapterAutonomyState,
    ChapterAutonomyStatus,
    project_chapter_autonomy,
)

# =================================================================================================
# Each state is reachable, and says something an operator can act on
# =================================================================================================


def test_autonomy_ready_when_nothing_blocks():
    status = project_chapter_autonomy(ChapterAutonomyInputs(work_remaining=True))
    assert status.state is ChapterAutonomyState.AUTONOMY_READY
    assert status.may_proceed_unattended is True
    assert status.next_human_action is None


def test_review_ready_when_the_draft_is_complete_and_clean():
    status = project_chapter_autonomy(ChapterAutonomyInputs(draft_complete=True, work_remaining=False))
    assert status.state is ChapterAutonomyState.REVIEW_READY
    assert status.may_proceed_unattended is False, "a finished draft is the AUTHOR's turn, not the driver's"
    assert status.next_human_action is None, "reading your own draft is not a blocked state"


def test_human_action_required_for_unresolved_open_questions():
    status = project_chapter_autonomy(ChapterAutonomyInputs(unresolved_open_questions=3))
    assert status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED
    assert "3 open question" in status.reason
    assert "resolution and source" in status.next_human_action


def test_human_action_required_for_holds_awaiting_a_human_grant():
    """#285's half: a model may nominate evidence for these, never clear them."""
    status = project_chapter_autonomy(ChapterAutonomyInputs(holds_awaiting_human_verification=2))
    assert status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED
    assert "may never verify" in status.reason
    assert "/issues/{id}/verify" in status.next_human_action


def test_operational_blocked_names_the_dependency_and_says_authoring_will_not_help():
    status = project_chapter_autonomy(ChapterAutonomyInputs(operational_failure="LiteLLM gateway unreachable"))
    assert status.state is ChapterAutonomyState.OPERATIONAL_BLOCKED
    assert "LiteLLM gateway unreachable" in status.reason
    assert "No authoring change will clear this" in status.next_human_action


# =================================================================================================
# Mutual intelligibility: a total precedence, so exactly one state applies
# =================================================================================================


def test_operational_failure_outranks_every_authoring_hold():
    """A ruling cannot be acted on while the machine cannot run. Reporting the human hold first would
    send the author to do work that changes nothing."""
    status = project_chapter_autonomy(
        ChapterAutonomyInputs(
            operational_failure="provider 503",
            unresolved_open_questions=5,
            holds_awaiting_human_verification=4,
            open_issues=9,
            qa_blocked=True,
            missing_scene_nos=(2, 3),
        )
    )
    assert status.state is ChapterAutonomyState.OPERATIONAL_BLOCKED


def test_a_human_hold_outranks_review_ready():
    """A complete draft with an unresolved hold is NOT the author's read — it is their ruling. Calling
    it REVIEW_READY would invite an approval over an open authority question."""
    status = project_chapter_autonomy(
        ChapterAutonomyInputs(draft_complete=True, work_remaining=False, unresolved_open_questions=1)
    )
    assert status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED


def test_a_human_hold_outranks_autonomy_ready():
    """The sharpest one: an unattended driver must NEVER see AUTONOMY_READY while a human-required hold
    is open, or the whole authority foundation is bypassed by the loop that consumes this."""
    status = project_chapter_autonomy(ChapterAutonomyInputs(work_remaining=True, holds_awaiting_human_verification=1))
    assert status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED
    assert status.may_proceed_unattended is False


def test_open_issues_prevent_review_ready():
    status = project_chapter_autonomy(ChapterAutonomyInputs(draft_complete=True, work_remaining=False, open_issues=1))
    assert status.state is ChapterAutonomyState.HUMAN_ACTION_REQUIRED


def test_work_remaining_prevents_review_ready():
    """A draft that still has machine work is not finished, however complete it looks."""
    status = project_chapter_autonomy(ChapterAutonomyInputs(draft_complete=True, work_remaining=True))
    assert status.state is ChapterAutonomyState.AUTONOMY_READY


@pytest.mark.parametrize(
    "inputs",
    [
        ChapterAutonomyInputs(),
        ChapterAutonomyInputs(work_remaining=True),
        ChapterAutonomyInputs(draft_complete=True),
        ChapterAutonomyInputs(open_issues=1),
        ChapterAutonomyInputs(qa_blocked=True),
        ChapterAutonomyInputs(missing_scene_nos=(1,)),
        ChapterAutonomyInputs(unresolved_open_questions=1),
        ChapterAutonomyInputs(holds_awaiting_human_verification=1),
        ChapterAutonomyInputs(operational_failure="down"),
        ChapterAutonomyInputs(
            operational_failure="down",
            unresolved_open_questions=2,
            holds_awaiting_human_verification=2,
            open_issues=2,
            qa_blocked=True,
            missing_scene_nos=(4,),
            draft_complete=True,
            work_remaining=True,
        ),
    ],
)
def test_every_input_combination_yields_exactly_one_valid_state(inputs):
    """Totality. There is no input for which the projection has no answer, and the answer is always one
    of the four — never a tuple, never None, never a fallthrough."""
    status = project_chapter_autonomy(inputs)
    assert isinstance(status, ChapterAutonomyStatus)
    assert status.state in set(ChapterAutonomyState)


# =================================================================================================
# The self-explaining invariant, enforced structurally
# =================================================================================================


@pytest.mark.parametrize("state", sorted(BLOCKED_STATES))
def test_a_blocked_state_cannot_be_constructed_without_a_next_human_action(state):
    """Enforced in __post_init__, not by convention. 'Blocked, cause unknown' is unconstructible."""
    with pytest.raises(ValueError, match="next human action"):
        ChapterAutonomyStatus(state=state, reason="something went wrong", next_human_action=None)


@pytest.mark.parametrize("state", sorted(ChapterAutonomyState))
def test_no_state_can_be_constructed_without_a_reason(state):
    blocked = state in BLOCKED_STATES
    with pytest.raises(ValueError, match="concrete reason"):
        ChapterAutonomyStatus(state=state, reason="   ", next_human_action="do the thing" if blocked else None)


def test_a_proceeding_state_must_not_claim_a_next_human_action():
    """The inverse mistake: telling the author to act on a chapter that is proceeding fine trains them
    to ignore the field."""
    with pytest.raises(ValueError, match="must not claim"):
        ChapterAutonomyStatus(
            state=ChapterAutonomyState.AUTONOMY_READY, reason="fine", next_human_action="do something"
        )


@pytest.mark.parametrize(
    "inputs",
    [
        ChapterAutonomyInputs(operational_failure="gateway down"),
        ChapterAutonomyInputs(unresolved_open_questions=1),
        ChapterAutonomyInputs(holds_awaiting_human_verification=1),
        ChapterAutonomyInputs(qa_blocked=True),
        ChapterAutonomyInputs(missing_scene_nos=(7,)),
        ChapterAutonomyInputs(open_issues=1),
    ],
)
def test_every_blocked_projection_carries_a_concrete_reason_and_next_action(inputs):
    """The requirement in the owner's words: every blocked state exposes a concrete reason and next
    human action. Asserted over every path that can produce one, not a sample."""
    status = project_chapter_autonomy(inputs)
    assert status.blocked
    assert len(status.reason.strip()) > 20, "a reason must be concrete, not a category name"
    assert len(status.next_human_action.strip()) > 20
    assert status.state.value not in status.reason, "the reason must not just restate the state name"


def test_only_autonomy_ready_permits_unattended_action():
    """One predicate, so a driver cannot invent its own notion of 'probably fine'."""
    permitted = {
        state
        for state in ChapterAutonomyState
        if ChapterAutonomyStatus(
            state=state,
            reason="a sufficiently concrete reason for this state",
            next_human_action="do the next thing" if state in BLOCKED_STATES else None,
        ).may_proceed_unattended
    }
    assert permitted == {ChapterAutonomyState.AUTONOMY_READY}


# =================================================================================================
# REVIEW_READY as a RUNTIME state — over the real route, against real rows
# =================================================================================================


async def test_review_ready_is_a_real_runtime_state_over_the_api(app_client, db_factory):
    """The requirement is that REVIEW_READY EXISTS AT RUNTIME, not that a pure function can name it.

    A chapter with a complete drafted scene, no open issue and no unresolved question reports
    REVIEW_READY over HTTP, with a reason and without a next-action nag.
    """
    from dominion.shared.models import Book, Chapter, Scene

    async with db_factory() as s:
        book = Book(title="Dominion Realm")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        s.add(
            Scene(
                chapter_id=ch.id,
                scene_no=1,
                version=1,
                status="pending_review",
                word_count=8,
                prose="She turned toward the window, and the rain came down.",
                prose_source="agent",
            )
        )
        await s.commit()
        chapter_id = ch.id

    resp = await app_client.get(f"/chapters/{chapter_id}/autonomy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == ChapterAutonomyState.REVIEW_READY.value
    assert body["may_proceed_unattended"] is False
    assert body["next_human_action"] is None
    assert len(body["reason"]) > 20


async def test_an_open_human_hold_reports_human_action_required_over_the_api(app_client, db_factory):
    """And the state that matters most for autonomy safety: an open issue needing a human grant is
    reported as HUMAN_ACTION_REQUIRED with a concrete next step — never as autonomy_ready."""
    from dominion.shared.enums import (
        AuthorizationRequirement,
        IssueStatus,
        RepairAuthorityLevel,
        RepairTaskStatus,
    )
    from dominion.shared.models import Book, Chapter, Issue, ProductionRun, RepairTask, Scene

    async with db_factory() as s:
        book = Book(title="Dominion Realm")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        s.add(
            Scene(
                chapter_id=ch.id,
                scene_no=1,
                version=1,
                status="pending_review",
                word_count=8,
                prose="She turned toward the window.",
                prose_source="agent",
            )
        )
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
        await s.commit()
        chapter_id = ch.id

    body = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert body["state"] == ChapterAutonomyState.HUMAN_ACTION_REQUIRED.value
    assert body["may_proceed_unattended"] is False, "a driver must never be told it may proceed here"
    assert body["next_human_action"] and len(body["next_human_action"]) > 20
    # Assert the REASON, not just the state. An open issue alone also produces HUMAN_ACTION_REQUIRED,
    # so a state-only assertion passes even when the reader counts zero human-grant holds — which is
    # exactly what the mutation matrix caught. The reason is what proves the hold was seen.
    assert "may never verify" in body["reason"], "the hold must be reported as a human-grant hold"
    assert body["diagnostics"].get("holds_awaiting_human_verification") == 1


async def test_unresolved_open_questions_on_the_authority_packet_block_at_runtime(app_client, db_factory):
    """The reader must consult the APPROVED packet's open questions through #277's canonical gate.

    Read from the authority packet specifically, not the newest one: `_latest` semantics would return a
    proposed amendment while the approved predecessor still governs, and asking the amendment about open
    questions answers a different question.
    """
    from dominion.shared.enums import PacketStatus
    from dominion.shared.models import Book, Chapter, ChapterPacket, Scene
    from dominion.workers.packet import open_questions as oq

    async with db_factory() as s:
        book = Book(title="Dominion Realm")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        s.add(
            Scene(
                chapter_id=ch.id,
                scene_no=1,
                version=1,
                status="pending_review",
                word_count=6,
                prose="She turned toward the window.",
                prose_source="agent",
            )
        )
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status=PacketStatus.APPROVED,
                confidence="green",
                body={"scene_seeds": []},
                open_questions=oq.normalize({"items": ["who hired the courier?", "is Serra recognized?"]}, mint=True),
            )
        )
        await s.commit()
        chapter_id = ch.id

    body = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert body["state"] == ChapterAutonomyState.HUMAN_ACTION_REQUIRED.value
    assert "2 open question" in body["reason"]
    assert "resolution and source" in body["next_human_action"]
    assert body["diagnostics"].get("unresolved_open_questions") == 2
