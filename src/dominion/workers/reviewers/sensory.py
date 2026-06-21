"""Sensory review lane (DESIGN §6, OPEN-8). Advisory: flags abstraction where concrete sense detail is due."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.reviewers.base import Flag
from dominion.workers.reviewers.lane import lane_review

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_FOCUS = (
    "the concreteness of sensory grounding — passages that stay abstract, generic, or told where specific "
    "physical sense detail (sight, sound, smell, taste, touch) is called for"
)


class SensoryReviewer:
    name = "sensory"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        return await lane_review(scene_prose, ctx, name=self.name, focus=_FOCUS)


sensory_reviewer = SensoryReviewer()
