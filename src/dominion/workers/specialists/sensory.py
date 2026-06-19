"""Enrichment pass: replace abstraction with concrete sensory detail (DESIGN §5-6). Tag-gated."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.specialists.base import PassError

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class SensoryPass:
    name = "sensory"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        # Phase 3 will implement this. Until then, raise PassError (not NotImplementedError) so the
        # pipeline lands the drafted spine + an advisory flag instead of hard-failing the job.
        raise PassError("sensory enrichment pass not implemented yet (Phase 3)")


sensory_pass = SensoryPass()
