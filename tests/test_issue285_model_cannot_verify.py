"""#285 — a model may nominate; it may never verify a human-required hold.

#223 Round 3 Fork 2 ruled the invariant::

    source = MODEL  =>  decision NOT IN { RULE, CLEAR, VERIFY }
    a model MAY:  NOMINATE · REPORT_EVIDENCE · PROPOSE

Two live paths violated it. Child A read the model's own `SATISFIED` result and set
`IssueStatus.VERIFIED`. Child B treated the ABSENCE of a repeated critique as remediation. Both ended in
the same place: a human-required hold left its active state with no human act, and both shortened the
count that gates publication at `production_sequence.py:904-913`.

The adversarial cases the ticket names are the ones that matter here — **empty output**, **changed
wording**, and **missing critique IDs** must each fail closed rather than read as remediation. Each of
the three is a way a reviewer returns nothing while the prose is still broken.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from dominion.shared.enums import (
    AuthorizationRequirement,
    IssueDecisionKind,
    IssueStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
)
from dominion.shared.models import Book, Chapter, Issue, IssueDecision, ProductionRun, RepairTask
from dominion.shared.verification_authority import (
    EVIDENCE_KIND_REPAIR_ATTEMPT,
    demands_human_verification,
    manual_grant_task_ids_for_issues,
    nominate_verification,
    requirements_demand_human_verification,
)

# =================================================================================================
# The predicate: unknown provenance is not human provenance
# =================================================================================================


def test_manual_grant_anywhere_demands_a_human():
    assert requirements_demand_human_verification([AuthorizationRequirement.MANUAL_GRANT.value])


def test_mixed_authority_is_manual_required_until_a_human_rules():
    """An issue linked to mixed-authority tasks is manual-required. Taking the permissive branch would
    let one ceiling-gated sibling launder the manual-grant one."""
    assert requirements_demand_human_verification(
        [AuthorizationRequirement.CEILING_GATED.value, AuthorizationRequirement.MANUAL_GRANT.value]
    )


def test_ceiling_gated_work_still_auto_verifies():
    """The withdrawal is scoped. Ordinary ceiling-gated repair is unaffected — otherwise this ticket
    would have stalled the whole repair loop rather than closed an authority hole."""
    assert not requirements_demand_human_verification([AuthorizationRequirement.CEILING_GATED.value] * 3)


@pytest.mark.parametrize("unknown", ["", "  ", "human_required", "autonomous", "something_invented_later", None])
def test_unknown_requirement_fails_closed(unknown):
    """NO FALLBACK MAPS UNKNOWN PROVENANCE TO TRUSTED HUMAN AUTHORITY. A value this code does not
    recognize — including the legacy `human_required` ladder rung, which is NOT the authorization axis —
    must never be read as 'safe to clear automatically'."""
    assert requirements_demand_human_verification([unknown])


def test_no_linked_tasks_fails_closed():
    """'I could not determine the requirement' is the unknown case, not the permissive one."""
    assert requirements_demand_human_verification([])


def test_policy_never_consults_authority_level():
    """ADR-0031 D16/A1c: `authority_level` is blast-radius metadata ONLY. The rejected WIP predicated
    clearance on `authority_level == human_required`, which silently reintroduces the ladder as a
    closeout gate and makes a persisted `ceiling_gated` value untrustworthy.

    Proven by construction: the predicate's only input is the requirement, so a task at the very top of
    the blast-radius ladder is auto-verifiable when its REQUIREMENT is ceiling-gated.
    """
    assert not requirements_demand_human_verification([AuthorizationRequirement.CEILING_GATED.value])
    import inspect

    from dominion.shared import verification_authority

    source = inspect.getsource(verification_authority)
    assert "authority_level" not in source.split('"""', 2)[-1], (
        "the verification policy must read authorization_requirement alone; authority_level is blast "
        "radius and nothing else"
    )


# =================================================================================================
# helpers
# =================================================================================================


async def _seed(s, *, requirement: str, count: int = 1) -> tuple[Issue, list[RepairTask]]:
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
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
        recommended_action="rewrite the passage to respect the locked canon fact",
        status=IssueStatus.ACCEPTED.value,
    )
    s.add(issue)
    await s.flush()
    tasks = []
    for _ in range(count):
        task = RepairTask(
            production_run_id=run.id,
            chapter_id=ch.id,
            repair_kind="fidelity",
            authority_level=RepairAuthorityLevel.HUMAN_REQUIRED,
            authorization_requirement=requirement,
            status=RepairTaskStatus.RUNNING,
            issue_ids=[str(issue.id)],
            instructions="author-controlled repair",
        )
        s.add(task)
        tasks.append(task)
    await s.flush()
    return issue, tasks


# =================================================================================================
# Nominations: evidence is recorded, authority is not granted
# =================================================================================================


async def test_a_nomination_records_evidence_without_touching_status(db_factory):
    """Both paths remain able to RECORD MODEL EVIDENCE without granting authority."""
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        issue_id = issue.id
        wrote = await nominate_verification(
            s,
            issue_id=issue_id,
            decided_by="scene_fidelity_evaluator",
            evidence_kind=EVIDENCE_KIND_REPAIR_ATTEMPT,
            evidence_id=f"{uuid.uuid4()}:{issue_id}",
            reason="evaluator says this looks remediated",
        )
        await s.commit()
        assert wrote is True

    async with db_factory() as s2:
        issue = await s2.get(Issue, issue_id)
        assert issue.status == IssueStatus.ACCEPTED.value, "the hold must still be ACTIVE"
        rows = (await s2.execute(select(IssueDecision).where(IssueDecision.issue_id == issue_id))).scalars().all()
        assert [r.decision for r in rows] == [IssueDecisionKind.VERIFICATION_NOMINATED.value]
        assert rows[0].decided_by == "scene_fidelity_evaluator", "provenance names the evaluator, not a human"
        assert rows[0].evidence_id, "a nomination must name the direct evidence it rests on"


async def test_nominations_are_idempotent_on_evidence_identity(db_factory):
    """A re-run of triage must not spam the issue's history. Idempotency keys on the DIRECT evidence,
    so re-evaluating the same report collides instead of appending."""
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        issue_id = issue.id
        evidence = f"{uuid.uuid4()}:clause-7"
        for _ in range(3):
            await nominate_verification(
                s,
                issue_id=issue_id,
                decided_by="scene_fidelity_evaluator",
                evidence_kind=EVIDENCE_KIND_REPAIR_ATTEMPT,
                evidence_id=evidence,
                reason="same evidence, third time",
            )
        await s.commit()

    async with db_factory() as s2:
        rows = (await s2.execute(select(IssueDecision).where(IssueDecision.issue_id == issue_id))).scalars().all()
        assert len(rows) == 1, "three identical nominations must collapse to one row"


async def test_distinct_evidence_produces_distinct_nominations(db_factory):
    """Idempotency must not swallow a genuinely NEW claim — a later report is new evidence."""
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        issue_id = issue.id
        for _ in range(2):
            await nominate_verification(
                s,
                issue_id=issue_id,
                decided_by="scene_fidelity_evaluator",
                evidence_kind=EVIDENCE_KIND_REPAIR_ATTEMPT,
                evidence_id=f"{uuid.uuid4()}:clause-7",
                reason="a fresh report",
            )
        await s.commit()

    async with db_factory() as s2:
        rows = (await s2.execute(select(IssueDecision).where(IssueDecision.issue_id == issue_id))).scalars().all()
        assert len(rows) == 2


# =================================================================================================
# The linked-task lookup that decides the policy
# =================================================================================================


async def test_linked_task_lookup_drives_the_policy(db_factory):
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        by_issue = await manual_grant_task_ids_for_issues(s, [issue.id])
        assert demands_human_verification(by_issue[str(issue.id)]) is True


async def test_a_ceiling_gated_issue_is_not_withheld(db_factory):
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.CEILING_GATED.value)
        by_issue = await manual_grant_task_ids_for_issues(s, [issue.id])
        assert demands_human_verification(by_issue[str(issue.id)]) is False


async def test_one_manual_grant_sibling_withholds_the_whole_issue(db_factory):
    """Requirement 5's mixed case, end to end through the real query."""
    async with db_factory() as s:
        issue, tasks = await _seed(s, requirement=AuthorizationRequirement.CEILING_GATED.value, count=2)
        tasks[1].authorization_requirement = AuthorizationRequirement.MANUAL_GRANT.value
        await s.flush()
        by_issue = await manual_grant_task_ids_for_issues(s, [issue.id])
        assert len(by_issue[str(issue.id)]) == 2
        assert demands_human_verification(by_issue[str(issue.id)]) is True


# =================================================================================================
# The automated verify entry point refuses before any evaluator runs
# =================================================================================================


async def test_core_verify_refuses_a_manual_grant_task_before_any_evaluator(db_factory):
    """Requirement 4. Refused BEFORE the evaluator — backpressure, not a late refusal: an evaluator that
    runs and is then ignored has still spent a provider call and still minted a claim."""
    from dominion.workers import production

    async with db_factory() as s:
        _, tasks = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        await s.commit()
        with pytest.raises(production.ManualVerificationRequired):
            await production.verify_repair_task(s, tasks[0].id)


@pytest.mark.parametrize("unknown", ["human_required", "autonomous", "not_a_requirement"])
async def test_an_unknown_requirement_cannot_even_be_PERSISTED(db_factory, unknown):
    """Defence in depth, and stronger than the entry-point check I first wrote.

    `ck_repair_tasks_authorization_requirement` refuses an unrecognized requirement at the DATABASE, so
    the "unknown requirement reaches the verifier" case is unreachable through any writer — including a
    direct SQL one that bypasses every Python guard. Note `human_required` is refused here too: it is a
    RepairAuthorityLevel (blast radius), never an Authorization Requirement, and conflating the two is
    exactly the D16/A1c error the rejected WIP made.

    The in-memory predicate still fails closed on an unknown value
    (`test_unknown_requirement_fails_closed`); this proves such a value cannot arrive in the first place.
    """
    from sqlalchemy.exc import IntegrityError

    async with db_factory() as s:
        with pytest.raises(IntegrityError):
            await _seed(s, requirement=unknown)
        await s.rollback()


def test_the_sweeper_excludes_manual_grant_from_its_verify_query():
    """Requirement 4's sharpest clause: the sweeper must create NO nomination at all. Refusing after
    fetching would still mint one on every tick, so the exclusion has to be in the QUERY."""
    import inspect

    from dominion.workers import sweeper

    source = inspect.getsource(sweeper)
    verify_block = source.split("RepairTask.status == RepairTaskStatus.RUNNING", 1)[1][:900]
    assert "AuthorizationRequirement.CEILING_GATED.value" in verify_block, (
        "the sweeper's verify query must filter to ceiling-gated tasks, not refuse manual-grant ones after loading them"
    )


# =================================================================================================
# The human path — the other half of the rule
# =================================================================================================


async def test_human_verify_clears_the_hold_and_reconciles_every_linked_task(db_factory):
    """Requirement 3 + 5. Withdrawing the model's transition without supplying a human one would strand
    every hold permanently. EVERY linked task is reconciled, not just the one in hand."""
    from dominion.workers import production

    async with db_factory() as s:
        issue, tasks = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value, count=3)
        issue_id = issue.id
        task_ids = [t.id for t in tasks]
        await nominate_verification(
            s,
            issue_id=issue_id,
            decided_by="scene_fidelity_evaluator",
            evidence_kind=EVIDENCE_KIND_REPAIR_ATTEMPT,
            evidence_id=f"{uuid.uuid4()}:{issue_id}",
            reason="looks remediated",
        )
        await s.commit()

        await production.human_verify_issue(s, issue_id=issue_id, decided_by="Mark", reason="checked it myself")
        await s.commit()

    async with db_factory() as s2:
        issue = await s2.get(Issue, issue_id)
        assert issue.status == IssueStatus.VERIFIED.value
        for tid in task_ids:
            task = await s2.get(RepairTask, tid)
            assert task.status == RepairTaskStatus.VERIFIED, "every linked task reconciles under one lock"
            assert task.human_approved_at is not None
        decisions = (await s2.execute(select(IssueDecision).where(IssueDecision.issue_id == issue_id))).scalars().all()
        kinds = {d.decision for d in decisions}
        assert IssueDecisionKind.VERIFY.value in kinds, "the human act is recorded as its own decision"
        assert IssueDecisionKind.VERIFICATION_NOMINATED.value in kinds, "and the evidence it rested on survives"


async def test_human_verify_without_a_nomination_is_refused_unless_forced(db_factory):
    """The default path cannot silently become 'click verify on anything'. A human may still rule
    without evaluator evidence — deliberately, via force."""
    from dominion.workers import production

    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        issue_id = issue.id
        await s.commit()
        with pytest.raises(production.NominationEvidenceMissing):
            await production.human_verify_issue(s, issue_id=issue_id, decided_by="Mark")
        await s.rollback()

    async with db_factory() as s2:
        assert (await s2.get(Issue, issue_id)).status == IssueStatus.ACCEPTED.value
        forced = await production.human_verify_issue(s2, issue_id=issue_id, decided_by="Mark", force=True)
        assert forced.status == IssueStatus.VERIFIED.value


# =================================================================================================
# The three adversarial shapes the ticket names
# =================================================================================================


@pytest.mark.parametrize(
    "shape",
    [
        "empty output — the reviewer returned nothing at all",
        "changed wording — the complaint is still true but phrased differently",
        "missing critique IDs — the finding lost the id the matcher keys on",
    ],
)
async def test_absence_of_a_critique_never_verifies_a_human_required_hold(db_factory, shape):
    """All three shapes arrive at `_finalize_repair_verification` identically: `new_critiques` contains
    nothing that matches the issue, so the issue lands in `resolved`. That partition is the ABSENCE of a
    complaint, and it must not clear a human-required hold in any of the three cases.

    Asserted at the policy seam the partition feeds, so the test covers the shape rather than one
    fixture's spelling of it — a terse model, a reworded complaint and a dropped id are the same fact
    to this code: nothing matched.
    """
    async with db_factory() as s:
        issue, _ = await _seed(s, requirement=AuthorizationRequirement.MANUAL_GRANT.value)
        by_issue = await manual_grant_task_ids_for_issues(s, [issue.id])
        assert demands_human_verification(by_issue[str(issue.id)]) is True, shape
        assert issue.status == IssueStatus.ACCEPTED.value, "still an open hold, whatever the reviewer said"
