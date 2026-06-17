"""Enrichment pass: punch up exchanges (DESIGN §5-6). Runs only when the beat carries the matching tag."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class DialoguePass:
    name = "dialogue"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        raise NotImplementedError("Phase 3: DialoguePass enrichment pass.")


dialogue_pass = DialoguePass()
