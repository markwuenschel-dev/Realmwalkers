"""Stat ledger: commit a beat's declared deltas on approval; the Oracle's state advances (DESIGN §1, §4).

Deterministic and idempotent-per-scene. One CharacterState row per (book, character) — updated in
place — so the Oracle's `current()` (which reads the single row) is unambiguous.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import Beat, Chapter, CharacterState, Scene


def _apply(current: Any, val: Any) -> Any:
    """Relative '+N'/'-N' on numbers; list values append (set-union); everything else sets."""
    if isinstance(val, str):
        s = val.strip()
        if len(s) > 1 and s[0] in "+-" and s[1:].isdigit():
            try:
                return int(current or 0) + int(s)
            except (TypeError, ValueError):
                return val
        return val
    if isinstance(val, list):
        base = current if isinstance(current, list) else ([] if current is None else [current])
        return [*base, *[x for x in val if x not in base]]
    return val


async def commit_declared_deltas(session: AsyncSession, *, scene_id: uuid.UUID) -> None:
    """Apply the scene's beat.expected_state_changes to character_state."""
    scene = await session.get(Scene, scene_id)
    if scene is None:
        return
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return
    beat = (
        await session.execute(select(Beat).where(Beat.chapter_id == scene.chapter_id, Beat.scene_no == scene.scene_no))
    ).scalar_one_or_none()
    if beat is None or not beat.expected_state_changes:
        return

    for character, deltas in beat.expected_state_changes.items():
        if not isinstance(deltas, dict):
            continue
        row = (
            await session.execute(
                select(CharacterState).where(
                    CharacterState.book_id == chapter.book_id,
                    func.lower(CharacterState.character) == character.lower(),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CharacterState(book_id=chapter.book_id, character=character, stats_json={})
            session.add(row)
        stats = dict(row.stats_json or {})
        for attr, val in deltas.items():
            stats[attr] = _apply(stats.get(attr), val)
        row.stats_json = stats  # reassign so SQLAlchemy tracks the JSONB change
        row.as_of_scene_id = scene_id
    await session.flush()
