"""Review-only reviewer (DESIGN §6). Advisory; Phase 3."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.reviewers.base import Flag

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class PacingReviewer:
    name = "pacing"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        raise NotImplementedError("Phase 3: PacingReviewer (advisory).")


pacing_reviewer = PacingReviewer()
