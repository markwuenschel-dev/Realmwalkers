"""Scheduling-loop tests for the autonomous sweeper.

`test_sweeper.py` covers a single TICK (`_sweep_one_run`); the interval-driven loop that DRIVES those
ticks — `run_forever` — was untested. These drive that loop on a fake clock (no wall-clock wait, no DB)
through its injectable collaborators, pinning the scheduling contract:

  * it ticks once per iteration and sleeps the configured interval between ticks;
  * it honors the stop signal (loops a bounded number of times, not forever);
  * a tick that RAISES is logged and the loop keeps going rather than dying (the resilience guarantee
    that keeps the always-on sweeper alive across transient DB blips);
  * a CancelledError propagates, so lifespan shutdown still stops the loop.

All fakes — the loop never opens a DB session, so this module is intentionally DB-free.
"""

from __future__ import annotations

import asyncio

import pytest

from dominion.workers import sweeper


async def test_run_forever_ticks_and_sleeps_configured_interval():
    ticks = 0
    sleeps: list[float] = []

    async def fake_tick() -> None:
        nonlocal ticks
        ticks += 1

    async def fake_interval() -> int:
        return 42

    async def fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    stop_calls = 0

    def should_stop() -> bool:
        nonlocal stop_calls
        stop_calls += 1
        return stop_calls >= 3  # run exactly 3 iterations, then exit cleanly

    await sweeper.run_forever(
        tick=fake_tick,
        read_interval=fake_interval,
        sleep=fake_sleep,
        should_stop=should_stop,
    )

    assert ticks == 3  # one tick per iteration
    # The stop signal fires on the 3rd iteration BEFORE that iteration's sleep, so we sleep twice, each
    # for the configured interval read live from `read_interval`.
    assert sleeps == [42, 42]


async def test_run_forever_survives_a_failing_tick():
    seen: list[str] = []
    slept: list[float] = []

    async def flaky_tick() -> None:
        seen.append("tick")
        if len(seen) == 1:
            raise RuntimeError("boom in tick")  # first tick blows up

    async def fake_interval() -> int:
        return 5

    async def fake_sleep(secs: float) -> None:
        slept.append(secs)

    n = 0

    def should_stop() -> bool:
        nonlocal n
        n += 1
        return n >= 2

    # A raising tick must NOT propagate out of the loop.
    await sweeper.run_forever(
        tick=flaky_tick,
        read_interval=fake_interval,
        sleep=fake_sleep,
        should_stop=should_stop,
    )

    assert seen == ["tick", "tick"]  # loop kept going and ran a SECOND tick after the first raised
    # On the failing iteration `read_interval` is skipped, so the interval falls back to the module
    # default (120); the surviving iteration reads the real 5.
    assert slept == [120]


async def test_run_forever_reraises_cancellation():
    async def cancelling_tick() -> None:
        raise asyncio.CancelledError

    async def fake_interval() -> int:
        return 1

    async def fake_sleep(secs: float) -> None:  # pragma: no cover - should never be reached
        raise AssertionError("cancellation must stop the loop before it sleeps")

    # CancelledError is how lifespan shutdown stops the loop — it must propagate, not be swallowed.
    with pytest.raises(asyncio.CancelledError):
        await sweeper.run_forever(
            tick=cancelling_tick,
            read_interval=fake_interval,
            sleep=fake_sleep,
        )
