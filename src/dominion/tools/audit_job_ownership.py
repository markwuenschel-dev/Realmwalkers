"""Audit / repair job->book ownership integrity (ADR 0027).

  dominion-audit             # read-only classification report (inspect) — the mandatory preflight
  dominion-audit --apply     # backfill (chapter->run, reject conflicts) + quarantine + constraint promotion

Shares the exact `reconcile_job_ownership` the boot migration runs, so the CLI is a faithful mirror of
what boot does — never a second, drifting implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from dominion.shared.db import engine
from dominion.shared.job_integrity import inspect_job_ownership, reconcile_job_ownership


async def _run(apply: bool) -> None:
    if apply:
        async with engine.begin() as conn:  # transaction: reconcile writes + promotes
            report = await reconcile_job_ownership(conn)
    else:
        async with engine.connect() as conn:  # read-only
            report = await inspect_job_ownership(conn)
    await engine.dispose()
    print(json.dumps(asdict(report), indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit/repair job->book ownership integrity (ADR 0027)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform backfill/quarantine/constraint promotion (default: read-only inspect)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.apply))


if __name__ == "__main__":
    main()
