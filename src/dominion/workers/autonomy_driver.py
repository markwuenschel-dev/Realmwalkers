"""The AutonomyDriver: the loop that runs a chapter unattended, and the one thing that stops it.

There was no driver. Unattended progress was whatever the sweeper happened to pick up, with no single
place that asked "may anything proceed on this chapter?" before acting — so every gate the authority
work built (#277's open-questions predicate, #285's verification rule) had to be re-honoured
independently by each caller, and a caller that forgot simply proceeded.

THE ONE RULE THIS CLASS EXISTS TO ENFORCE::

    the driver acts if and ONLY if  status.may_proceed_unattended

which is true for exactly one of the four states, ``AUTONOMY_READY``. The driver has no second opinion,
no "probably fine" heuristic, and no override flag. If a human-required hold is open, the loop stops and
reports the hold's own next-human-action rather than inventing one — which is what keeps the authority
foundation *in front of* the loop instead of beside it.

WHY THE ACTION IS INJECTED. `ChapterAction` is a protocol, not a hard-coded call into the drafting stack.
Two reasons, both load-bearing: the safety property above is then testable without a provider or a
worker, and the driver cannot grow a private path to a mutation that bypasses its own gate — everything
it can do arrives through one seam the caller chose.

TERMINATION IS EXPLICIT. An unattended loop with no stop condition is not autonomy, it is a runaway. The
loop ends on the first of: a non-AUTONOMY_READY state, an action reporting nothing left to do, or
`max_ticks`. `max_ticks` is a hard backstop, and hitting it is reported as a distinct outcome rather
than silently looking like completion.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.workers.autonomy_funnel import ChapterFunnel, read_chapter_funnel
from dominion.workers.autonomy_reader import chapter_autonomy_status
from dominion.workers.autonomy_status import ChapterAutonomyState, ChapterAutonomyStatus

log = structlog.get_logger()

__all__ = ["AutonomyDriver", "ChapterAction", "DriverRun", "DriverStop", "DriverTick"]

#: Belt-and-braces ceiling on one unattended run. Deliberately small: a chapter that needs more than
#: this many machine actions without a human touching it is not converging, and continuing would burn
#: provider budget on a loop that has already failed to make progress.
DEFAULT_MAX_TICKS = 25


class ChapterAction(Protocol):
    """One unit of machine work on a chapter.

    Returns a short description of what it did, or None when there is nothing left to do — which is how
    the loop learns it has finished rather than by guessing from state it does not own.
    """

    async def __call__(self, session: AsyncSession, chapter_id: uuid.UUID) -> str | None: ...


class DriverStop(str):
    """Why the loop ended. A plain str subclass so it renders in logs and JSON without ceremony."""


STOP_BLOCKED = DriverStop("blocked")
#: The loop ended because the chapter reached REVIEW_READY. NOT a block: the machine finished and
#: handed off. Reporting this as "blocked" would tell an operator to go fix something when the only
#: thing left is the author reading their own draft.
STOP_REVIEW_READY = DriverStop("review_ready")
STOP_NOTHING_TO_DO = DriverStop("nothing_to_do")
STOP_MAX_TICKS = DriverStop("max_ticks")


@dataclass(frozen=True)
class DriverTick:
    """One pass of the loop. Records what the state was, whether the driver acted, and why not if not."""

    chapter_id: uuid.UUID
    state: ChapterAutonomyState
    acted: bool
    action: str | None
    reason: str
    next_human_action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": str(self.chapter_id),
            "state": self.state.value,
            "acted": self.acted,
            "action": self.action,
            "reason": self.reason,
            "next_human_action": self.next_human_action,
        }


@dataclass
class DriverRun:
    """The result of driving one chapter until it stopped."""

    chapter_id: uuid.UUID
    ticks: list[DriverTick] = field(default_factory=list)
    stopped_because: DriverStop = STOP_NOTHING_TO_DO
    final_status: ChapterAutonomyStatus | None = None
    funnel: ChapterFunnel | None = None

    @property
    def actions_taken(self) -> int:
        return sum(1 for tick in self.ticks if tick.acted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": str(self.chapter_id),
            "stopped_because": str(self.stopped_because),
            "actions_taken": self.actions_taken,
            "final_state": self.final_status.state.value if self.final_status else None,
            "final_reason": self.final_status.reason if self.final_status else None,
            "next_human_action": self.final_status.next_human_action if self.final_status else None,
            "ticks": [t.as_dict() for t in self.ticks],
            "funnel": self.funnel.as_dict() if self.funnel else None,
        }


class AutonomyDriver:
    """Owns the unattended chapter loop.

    `operational_probe` reports provider/infrastructure health, which is not a row in this database.
    It returns a failure string or None. Supplying it here — rather than having the driver guess — is
    what lets OPERATIONAL_BLOCKED be distinguished from an authoring hold, so an operator is told to fix
    a gateway instead of being sent to rule a question that would change nothing.
    """

    def __init__(
        self,
        *,
        action: ChapterAction,
        operational_probe: Callable[[], Awaitable[str | None]] | None = None,
        max_ticks: int = DEFAULT_MAX_TICKS,
    ) -> None:
        if max_ticks < 1:
            raise ValueError("max_ticks must be at least 1; a loop that cannot tick is not a driver")
        self._action = action
        self._probe = operational_probe
        self._max_ticks = max_ticks

    async def _status(self, session: AsyncSession, chapter_id: uuid.UUID) -> ChapterAutonomyStatus:
        failure = await self._probe() if self._probe is not None else None
        return await chapter_autonomy_status(session, chapter_id, operational_failure=failure)

    async def tick(self, session: AsyncSession, chapter_id: uuid.UUID) -> DriverTick:
        """One pass. Consults the status FIRST and acts only on AUTONOMY_READY.

        The order is the safety property: the gate is read before the action, from the same seam the API
        and the Desk read, so there is exactly one notion of whether this chapter may proceed.
        """
        status = await self._status(session, chapter_id)
        if not status.may_proceed_unattended:
            log.info(
                "autonomy.blocked",
                chapter_id=str(chapter_id),
                state=status.state.value,
                reason=status.reason,
            )
            return DriverTick(
                chapter_id=chapter_id,
                state=status.state,
                acted=False,
                action=None,
                reason=status.reason,
                next_human_action=status.next_human_action,
            )
        performed = await self._action(session, chapter_id)
        return DriverTick(
            chapter_id=chapter_id,
            state=status.state,
            acted=performed is not None,
            action=performed,
            reason=status.reason if performed is not None else "no machine work remains on this chapter",
        )

    async def run_chapter(self, session: AsyncSession, chapter_id: uuid.UUID) -> DriverRun:
        """Drive one chapter until it stops. Terminates on a block, on nothing-to-do, or at max_ticks."""
        run = DriverRun(chapter_id=chapter_id)
        for _ in range(self._max_ticks):
            tick = await self.tick(session, chapter_id)
            run.ticks.append(tick)
            if tick.state is not ChapterAutonomyState.AUTONOMY_READY:
                # REVIEW_READY is a HAND-OFF, not a block. Collapsing the two would send an operator to
                # diagnose a chapter whose only remaining step is the author reading it.
                run.stopped_because = (
                    STOP_REVIEW_READY if tick.state is ChapterAutonomyState.REVIEW_READY else STOP_BLOCKED
                )
                break
            if not tick.acted:
                run.stopped_because = STOP_NOTHING_TO_DO
                break
        else:
            # Exhausted the budget without reaching a natural stop. Reported distinctly: a run that hit
            # its ceiling has NOT finished, and letting it read as completion is how a stalled loop gets
            # mistaken for a converged one.
            run.stopped_because = STOP_MAX_TICKS
            log.warning("autonomy.max_ticks", chapter_id=str(chapter_id), max_ticks=self._max_ticks)

        run.final_status = await self._status(session, chapter_id)
        run.funnel = await read_chapter_funnel(session, chapter_id)
        log.info(
            "autonomy.run_finished",
            chapter_id=str(chapter_id),
            stopped_because=str(run.stopped_because),
            actions_taken=run.actions_taken,
            final_state=run.final_status.state.value,
            approved_scene_rate=round(run.funnel.approved_scene_rate, 4),
            interventions=run.funnel.interventions,
            revisions=run.funnel.revisions,
            provider_calls=run.funnel.provider_calls,
            failure_reasons=run.funnel.failure_reasons,
        )
        return run
