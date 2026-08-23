"""Who may clear a human-required hold, and what a model is allowed to say instead (#285).

#223 Round 3 Fork 2 ruled the invariant this module exists to enforce::

    source = MODEL  =>  decision NOT IN { RULE, CLEAR, VERIFY }

    a model MAY:  NOMINATE · REPORT_EVIDENCE · PROPOSE

Two live production paths violated it, and both ended in the same place — a human-required hold leaving
its active state with no human act:

* ``production_fidelity._verify_satisfied_clauses`` set ``IssueStatus.VERIFIED`` from
  ``ev.result == SATISFIED``, which is copied verbatim from the adapter's model output
  (``scene_fidelity/evaluator.py:183``). ``evidence_valid`` beside it IS deterministic, but it only
  proves the quote the model chose really occurs at the offsets it named — not that the quote satisfies
  the clause. A model returning "satisfied" plus any exact substring of the prose closed the hold.
* ``production_repair`` treated the ABSENCE of a repeated critique as remediation
  (``must_change_ok = True  # addressed via critique disappearance``). A reviewer that returned nothing —
  terse model, shifted wording, changed critique id, soft-failed call — was indistinguishable from a
  genuinely repaired issue. Verification by silence.

WHAT THIS MODULE DOES NOT DO. It does not withdraw the model's ability to say something. Nomination and
evidence reporting are preserved in full; only the *verify transition* is withdrawn. The claim is
persisted as an append-only ``IssueDecision`` so the human sees exactly what the evaluator found and
why — it simply no longer clears anything by itself.

THE POLICY INPUT IS ``authorization_requirement`` ALONE (ADR-0031 D16/A1c). ``authority_level`` is
blast-radius metadata and nothing else; predicating clearance on it would reintroduce the ladder as a
closeout gate and make a persisted ``ceiling_gated`` value untrustworthy.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import AuthorizationRequirement, IssueDecisionKind
from dominion.shared.models import IssueDecision, RepairTask

if TYPE_CHECKING:  # pragma: no cover - typing only
    import uuid

log = structlog.get_logger()

__all__ = [
    "EVIDENCE_KIND_FIDELITY_CLAUSE",
    "EVIDENCE_KIND_REPAIR_ATTEMPT",
    "demands_human_verification",
    "manual_grant_task_ids_for_issues",
    "nominate_verification",
    "requirements_demand_human_verification",
]

#: Evidence-identity discriminators. A nomination names the DIRECT evidence its claim rests on, so a
#: re-evaluation of the same thing collides on the uniqueness key instead of spamming the issue history.
EVIDENCE_KIND_FIDELITY_CLAUSE = "fidelity_clause_evaluation"
EVIDENCE_KIND_REPAIR_ATTEMPT = "repair_attempt"


def requirements_demand_human_verification(requirements: Iterable[str | None]) -> bool:
    """Does clearing this work need an explicit human act?

    Reads the Authorization Requirement ALONE. Three fail-closed rules, each deliberate:

    * ``MANUAL_GRANT`` anywhere among the linked tasks -> human required. An issue linked to
      MIXED-authority tasks is manual-required until a human rules it; taking the permissive branch
      would let one ceiling-gated sibling launder the manual-grant one.
    * an UNRECOGNIZED requirement -> human required. Unknown provenance is not human provenance, and a
      value this code does not understand must never be mapped onto trusted authority.
    * NO linked tasks at all -> human required. "I could not determine the requirement" is the unknown
      case, not the permissive one.
    """
    saw_any = False
    for requirement in requirements:
        saw_any = True
        if requirement != AuthorizationRequirement.CEILING_GATED.value:
            return True
    return not saw_any


async def manual_grant_task_ids_for_issues(
    session: AsyncSession, issue_ids: Iterable[uuid.UUID]
) -> dict[str, list[RepairTask]]:
    """`str(issue_id) -> [RepairTask]` for every task linking any of these issues.

    ONE query for the whole set. The rejected WIP did this per issue, which is an N+1 over a table the
    triage path walks for every scene of every chapter.
    """
    wanted = {str(i) for i in issue_ids}
    if not wanted:
        return {}
    # JSONB containment (`@>`), not the `?|` overlap operator: `?|` takes a text[] on its right-hand
    # side, and binding a Python list through SQLAlchemy sends JSONB — which is an UndefinedFunctionError
    # at runtime, not a type error at import. One OR per wanted id keeps it a single round trip.
    rows = (
        (
            await session.execute(
                select(RepairTask).where(or_(*(RepairTask.issue_ids.contains([i]) for i in sorted(wanted))))
            )
        )
        .scalars()
        .all()
    )
    by_issue: dict[str, list[RepairTask]] = {issue_id: [] for issue_id in wanted}
    for task in rows:
        for linked in task.issue_ids or []:
            if str(linked) in by_issue:
                by_issue[str(linked)].append(task)
    return by_issue


def demands_human_verification(tasks: Iterable[RepairTask]) -> bool:
    """`requirements_demand_human_verification` over a set of linked tasks."""
    return requirements_demand_human_verification(task.authorization_requirement for task in tasks)


async def nominate_verification(
    session: AsyncSession,
    *,
    issue_id: uuid.UUID,
    decided_by: str,
    evidence_kind: str,
    evidence_id: str,
    reason: str,
) -> bool:
    """Record a model/evaluator claim that an issue LOOKS remediated. Returns True if a row was written.

    This is the whole of what a model is permitted to do here. The issue's status is NOT touched: the
    hold stays active, it keeps counting toward the readiness gate, and a human still has to verify it.

    IDEMPOTENT. Pre-checked, with the partial unique index as the real backstop — a concurrent duplicate
    raises IntegrityError inside a SAVEPOINT and is swallowed, so a re-run of triage neither spams the
    history nor fails the surrounding transaction. Nothing is read from the ORM inside the `except`
    (`tests/test_sweeper_greenlet_guard.py` bans that: the savepoint rollback expires every flushed
    attribute, and a post-rollback read becomes a sync lazy-load on the async session).
    """
    existing = (
        await session.execute(
            select(IssueDecision.id).where(
                IssueDecision.issue_id == issue_id,
                IssueDecision.decision == IssueDecisionKind.VERIFICATION_NOMINATED.value,
                IssueDecision.evidence_id == evidence_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    try:
        async with session.begin_nested():
            session.add(
                IssueDecision(
                    issue_id=issue_id,
                    decided_by=decided_by,
                    decision=IssueDecisionKind.VERIFICATION_NOMINATED.value,
                    reason=reason,
                    evidence_kind=evidence_kind,
                    evidence_id=evidence_id,
                )
            )
    except IntegrityError:
        # Lost a race to an identical nomination. The row that won carries the same evidence, so there is
        # nothing to add and nothing to repair.
        log.info(
            "verification.nomination_duplicate",
            issue_id=str(issue_id),
            evidence_kind=evidence_kind,
            evidence_id=evidence_id,
        )
        return False
    return True
