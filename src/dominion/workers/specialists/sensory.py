"""Enrichment pass: replace abstraction with concrete sensory detail (DESIGN §5-6). Tag-gated."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.specialists.enrich import run_enrichment

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_DIMENSION = (
    "Replace abstraction with concrete, grounded sensory detail. Where the prose tells or generalizes a "
    "perception, render it through specific things the POV actually sees, hears, smells, tastes, or "
    "feels in this place. Ground the scene in physical specifics — add no new events, characters, or lore."
)


class SensoryPass:
    name = "sensory"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        return await run_enrichment(prose, ctx, name=self.name, dimension=_DIMENSION)


sensory_pass = SensoryPass()
