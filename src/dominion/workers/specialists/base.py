"""Specialist protocol. Drafter writes the spine; enrichment passes deepen it (DESIGN §4-6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


class PassError(Exception):
    """An enrichment pass failed. Pipeline lands the partial spine + flags it; never blocks (DESIGN §4)."""


@runtime_checkable
class Specialist(Protocol):
    name: str

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        """Drafter ignores `prose` (writes from scratch). Enrichment passes transform it."""
        ...
