"""The one Authorization Requirement decision (ADR-0031 D16, A1c).

**Authorization** answers *what must be true before this unit of repair work may execute*. It is
ORTHOGONAL to `authority_level`, which states **blast radius** only. Before A1c the two were conflated —
`human_required` was a rung on the blast-radius ladder that a raised sweeper ceiling could silently
negate (ADR-0031 B-3) — and the gate itself was three booleans OR'd together
(`human_approved or task.human_approved_at is not None or autonomous`), which made "the caller is
automated" *itself* a grant.

This module replaces that with two explicit facts:

1. **The requirement** the work carries, persisted on the task
   (`AuthorizationRequirement.CEILING_GATED` | `MANUAL_GRANT`).
2. **The grant** the caller can supply — an explicit human grant, or a *declared ceiling* that covers the
   task's blast radius. `autonomous` is provenance, not a grant: an automated caller authorizes work only
   by declaring a ceiling that covers it.

Every apply path (the manual `/apply` route, `/approve-apply`, the unattended drain, and the sweeper)
routes through `authorize_repair`. Nothing re-derives the decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from dominion.shared.enums import (
    AUTO_APPROVAL_CEILINGS,
    AuthorizationRequirement,
    RepairAuthorityLevel,
    is_manual_grant,
)

#: Blast-radius ladder. The ceiling is the highest rung a declaring caller may authorize.
_AUTHORITY_RANK: dict[RepairAuthorityLevel, int] = {level: i for i, level in enumerate(RepairAuthorityLevel)}

#: The ceiling a caller that declares none authorizes up to. This is NOT a new policy: it is the ceiling
#: that `RepairTask.requires_human_approval` encoded implicitly before A1c — the stored boolean was True
#: for exactly CROSS_SCENE and above, which is what made the unattended drain skip them and a plain
#: manual `/apply` park them. Naming it turns an invisible constant into a declared one; the authorized
#: set is unchanged.
DEFAULT_AUTHORIZATION_CEILING: str = RepairAuthorityLevel.SCENE_STRUCTURAL.value

#: Refusal reasons `authorize_repair` can return. Stable strings — they are persisted in run events.
REFUSAL_MANUAL_GRANT_REQUIRED = "manual_grant_required"
REFUSAL_ABOVE_CEILING = "above_ceiling"
REFUSAL_UNKNOWN_REQUIREMENT = "unknown_requirement"

#: Grant kinds. `HUMAN_GRANT` is a grant made now; `PRIOR_HUMAN_GRANT` is one already stamped on the task
#: (one human approval covers the task's whole repair loop, not a single attempt); `CEILING` is an
#: automated caller's declared ceiling covering the task's blast radius.
GRANT_HUMAN = "human_grant"
GRANT_PRIOR_HUMAN = "prior_human_grant"
GRANT_CEILING = "ceiling"


def rank(level: str | RepairAuthorityLevel) -> int:
    """Blast-radius rank of `level`; an unknown level ranks beyond every ceiling (never auto-authorized)."""
    try:
        return _AUTHORITY_RANK[RepairAuthorityLevel(level)]
    except ValueError:
        return 999


def within_ceiling(level: str, ceiling: str) -> bool:
    """True iff blast radius `level` is covered by a declared `ceiling`. Fails closed on every axis: a
    manual-grant blast radius (`human_required`) is never covered regardless of ceiling, and an unknown or
    invalid ceiling *or* level covers NOTHING (an unknown ceiling must never fail open)."""
    if is_manual_grant(level):
        return False
    if ceiling not in AUTO_APPROVAL_CEILINGS or level not in AUTO_APPROVAL_CEILINGS:
        return False
    return rank(level) <= rank(ceiling)


def requirement_for_authority(authority_level: str | RepairAuthorityLevel) -> str:
    """The DEFAULT requirement a freshly minted task carries, derived from its blast radius. This is the
    mint-time default only — the requirement is a durable column and callers may set it independently
    (that independence is the point of the axis: manual-grant work exists at every blast radius)."""
    if is_manual_grant(authority_level):
        return AuthorizationRequirement.MANUAL_GRANT.value
    return AuthorizationRequirement.CEILING_GATED.value


def requires_explicit_authorization(authority_level: str, authorization_requirement: str) -> bool:
    """The UI/wire projection formerly stored as `RepairTask.requires_human_approval`: does this task need
    something MORE than a default-ceiling automated caller — i.e. will the unattended drain skip it and a
    plain `/apply` park it? Derived, never stored (A1c: no boolean stands in for the requirement).

    Keep in lockstep with `requires_explicit_authorization_clause()`, the SQL form of this same rule;
    `tests/test_authorization_axis.py` pins that the two agree over every (level, requirement) pair.
    """
    if authorization_requirement != AuthorizationRequirement.CEILING_GATED.value:
        return True  # manual-grant (or anything unrecognized) always needs an explicit grant
    return not within_ceiling(authority_level, DEFAULT_AUTHORIZATION_CEILING)


def requires_explicit_authorization_clause():
    """SQL form of `requires_explicit_authorization` for `.where(...)` filters over `repair_tasks`.

    A task is claimable by a default-ceiling automated caller iff it is CEILING_GATED **and** its
    blast radius is within `DEFAULT_AUTHORIZATION_CEILING` — the same set the stored
    `requires_human_approval = false` boolean selected before A1c."""
    from dominion.shared.models import RepairTask  # local: models imports enums, not the reverse

    covered = sorted(level for level in AUTO_APPROVAL_CEILINGS if within_ceiling(level, DEFAULT_AUTHORIZATION_CEILING))
    return ~(
        (RepairTask.authorization_requirement == AuthorizationRequirement.CEILING_GATED.value)
        & (RepairTask.authority_level.in_(covered))
    )


@dataclass(frozen=True)
class AuthorizationDecision:
    """The result of the one authorization decision. `grant` names WHAT authorized the work (empty when
    refused); `refusal` names why it was refused (empty when authorized). Both are stable strings that
    land in the run event payload, so an operator can see which fact carried an execution."""

    authorized: bool
    grant: str = ""
    refusal: str = ""

    @property
    def human_message(self) -> str:
        """Operator-facing reason for a refusal (the run-event message)."""
        if self.refusal == REFUSAL_MANUAL_GRANT_REQUIRED:
            return "Manual-grant repair needs an explicit human grant, at any ceiling — use Approve & apply."
        if self.refusal == REFUSAL_ABOVE_CEILING:
            return "Repair task is above the calling authorization ceiling — use Approve & apply."
        return "Repair task carries an unrecognized authorization requirement; refusing to execute it."


def authorize_repair(
    *,
    authorization_requirement: str,
    authority_level: str,
    ceiling: str,
    human_approved: bool,
    prior_human_grant: bool,
) -> AuthorizationDecision:
    """Decide whether one repair task may execute for one caller. The ONLY authorization decision.

    * A **human grant** — made now (`human_approved`) or already stamped (`prior_human_grant`) — satisfies
      every requirement.
    * A **declared ceiling** satisfies `CEILING_GATED` work whose blast radius it covers, and never
      satisfies `MANUAL_GRANT` work.
    * Anything else is refused. Being an autonomous caller is not, by itself, an input here.
    """
    if human_approved:
        return AuthorizationDecision(True, grant=GRANT_HUMAN)
    if prior_human_grant:
        return AuthorizationDecision(True, grant=GRANT_PRIOR_HUMAN)
    if authorization_requirement == AuthorizationRequirement.MANUAL_GRANT.value:
        return AuthorizationDecision(False, refusal=REFUSAL_MANUAL_GRANT_REQUIRED)
    if authorization_requirement != AuthorizationRequirement.CEILING_GATED.value:
        return AuthorizationDecision(False, refusal=REFUSAL_UNKNOWN_REQUIREMENT)
    if within_ceiling(authority_level, ceiling):
        return AuthorizationDecision(True, grant=GRANT_CEILING)
    return AuthorizationDecision(False, refusal=REFUSAL_ABOVE_CEILING)
