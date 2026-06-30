"""Enrichment pass: punch up exchanges (DESIGN §5-6). Runs only when the beat carries the matching tag."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.workers.specialists.enrich import run_enrichment

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_DIMENSION = (
    "Deepen the dialogue — voice and subtext. Make each speaker sound distinct and let what goes unsaid "
    "carry weight: sharpen rhythm, implication, and the friction between what is said and what is meant. "
    "Keep every line's intent and the information each exchange conveys; add no new plot or revelations."
)


class DialoguePass:
    name = "dialogue"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        # Dialogue rules are authoritative for how dialogue is written/formatted (see drafter._voice_system).
        return await run_enrichment(prose, ctx, name=self.name, dimension=_DIMENSION, use_dialogue_rules=True)


dialogue_pass = DialoguePass()
