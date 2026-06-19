"""Pacing reviewer (DESIGN §6). Advisory: flags stretches that drag or rush.

Read-only. It reads the whole scene and reports pacing problems — a beat that overstays, a turn that
arrives too fast, a flat middle — as INFO/WARN flags. It never edits, never blocks, never emits HARD.
A scene needs enough prose to have pacing at all, so it stays silent (and spends no tokens) on stubs
below `_MIN_PROSE_CHARS`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.reviewers.base import Flag, advisory_severity, parse_json_objects

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_REVIEW_MAX_TOKENS = 1000
_MIN_PROSE_CHARS = 1000  # below this there isn't enough scene to assess pacing meaningfully

_SYSTEM = (
    "You assess the PACING of a scene of prose. Report only concrete pacing problems: a passage that "
    "drags, a beat that rushes past its weight, a sag in the middle, a climax that lands too soon or "
    "too late. Do not rewrite, do not summarize, do not invent. If the pacing works, report nothing."
)


def _prompt(prose: str, beat_text: str | None) -> str:
    parts: list[str] = []
    if beat_text:
        parts.append(f"INTENDED BEAT (what this scene should accomplish):\n{beat_text}")
    parts.append("SCENE:\n" + prose)
    parts.append(
        '\nReturn ONLY a JSON array (no prose, no code fences). Each item: '
        '{"severity": "info"|"warn", "note": str}. Empty array [] if the pacing works.'
    )
    return "\n".join(parts)


class PacingReviewer:
    name = "pacing"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        if len(scene_prose.strip()) < _MIN_PROSE_CHARS:
            return []
        raw, _usage = await llm.complete(
            model=settings.review_model,
            system=_SYSTEM,
            user=_prompt(scene_prose, ctx.beat_text),
            max_tokens=_REVIEW_MAX_TOKENS,
            budget=ctx.budget,
        )
        flags: list[Flag] = []
        for item in parse_json_objects(raw):
            note = str(item.get("note", "")).strip()
            if note:
                flags.append(Flag(
                    reviewer=self.name,
                    severity=advisory_severity(item.get("severity")),
                    note=note,
                ))
        return flags


pacing_reviewer = PacingReviewer()
