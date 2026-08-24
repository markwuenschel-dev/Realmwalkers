"""`dominion-autonomy` — the production caller the AutonomyDriver never had.

The driver, its four authoritative states, its funnel and its acceptance tests all landed on
2026-08-23. Nothing constructed it outside the test suite, so the unattended loop was proven correct
and never ran. This is the entrypoint that makes it real.

DRY RUN IS THE DEFAULT, and it is not timidity. Running the loop for real spends provider budget
without a human present, which is the one thing in this system that cannot be undone by editing a row.
The house already has the precedent: `dominion-seed --no-summaries` documents the single step that
needs a paid key. Here the polarity is inverted — the paid path is opt-in — because seeding imports
prose the author already wrote, while this WRITES prose nobody has read.

The two modes are deliberately different shapes, not the same loop with a flag:

  default   ONE tick, read-only. Reads the same gate the live loop reads, reports the state, the
            reason, the next human action, and what it would draft. Mints nothing, claims nothing,
            bills nothing. A dry run that looped would print the same line twenty-five times, because
            nothing it does changes the state it reads.

  --live    The real `run_chapter` loop, bounded by --max-ticks, ending on the first of: a
            non-AUTONOMY_READY state, an action reporting nothing left, or the ceiling.

Exit code is 0 for a clean stop and 1 for a stop that needs a human, so this can be scripted. That
differs from `dominion-worker`, which reports only through structlog — a loop that can end because a
human must act is worth a status a shell can branch on.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from dominion.shared import agent_ops
from dominion.shared.db import SessionFactory
from dominion.workers.autonomy_action import ChapterDraftAction, make_operational_probe, plan_chapter_work
from dominion.workers.autonomy_driver import STOP_MAX_TICKS, STOP_NOTHING_TO_DO, STOP_REVIEW_READY, AutonomyDriver
from dominion.workers.autonomy_status import ChapterAutonomyState

#: Stops that mean "the machine finished its part". Everything else wants a human and exits non-zero.
_CLEAN_STOPS = {STOP_NOTHING_TO_DO, STOP_REVIEW_READY}


async def _dry_run(chapter_id: uuid.UUID) -> int:
    async with SessionFactory() as session:
        await agent_ops.apply_model_overrides(session)
        probe = make_operational_probe(chapter_id)
        driver = AutonomyDriver(action=_refuse_to_act, operational_probe=probe, max_ticks=1)
        tick = await driver.tick(session, chapter_id)
        plan = await plan_chapter_work(session, chapter_id)

    print(f"chapter    {chapter_id}")
    print(f"state      {tick.state.value}")
    print(f"may run    {'yes' if tick.state is ChapterAutonomyState.AUTONOMY_READY else 'no'}")
    if tick.reason:
        print(f"reason     {tick.reason}")
    if tick.next_human_action:
        print(f"you must   {tick.next_human_action}")
    print(f"plan       {plan.describe()}")
    print()
    print("DRY RUN — nothing was queued, claimed, or billed. Re-run with --live to act.")
    return 0 if tick.state is ChapterAutonomyState.AUTONOMY_READY else 1


async def _refuse_to_act(session: object, chapter_id: uuid.UUID) -> str | None:
    """The dry run's action. It is never reached — the driver only calls the action on
    AUTONOMY_READY, and reaching it would mean the gate let unpaid work through on a read-only path,
    which is worth crashing over rather than silently performing."""
    raise AssertionError(f"the dry run must never act on {chapter_id}; the gate let an action through")


async def _live_run(chapter_id: uuid.UUID, *, max_ticks: int) -> int:
    async with SessionFactory() as session:
        await agent_ops.apply_model_overrides(session)
        driver = AutonomyDriver(
            action=ChapterDraftAction(),
            operational_probe=make_operational_probe(chapter_id),
            max_ticks=max_ticks,
        )
        run = await driver.run_chapter(session, chapter_id)

    final = run.final_status
    funnel = run.funnel
    print(f"chapter    {chapter_id}")
    print(f"stopped    {run.stopped_because}")
    print(f"ticks      {len(run.ticks)} of {max_ticks}")
    for i, tick in enumerate(run.ticks, 1):
        did = tick.action or ("acted" if tick.acted else "—")
        print(f"  {i:>3}. [{tick.state.value}] {did}")
    print(f"final      {final.state.value}")
    if final.reason:
        print(f"reason     {final.reason}")
    if final.next_human_action:
        print(f"you must   {final.next_human_action}")
    print(
        f"funnel     {funnel.scenes_approved}/{funnel.scenes_total} scenes approved · "
        f"{funnel.interventions} interventions · {funnel.revisions} revisions · "
        f"{funnel.provider_calls} provider calls "
        f"({funnel.provider_input_tokens} in / {funnel.provider_output_tokens} out)"
    )
    if funnel.failure_reasons:
        print(f"failures   {funnel.failure_reasons}")
    if run.stopped_because == STOP_MAX_TICKS:
        print()
        print("HIT THE TICK CEILING. This is NOT convergence — the loop was still finding work when it")
        print("stopped. Re-run to continue, or investigate why the chapter is not settling.")
    return 0 if run.stopped_because in _CLEAN_STOPS else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one chapter's unattended autonomy loop. Reports what it would do unless --live."
    )
    parser.add_argument("--chapter", required=True, help="chapter UUID")
    parser.add_argument(
        "--live",
        action="store_true",
        help="ACTUALLY run the loop. Spends provider budget with no human present. Off by default.",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=25,
        help="ceiling on machine actions in one run (default 25). Ignored without --live.",
    )
    args = parser.parse_args()
    try:
        chapter_id = uuid.UUID(args.chapter)
    except ValueError:
        parser.error(f"--chapter must be a UUID, got {args.chapter!r}")
    if args.live and args.max_ticks < 1:
        parser.error("--max-ticks must be at least 1; a loop that cannot tick is not a driver")
    code = asyncio.run(_live_run(chapter_id, max_ticks=args.max_ticks) if args.live else _dry_run(chapter_id))
    sys.exit(code)
