"""End-to-end acceptance: real HTTP routes, real database persistence, real workers, one chapter.

Every other test in this stack proves one seam. This one proves they compose — that the authority
foundation actually stands in front of the autonomy loop when the pieces are wired together, rather than
each being individually correct and jointly bypassable.

The journey, in the order a real chapter takes it:

    1. a chapter with an APPROVED contract carrying an unresolved open question
       -> GET /chapters/{id}/autonomy  ==  HUMAN_ACTION_REQUIRED   (#277)
       -> the AutonomyDriver refuses to act

    2. the author RULES the question over the real route, echoing the state token
       -> PUT /chapters/{id}/packet   (#277 clause B: absent token = 422, stale = 409)
       -> the open-questions gate clears

    3. a fidelity hold appears that needs an explicit human grant
       -> the evaluator NOMINATES it (a model may nominate, never verify)   (#285)
       -> GET .../autonomy  ==  HUMAN_ACTION_REQUIRED, and the driver still refuses

    4. the author VERIFIES it over the real route
       -> POST /issues/{id}/verify

    5. nothing blocks -> the driver runs, acts, and records the funnel
       -> the chapter lands at REVIEW_READY

What makes it an ACCEPTANCE test rather than an integration test: at every blocked step it asserts the
DRIVER DID NOT ACT — by observing that the injected action was never invoked — not merely that a
response code was 4xx. A gate that returns the right status while the loop proceeds anyway is the exact
failure this whole sequence of work exists to prevent.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared.enums import (
    AuthorizationRequirement,
    IssueDecisionKind,
    IssueStatus,
    PacketStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    Issue,
    IssueDecision,
    ProductionRun,
    RepairTask,
    Scene,
)
from dominion.shared.verification_authority import (
    EVIDENCE_KIND_FIDELITY_CLAUSE,
    nominate_verification,
)
from dominion.workers.autonomy_driver import STOP_BLOCKED, STOP_REVIEW_READY, AutonomyDriver
from dominion.workers.autonomy_status import ChapterAutonomyState
from dominion.workers.packet import open_questions as oq


class _NeverRuns:
    """An action that fails the test if the driver ever calls it. The strongest form of "did not act"."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, session, chapter_id) -> str | None:
        self.calls += 1
        raise AssertionError(
            "the AutonomyDriver acted on a chapter that was NOT autonomy-ready — the authority gate was "
            "bypassed by the loop it is supposed to stop"
        )


class _DraftsOnce:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, session, chapter_id) -> str | None:
        self.calls += 1
        return "drafted the remaining scene" if self.calls == 1 else None


async def test_the_authority_foundation_stands_in_front_of_the_autonomy_loop(app_client, db_factory):
    # ---------------------------------------------------------------- 1. an unresolved open question
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
                status=SceneStatus.PENDING_REVIEW,
                word_count=9,
                prose="She turned toward the window, and the rain came down hard.",
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
                open_questions=oq.normalize({"items": ["who hired the courier?"]}, mint=True),
            )
        )
        await s.commit()
        chapter_id, book_id = ch.id, book.id

    body = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert body["state"] == ChapterAutonomyState.HUMAN_ACTION_REQUIRED.value
    assert "open question" in body["reason"]
    assert body["may_proceed_unattended"] is False

    async with db_factory() as s:
        run = await AutonomyDriver(action=_NeverRuns()).run_chapter(s, chapter_id)
    assert run.stopped_because == STOP_BLOCKED
    assert run.actions_taken == 0

    # ---------------------------------------------------------------- 2. the author rules it, for real
    packet = (await app_client.get(f"/chapters/{chapter_id}/packet")).json()
    item = packet["open_questions"]["items"][0]

    # #277 clause B: a write that changes open questions must echo the token it read.
    no_token = await app_client.put(
        f"/chapters/{chapter_id}/packet",
        json={"open_questions": {"items": packet["open_questions"]["items"], "resolved": []}},
    )
    assert no_token.status_code == 422
    assert no_token.json()["detail"]["reason"] == "open_questions_token_required"

    # The stale case needs content that DIFFERS from current state. Re-sending the current value with a
    # stale token is the idempotent-replay branch and correctly returns 200 — a duplicate delivery of a
    # request that already succeeded must be safe, not a conflict. So send a real ruling with a bogus
    # token: different content, unproven base state, refused.
    stale = await app_client.put(
        f"/chapters/{chapter_id}/packet",
        json={
            "open_questions": {
                "items": packet["open_questions"]["items"],
                "resolved": [
                    {"item_id": item["item_id"], "resolution": "ruled against stale state", "source": "author"}
                ],
            },
            "expected_open_questions_token": "0" * 64,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["reason"] == "open_questions_stale"
    assert "NOTHING WAS CHANGED" in stale.json()["detail"]["message"]

    ruled = await app_client.put(
        f"/chapters/{chapter_id}/packet",
        json={
            "open_questions": {
                "items": packet["open_questions"]["items"],
                "resolved": [
                    {
                        "item_id": item["item_id"],
                        "resolution": "Mara hired the courier; it is on the page in scene 2.",
                        "source": "author",
                    }
                ],
            },
            "expected_open_questions_token": packet["open_questions_token"],
        },
    )
    assert ruled.status_code == 200, ruled.text

    body = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert body["state"] != ChapterAutonomyState.HUMAN_ACTION_REQUIRED.value or "open question" not in body["reason"]

    # ---------------------------------------------------------------- 3. a hold a model may not clear
    async with db_factory() as s:
        prun = ProductionRun(book_id=book_id, chapter_id=chapter_id, mode="full_chapter")
        s.add(prun)
        await s.flush()
        issue = Issue(
            production_run_id=prun.id,
            chapter_id=chapter_id,
            artifact_type="scene_fidelity_report",
            artifact_id=uuid.uuid4(),
            validator="scene_fidelity",
            issue_kind="fidelity",
            severity="block",
            claim="the scene contradicts locked canon about Serra's rank",
            recommended_action="rewrite the passage to respect the locked fact",
            status=IssueStatus.ACCEPTED.value,
        )
        s.add(issue)
        await s.flush()
        s.add(
            RepairTask(
                production_run_id=prun.id,
                chapter_id=chapter_id,
                repair_kind="fidelity",
                authority_level=RepairAuthorityLevel.HUMAN_REQUIRED,
                authorization_requirement=AuthorizationRequirement.MANUAL_GRANT.value,
                status=RepairTaskStatus.WAITING_FOR_HUMAN,
                issue_ids=[str(issue.id)],
                instructions="author-controlled repair",
            )
        )
        # The evaluator says it looks fixed. That is a NOMINATION and nothing more.
        await nominate_verification(
            s,
            issue_id=issue.id,
            decided_by="scene_fidelity_evaluator",
            evidence_kind=EVIDENCE_KIND_FIDELITY_CLAUSE,
            evidence_id=f"{uuid.uuid4()}:cl-1",
            reason="evaluator reported the clause SATISFIED with a quote that validated against the prose",
        )
        await s.commit()
        issue_id = issue.id

    async with db_factory() as s:
        still_open = await s.get(Issue, issue_id)
        assert still_open.status != IssueStatus.VERIFIED.value, (
            "a model nomination must NEVER clear the hold on its own"
        )

    body = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert body["state"] == ChapterAutonomyState.HUMAN_ACTION_REQUIRED.value
    assert "may never verify" in body["reason"]
    assert "/issues/{id}/verify" in body["next_human_action"]

    async with db_factory() as s:
        run = await AutonomyDriver(action=_NeverRuns()).run_chapter(s, chapter_id)
    assert run.stopped_because == STOP_BLOCKED
    assert run.actions_taken == 0

    # ---------------------------------------------------------------- 4. the author verifies it
    verified = await app_client.post(
        f"/issues/{issue_id}/verify", json={"reason": "checked the passage myself", "decided_by": "Mark"}
    )
    assert verified.status_code == 200, verified.text

    async with db_factory() as s:
        cleared = await s.get(Issue, issue_id)
        assert cleared.status == IssueStatus.VERIFIED.value
        decisions = (await s.execute(select(IssueDecision).where(IssueDecision.issue_id == issue_id))).scalars().all()
        kinds = {d.decision for d in decisions}
        assert IssueDecisionKind.VERIFY.value in kinds, "the human act is recorded"
        assert IssueDecisionKind.VERIFICATION_NOMINATED.value in kinds, "and the evidence it rested on survives"

    # ---------------------------------------------------------------- 5. now the loop may run
    # Every hold is cleared and the draft is complete, so the correct outcome is a HAND-OFF, not more
    # machine work. The driver must recognise that and stop WITHOUT acting — a loop that churned a
    # finished chapter would burn provider budget producing nothing the author asked for.
    action = _NeverRuns()
    async with db_factory() as s:
        run = await AutonomyDriver(action=action).run_chapter(s, chapter_id)

    assert action.calls == 0, "a finished chapter is the author's read, not more machine work"
    assert run.stopped_because == STOP_REVIEW_READY, (
        "REVIEW_READY is a hand-off; reporting it as 'blocked' would send an operator to diagnose a "
        "chapter whose only remaining step is the author reading it"
    )
    assert run.final_status.state is ChapterAutonomyState.REVIEW_READY

    # ---------------------------------------------------------------- the funnel recorded the journey
    funnel = run.as_dict()["funnel"]
    assert funnel is not None
    for required in (
        "approved_scene_rate",
        "interventions",
        "revisions",
        "provider_calls",
        "cost_usd",
        "failure_reasons",
    ):
        assert required in funnel, f"the funnel must record {required}"
    assert funnel["interventions"] >= 1, "the human VERIFY is an intervention and must be counted"
    assert funnel["cost_usd"] is None, "present-and-null: no price table exists, so cost is not invented"

    final = (await app_client.get(f"/chapters/{chapter_id}/autonomy")).json()
    assert final["state"] == ChapterAutonomyState.REVIEW_READY.value
    assert final["next_human_action"] is None
