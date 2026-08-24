"""The real work the AutonomyDriver performs, and the probe that tells it when not to.

`AutonomyDriver` has always taken its action as an injected protocol so the safety property could be
tested without a provider and so the driver could not grow a private path to a mutation that bypasses
its own gate. That was the right shape and it left one thing undone: nothing in the tree implemented
the protocol, so the unattended loop existed and never ran. This module is the missing half.

THREE THINGS HAD TO BE FIXED TO MAKE THE LOOP HONEST, and each is a defect in its own right:

1. **The executor was global.** `worker.run_once` claims the globally oldest job. A per-chapter loop
   calling it would spend its tick drafting a DIFFERENT chapter and then truthfully report "a job
   ran" — which an operator reads as progress on the chapter they asked about. `claim_one_job` and
   `recover_stale_jobs` now take `chapter_id`, so the scope of the claim matches the scope of the
   report.

2. **"Blocked" and "done" were the same value.** `queue_draft_jobs_for_missing_sequence_scenes`
   returns `[]` for a finished chapter AND for a missing sequence, a refused structural gate, a
   missing approved ScenePacket and a parked run. The driver maps a `None` action to
   `STOP_NOTHING_TO_DO`, which reads as convergence — so a blocked chapter would be reported as
   finished. The fix is not in the action: a block is not "no work", it is a REASON, and the driver
   already has a channel for reasons. It goes in the probe.

3. **The kill switches were not honoured.** `background_work.queue_paused` and the sweeper's
   `autonomy_enabled` setting gate every other autonomous path in this system; the driver consulted
   neither. Worse than merely ignoring them: a paused queue turns the drain into a silent no-op while
   the action keeps minting jobs each tick, so the loop would spin to `max_ticks` and bill nothing
   while reporting nothing wrong. Both are now operational blocks with their own sentence.

WHY THE PROBE CARRIES ALL OF IT. `ChapterAction` returns `str | None` and has no way to say "stop, and
here is why". `operational_probe` returns exactly that — a failure string or None — and
`chapter_autonomy_status` turns it into `OPERATIONAL_BLOCKED` with the reason attached, which is the
state whose whole purpose is "an operator must fix infrastructure, not rule a question". Routing
blocks through the probe means the driver stops for a stated cause instead of claiming it converged.

THE PROBE MUST STAY FREE. It is called on every tick and once more at run end — up to 26 times per
`run_chapter` at the default ceiling. A live gateway ping there would bill 26 completions to answer a
question the database can already answer, so every check here is a row read.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared.db import SessionFactory
from dominion.shared.models import ProductionRun
from dominion.workers import background_work, draft_readiness, production, worker

log = structlog.get_logger()

__all__ = ["ChapterDraftAction", "PlannedWork", "make_operational_probe", "plan_chapter_work"]


async def _latest_production_run(session: AsyncSession, chapter_id: uuid.UUID) -> ProductionRun | None:
    """The run the chapter is currently being produced under, newest first.

    Deliberately does not filter on status: a parked or blocked run is still THE run, and refusing to
    look at it would turn a stated block into a silent "no work to do" — defect 2 in the module
    docstring, reintroduced one layer down.
    """
    return (
        await session.execute(
            select(ProductionRun)
            .where(ProductionRun.chapter_id == chapter_id)
            .order_by(ProductionRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def make_operational_probe(
    chapter_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> Callable[[], Awaitable[str | None]]:
    """Build the driver's `operational_probe` for one chapter. Every check is a row read.

    Order matters: the cheapest and most operator-actionable causes come first, because the driver
    reports only the first failure and the operator should be told "the queue is paused" before
    "this chapter has no approved sequence".
    """

    async def probe() -> str | None:
        async with session_factory() as session:
            if await background_work.load_queue_paused(session):
                return (
                    "the job queue is PAUSED (the persisted human pause switch). Nothing will draft "
                    "until it is resumed, and an unattended loop would mint jobs that never run."
                )
            readiness = await draft_readiness.compute_draft_readiness(session, chapter_id)
            if getattr(readiness, "provider_rate_limited", False):
                return (
                    "the provider is rate-limited for this chapter (a scene packet or run is parked on "
                    "a rate limit). Drafting now would re-park it; wait for the limit to clear."
                )
            if not readiness.can_draft:
                return (
                    f"this chapter cannot draft: {readiness.disabled_reason or 'no reason recorded'}. "
                    "This is a STOP with a stated cause, not a converged chapter."
                )
        return None

    return probe


class PlannedWork:
    """What the next unit of work would be, decided without minting anything.

    A dry run has to answer "what would you do" without doing it, and the selector that normally
    answers that question (`queue_draft_jobs_for_missing_sequence_scenes`) answers it by WRITING a
    job row. So the dry path reads readiness instead, which is derived from the same rows and is
    documented as fully deterministic — no model decides any of it.
    """

    def __init__(self, *, scene_nos: list[int], reason: str | None) -> None:
        self.scene_nos = scene_nos
        self.reason = reason

    @property
    def has_work(self) -> bool:
        return bool(self.scene_nos)

    def describe(self) -> str:
        if self.reason:
            return f"would NOT draft: {self.reason}"
        if not self.scene_nos:
            return "nothing left to draft on this chapter"
        return f"would draft scene {self.scene_nos[0]} (remaining: {', '.join(str(n) for n in self.scene_nos)})"


async def plan_chapter_work(session: AsyncSession, chapter_id: uuid.UUID) -> PlannedWork:
    """Read-only. Never mints a job, never calls a provider."""
    readiness = await draft_readiness.compute_draft_readiness(session, chapter_id)
    if not readiness.can_draft:
        return PlannedWork(scene_nos=[], reason=readiness.disabled_reason or "drafting is disabled")
    return PlannedWork(scene_nos=list(readiness.missing_scene_drafts or []), reason=None)


class ChapterDraftAction:
    """One unit of unattended drafting on one chapter. THE thing that spends money.

    The two halves live in different transactions and that is not an accident to be tidied away:
    the selector writes a `Job` row on the caller's session and commits nothing, while the executor
    (`worker.run_once`) opens its OWN session and owns its own commits. So the action must commit
    between them, or the executor cannot see the job it is meant to claim.

    After the drain it expires the driver's session. The driver reads `chapter_autonomy_status` from
    that session on the next tick, and a snapshot taken before the executor's separate transaction
    committed would not show the new prose — the loop would re-queue the same scene and spin to the
    tick ceiling. That refresh is the difference between a loop that converges and one that looks
    like it is working.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self._session_factory = session_factory

    async def __call__(self, session: AsyncSession, chapter_id: uuid.UUID) -> str | None:
        run = await _latest_production_run(session, chapter_id)
        if run is None:
            return None  # no production run: nothing this action knows how to advance
        job_ids = await production.queue_draft_jobs_for_missing_sequence_scenes(session, run)
        if not job_ids:
            return None
        # The selector only flushed. Commit, or the executor's separate session cannot claim it.
        await session.commit()
        ran = await worker.run_once(self._session_factory, chapter_id=chapter_id)
        # The executor committed on a session this one has never seen. Drop our snapshot so the
        # driver's next status read sees the prose that was just written.
        session.expire_all()
        if not ran:
            return None
        log.info("autonomy.action.drafted", chapter_id=str(chapter_id), job_id=str(job_ids[0]))
        return f"drafted the scene for job {job_ids[0]}"
