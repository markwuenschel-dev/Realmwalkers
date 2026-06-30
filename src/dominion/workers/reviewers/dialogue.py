"""Dialogue review lane (DESIGN §6, OPEN-8). Advisory: flags flat voices and on-the-nose exchanges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.reviewers.base import Flag
from dominion.workers.reviewers.lane import lane_review

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_FOCUS = (
    "the dialogue — flat or interchangeable voices, on-the-nose lines that state what should be subtext, "
    "and exchanges that do not land or carry the weight the moment needs"
)


class DialogueReviewer:
    name = "dialogue"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        return await lane_review(scene_prose, ctx, name=self.name, focus=_FOCUS)


dialogue_reviewer = DialogueReviewer()
