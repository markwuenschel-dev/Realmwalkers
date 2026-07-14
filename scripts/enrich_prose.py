"""Run an enrichment lane over prose you already wrote — no DB, no packet, no drafter.

The enrichment passes take a plain string (`run_enrichment(prose, ctx, ...)`), so nothing about them
needs the contract-first apparatus: that machinery exists to tell the DRAFTER what to write from
scratch. Prose you injected is already written. This is the direct path in.

    uv run python scripts/enrich_prose.py book1/manuscript/scenes/SCENE-002_lobby-duel.md \
        --pov Mara --lane combat

Writes `<input>.<lane>.enriched.md` next to the source and leaves the original untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dominion.workers.budget import TokenBudget  # noqa: E402
from dominion.workers.context.types import SceneContext  # noqa: E402
from dominion.workers.specialists.base import PassError  # noqa: E402
from dominion.workers.specialists.enrich import combat_pass, dialogue_pass, sensory_pass  # noqa: E402

LANES = {"combat": combat_pass, "sensory": sensory_pass, "dialogue": dialogue_pass}


def _context(pov: str, *, beat_text: str | None, budget_tokens: int) -> SceneContext:
    """The minimum SceneContext an enrichment pass actually reads.

    `run_enrichment` touches only pov, beat_text, dialogue_rules, and budget. Everything else on
    SceneContext serves the drafter or the reviewers; the ids are structural and never read here.
    """
    return SceneContext(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov=pov,
        scene_no=1,
        tags=[],
        characters_present=[],
        beat_text=beat_text,
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=budget_tokens),
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="file containing the prose to enrich")
    ap.add_argument("--pov", required=True, help="POV character name (free text, per #216)")
    ap.add_argument("--lane", required=True, choices=sorted(LANES), help="which enrichment lane to run")
    ap.add_argument("--beat", default=None, help="optional beat text giving the pass its intent")
    ap.add_argument("--budget", type=int, default=200_000, help="token ceiling for the run")
    args = ap.parse_args()

    prose = args.source.read_text(encoding="utf-8")
    ctx = _context(args.pov, beat_text=args.beat, budget_tokens=args.budget)

    print(f"[enrich] lane={args.lane} pov={args.pov} source={args.source} ({len(prose)} chars)")
    try:
        out = await LANES[args.lane].run(prose, ctx)
    except PassError as exc:
        print(f"[enrich] FAILED (soft): {exc}", file=sys.stderr)
        return 1

    dest = args.source.with_suffix(f".{args.lane}.enriched.md")
    dest.write_text(out, encoding="utf-8")
    print(f"[enrich] OK  {len(prose)} -> {len(out)} chars  ({ctx.budget.used} tokens)")
    print(f"[enrich] wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
