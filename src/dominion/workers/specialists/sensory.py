"""Enrichment pass: replace abstraction with concrete sensory detail (DESIGN §5-6). Tag-gated."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class SensoryPass:
    name = "sensory"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        raise NotImplementedError("Phase 3: SensoryPass enrichment pass.")


sensory_pass = SensoryPass()
