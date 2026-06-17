"""Reviewer protocol. Reviewers ADVISE; they never mutate prose or block the inbox (DESIGN §2, §9)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dominion.shared.enums import Severity

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


@dataclass
class Flag:
    """An advisory finding. Persisted as a Critique row; HARD numeric ones feed the continuity panel."""
    reviewer: str
    severity: Severity
    note: str
    payload: dict[str, Any] | None = None


@runtime_checkable
class Reviewer(Protocol):
    name: str

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        ...
