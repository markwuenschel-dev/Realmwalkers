"""Enrichment pass: punch up exchanges (DESIGN §5-6). Runs only when the beat carries the matching tag."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.specialists.base import PassError

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class DialoguePass:
    name = "dialogue"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        # Phase 3 will implement this. Until then, raise PassError (not NotImplementedError) so the
        # pipeline lands the drafted spine + an advisory flag instead of hard-failing the job.
        raise PassError("dialogue enrichment pass not implemented yet (Phase 3)")


dialogue_pass = DialoguePass()
