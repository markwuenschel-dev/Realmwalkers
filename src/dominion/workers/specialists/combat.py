"""Enrichment pass: sharpen fight choreography (DESIGN §5-6). Runs only when the beat carries the matching tag."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.specialists.enrich import run_enrichment

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_DIMENSION = (
    "Sharpen the fight choreography. Make every exchange spatially clear — who is where, what moves, "
    "what connects and what misses, the order of blows, how the space and bodies shift — and keep it "
    "consistent with the combatants' established stats, skills, and abilities. Preserve the beat's "
    "outcome (who prevails, who is hurt, what is won or lost); only make the action legible and physical."
)


class CombatPass:
    name = "combat"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        return await run_enrichment(prose, ctx, name=self.name, dimension=_DIMENSION)


combat_pass = CombatPass()
