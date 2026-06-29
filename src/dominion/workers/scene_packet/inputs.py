"""Shared scene-packet derivation inputs (word targets, prior-scene keys for source hashing)."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import SceneStatus
from dominion.shared.models import Scene

_DEFAULT_SCENE_TARGET = 1500


def chapter_targets(body: dict[str, Any], seeds: list[dict[str, Any]]) -> tuple[int, int | None]:
    """Chapter target + optional hard cap. Prefer an explicit chapter figure, else sum seed targets."""
    target = body.get("chapter_target_words")
    if isinstance(target, int) and target > 0:
        return target, body.get("chapter_max_words") if isinstance(body.get("chapter_max_words"), int) else None
    seed_targets = [
        t for s in seeds
        if isinstance((wb := s.get("word_budget")), dict) and isinstance((t := wb.get("target")), int)
    ]
    chapter_target = sum(seed_targets) if seed_targets else _DEFAULT_SCENE_TARGET * len(seeds)
    return chapter_target, None


async def prior_scene_keys(
    session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int
) -> list[list[Any]]:
    """Stable keys for approved scenes before this one — feeds source_hash / staleness."""
    rows = (await session.execute(
        select(Scene.id, Scene.version, Scene.word_count)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.scene_no < scene_no,
            Scene.status == SceneStatus.APPROVED,
        )
        .order_by(Scene.scene_no)
    )).all()
    return [[str(sid), ver, wc] for sid, ver, wc in rows]
