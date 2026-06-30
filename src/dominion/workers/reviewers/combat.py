"""Combat review lane (DESIGN §6, OPEN-8). Advisory: flags muddy or inconsistent fight choreography."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.reviewers.base import Flag
from dominion.workers.reviewers.lane import lane_review

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_FOCUS = (
    "the fight choreography — confusing spatial geography (who is where, what moves), blows that do not "
    "connect or land logically, and action that contradicts the combatants' established stats or abilities"
)


class CombatReviewer:
    name = "combat"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        return await lane_review(scene_prose, ctx, name=self.name, focus=_FOCUS)


combat_reviewer = CombatReviewer()
