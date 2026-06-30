"""The Oracle: read-authority over character_state (DESIGN §5). Deterministic; owns truth, never reasons.

Distinct from the continuity reviewer (which only REPORTS whether prose matches this truth). The Oracle
never decides who's right when prose and ledger disagree — the human resolves that (DESIGN §9).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import CharacterState


class Oracle:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def current(self, *, book_id: uuid.UUID, character: str) -> dict[str, Any]:
        """Latest known stats for a character. The truth other components query for hard numbers."""
        stmt = (
            select(CharacterState.stats_json)
            .where(CharacterState.book_id == book_id, CharacterState.character == character)
            .order_by(CharacterState.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return row or {}
