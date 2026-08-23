"""The four authoritative chapter autonomy states, and why a chapter is in one of them.

A chapter's *lifecycle* (``ChapterStatus``: planned → beats → drafting → done) says where it is in the
pipeline. It does not say **whether anything can proceed right now, and if not, who has to act**. That
question was answered before this module by a single inline boolean::

    ready_for_human = not open_issues and not missing_scene_nos and not qa_block

— computed in the middle of `production_sequence`, carrying no reason, no next action, and no way to
distinguish "the author has to read this" from "a provider is down". An operator seeing `False` learned
that something was wrong and nothing about what to do.

THE FOUR STATES, IN PRECEDENCE ORDER. Exactly one applies. The order is what makes them *mutually
intelligible* rather than four overlapping labels — a chapter that is both provider-blocked and awaiting
a ruling is OPERATIONAL_BLOCKED, because the ruling cannot be acted on until the machine runs again.

1. ``OPERATIONAL_BLOCKED``    — infrastructure or provider state stops the machine. Not the author's
                                fault and not fixable by any authoring act.
2. ``HUMAN_ACTION_REQUIRED``  — a human-required hold. The machine is healthy and refuses to proceed
                                because proceeding would require authority it does not have.
3. ``REVIEW_READY``           — the draft is complete and correct as far as the machine can tell; the
                                author's read is the next step.
4. ``AUTONOMY_READY``         — nothing blocks; an unattended driver may take the next action.

EVERY BLOCKED STATE CARRIES A CONCRETE REASON AND A NEXT HUMAN ACTION. Enforced structurally in
``__post_init__``, not by convention: a blocked state with an empty reason or a missing next action is a
construction error, because "blocked, cause unknown" is the exact failure this module exists to end.

PURE PROJECTION. This module decides nothing about authority and mutates nothing. It reads facts that
other seams already own — the open-questions gate (#277), the verification-authority predicate (#285),
issue status, provider health — and states what they imply. Keeping it pure is what lets the driver, the
API, and the tests all consult ONE answer instead of three drifting re-derivations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ChapterAutonomyInputs",
    "ChapterAutonomyState",
    "ChapterAutonomyStatus",
    "project_chapter_autonomy",
]


class ChapterAutonomyState(StrEnum):
    """What is true of a chapter RIGHT NOW, on the can-anything-proceed axis.

    Deliberately NOT added to ``ChapterStatus``. That enum is the pipeline lifecycle; folding an
    authority/health axis into it would repeat the ADR-0031 D16 mistake of conflating two orthogonal
    axes on one ladder, where raising a value on one axis silently moves the other.
    """

    OPERATIONAL_BLOCKED = "operational_blocked"
    HUMAN_ACTION_REQUIRED = "human_action_required"
    REVIEW_READY = "review_ready"
    AUTONOMY_READY = "autonomy_ready"


#: The two states that mean "stopped". Both MUST carry a reason and a next human action.
BLOCKED_STATES = frozenset({ChapterAutonomyState.OPERATIONAL_BLOCKED, ChapterAutonomyState.HUMAN_ACTION_REQUIRED})


@dataclass(frozen=True)
class ChapterAutonomyStatus:
    """One chapter's autonomy state plus the human-facing account of it."""

    state: ChapterAutonomyState
    #: Why, in concrete terms an operator can act on. Never a bare category name.
    reason: str
    #: The single next thing a human should do. Required for a blocked state, None otherwise.
    next_human_action: str | None = None
    #: The raw facts the decision was made from, so a surprising verdict can be audited without
    #: re-deriving it. Never the basis for a second opinion — the state above is the answer.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(f"{self.state} requires a concrete reason; got {self.reason!r}")
        if self.state in BLOCKED_STATES and not (self.next_human_action or "").strip():
            raise ValueError(
                f"{self.state} is a blocked state and must name the next human action. A blocked chapter "
                "with no stated next step is the defect this projection exists to end."
            )
        if self.state not in BLOCKED_STATES and self.next_human_action is not None:
            raise ValueError(
                f"{self.state} is not blocked, so it must not claim a next human action — that would read "
                "as 'you must act' on a chapter that is proceeding."
            )

    @property
    def blocked(self) -> bool:
        return self.state in BLOCKED_STATES

    @property
    def may_proceed_unattended(self) -> bool:
        """The ONLY predicate an unattended driver may act on."""
        return self.state == ChapterAutonomyState.AUTONOMY_READY


@dataclass(frozen=True)
class ChapterAutonomyInputs:
    """The facts the projection reads. A typed struct rather than loose kwargs so a new fact cannot be
    added at one call site and silently forgotten at another."""

    #: Provider / infrastructure failure text, if the machine cannot currently run. Highest precedence.
    operational_failure: str | None = None
    #: Unresolved open questions on the chapter's authority packet (#277's predicate, already computed).
    unresolved_open_questions: int = 0
    #: Issues whose clearance needs an explicit human grant (#285's predicate, already computed).
    holds_awaiting_human_verification: int = 0
    #: Open issues of any kind — the readiness count that gates publication.
    open_issues: int = 0
    #: Scenes the chapter expects but does not have.
    missing_scene_nos: tuple[int, ...] = ()
    #: The chapter-level QA verdict blocked the draft.
    qa_blocked: bool = False
    #: The chapter has a complete draft awaiting the author's read.
    draft_complete: bool = False
    #: There is still machine work to do (scenes to draft, repairs to apply).
    work_remaining: bool = False


def project_chapter_autonomy(inputs: ChapterAutonomyInputs) -> ChapterAutonomyStatus:
    """The one place a chapter's autonomy state is decided.

    Precedence is total and ordered highest-severity-first, so the result is deterministic and two
    callers cannot disagree. Each branch names the concrete fact AND the next human action, because a
    state whose account is "blocked" teaches an operator nothing.
    """
    if inputs.operational_failure and inputs.operational_failure.strip():
        # FIRST, above every authoring concern: a ruling cannot be acted on while the machine cannot run,
        # so reporting a human hold here would send the author to do work that changes nothing.
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.OPERATIONAL_BLOCKED,
            reason=f"the machine cannot currently run: {inputs.operational_failure.strip()}",
            next_human_action=(
                "Restore the failing dependency (provider credentials, gateway, or database), then retry. "
                "No authoring change will clear this."
            ),
            diagnostics={"operational_failure": inputs.operational_failure.strip()},
        )

    if inputs.unresolved_open_questions:
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.HUMAN_ACTION_REQUIRED,
            reason=(
                f"{inputs.unresolved_open_questions} open question(s) on this chapter's contract are "
                "unresolved, so the contract cannot take authority"
            ),
            next_human_action=(
                "Rule each open question on the chapter packet — every ruling needs a non-empty "
                "resolution and source — then approve the contract."
            ),
            diagnostics={"unresolved_open_questions": inputs.unresolved_open_questions},
        )

    if inputs.holds_awaiting_human_verification:
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.HUMAN_ACTION_REQUIRED,
            reason=(
                f"{inputs.holds_awaiting_human_verification} hold(s) need an explicit human grant to "
                "clear. A model may nominate evidence for them; it may never verify them"
            ),
            next_human_action=(
                "Review each nominated hold and verify it yourself (POST /issues/{id}/verify), or send it "
                "back for another repair."
            ),
            diagnostics={"holds_awaiting_human_verification": inputs.holds_awaiting_human_verification},
        )

    if inputs.qa_blocked:
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.HUMAN_ACTION_REQUIRED,
            reason="the chapter-level QA verdict is BLOCK, so the draft is not publishable as it stands",
            next_human_action="Read the chapter QA report and either repair the flagged defects or overrule it.",
            diagnostics={"qa_blocked": True},
        )

    if inputs.missing_scene_nos:
        missing = ", ".join(str(n) for n in inputs.missing_scene_nos)
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.HUMAN_ACTION_REQUIRED,
            reason=f"the chapter is missing scene(s) {missing}, so the draft is incomplete",
            next_human_action=(
                f"Add or re-import scene(s) {missing} — or amend the contract if the chapter genuinely "
                "has fewer scenes than the sequence expects."
            ),
            diagnostics={"missing_scene_nos": list(inputs.missing_scene_nos)},
        )

    if inputs.open_issues:
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.HUMAN_ACTION_REQUIRED,
            reason=f"{inputs.open_issues} issue(s) are still open against this chapter",
            next_human_action="Work the open issues to a verdict — accept, reject, or repair each one.",
            diagnostics={"open_issues": inputs.open_issues},
        )

    if inputs.draft_complete and not inputs.work_remaining:
        return ChapterAutonomyStatus(
            state=ChapterAutonomyState.REVIEW_READY,
            reason=(
                "the draft is complete, no issue is open, no question is unresolved, and nothing is "
                "awaiting a human grant — the author's read is the next step"
            ),
            diagnostics={"draft_complete": True},
        )

    return ChapterAutonomyStatus(
        state=ChapterAutonomyState.AUTONOMY_READY,
        reason="nothing blocks: an unattended driver may take the next action on this chapter",
        diagnostics={"work_remaining": inputs.work_remaining, "draft_complete": inputs.draft_complete},
    )
