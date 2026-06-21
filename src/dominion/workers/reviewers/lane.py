"""Shared machinery for the tag-gated review lanes (DESIGN §6, OPEN-8).

The combat / sensory / dialogue reviewers are the same shape as pacing/voice: read-only, advisory,
token-gated. Each one assesses a single dimension and reports concrete problems in it as INFO/WARN
flags. They never rewrite, never block, and never emit HARD — only the lane focus differs, so the loop
lives here once. The router runs a lane reviewer only when the beat carries its tag, so there is no
extra "is there combat here?" gate beyond the shared prose-length floor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.reviewers.base import Flag, advisory_severity, parse_json_objects

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_REVIEW_MAX_TOKENS = 1000
_MIN_PROSE_CHARS = 1000  # below this there isn't enough scene to assess a dimension meaningfully

_SYSTEM = (
    "You review ONE dimension of a scene of prose: {focus}. Report only concrete, specific problems in "
    "THAT dimension — a line or moment that fails it. Do not rewrite, do not praise, do not invent, and "
    "do not comment on any other dimension. If this dimension holds, report nothing."
)


def _prompt(prose: str, beat_text: str | None) -> str:
    parts: list[str] = []
    if beat_text:
        parts.append(f"INTENDED BEAT (what this scene should accomplish):\n{beat_text}")
    parts.append("SCENE:\n" + prose)
    parts.append(
        '\nReturn ONLY a JSON array (no prose, no code fences). Each item: '
        '{"severity": "info"|"warn", "note": str, "quote": str (the problem phrase, optional)}. '
        "Empty array [] if the dimension holds."
    )
    return "\n".join(parts)


async def lane_review(scene_prose: str, ctx: SceneContext, *, name: str, focus: str) -> list[Flag]:
    """Run one advisory lane review over `scene_prose`; silent (and free) on stubs below the floor."""
    if len(scene_prose.strip()) < _MIN_PROSE_CHARS:
        return []
    raw, _usage = await llm.complete(
        model=settings.review_model,
        system=_SYSTEM.format(focus=focus),
        user=_prompt(scene_prose, ctx.beat_text),
        max_tokens=_REVIEW_MAX_TOKENS,
        budget=ctx.budget,
    )
    flags: list[Flag] = []
    for item in parse_json_objects(raw):
        note = str(item.get("note", "")).strip()
        if not note:
            continue
        quote = str(item.get("quote", "")).strip()
        flags.append(Flag(
            reviewer=name,
            severity=advisory_severity(item.get("severity")),
            note=note,
            payload={"quote": quote} if quote else None,
        ))
    return flags
