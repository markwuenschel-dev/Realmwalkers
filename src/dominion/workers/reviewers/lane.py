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
    "do not comment on any other dimension. Respect the scene contract: do not ask for anything its "
    "forbidden beats exclude, and do not flag a predeclared false-positive trap. If this dimension "
    "holds, report nothing."
)


def _scene_section(ctx: SceneContext, name: str) -> str:
    """Scene-specific review constraints from the ScenePacket reviewer contract (empty when absent)."""
    rc = ctx.reviewer_contract or {}
    if not rc:
        return ""
    import json as _json

    lines: list[str] = []
    if rc.get("scene_job"):
        lines.append(f"Scene job: {rc['scene_job']}")
    if rc.get("scene_type"):
        lines.append(f"Scene type: {rc['scene_type']}")
    if rc.get("required_beats"):
        lines.append(f"Required beats: {_json.dumps(rc['required_beats'])}")
    if rc.get("forbidden_beats"):
        lines.append(f"Forbidden beats (do not ask for these): {_json.dumps(rc['forbidden_beats'])}")
    lane_instr = (rc.get("reviewer_instructions") or {}).get(name)
    if lane_instr:
        lines.append(f"Instructions for this lane: {_json.dumps(lane_instr)}")
    if rc.get("reviewer_false_positive_traps"):
        lines.append(f"False-positive traps (do not flag): {_json.dumps(rc['reviewer_false_positive_traps'])}")
    wb = rc.get("word_budget") or {}
    if wb.get("target"):
        lines.append(f"Word budget target: {wb.get('target')} (min {wb.get('min')}, max {wb.get('max')})")
    return ("SCENE CONTRACT:\n" + "\n".join(lines) + "\n\n") if lines else ""


def _prompt(prose: str, ctx: SceneContext, name: str) -> str:
    parts: list[str] = []
    if section := _scene_section(ctx, name):
        parts.append(section.rstrip())
    if ctx.beat_text:
        parts.append(f"INTENDED BEAT (what this scene should accomplish):\n{ctx.beat_text}")
    parts.append("SCENE:\n" + prose)
    parts.append(
        "\nReturn ONLY a JSON array (no prose, no code fences). Each item: "
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
        user=_prompt(scene_prose, ctx, name),
        max_tokens=_REVIEW_MAX_TOKENS,
        budget=ctx.budget,
        expect_cache=False,
    )
    flags: list[Flag] = []
    for item in parse_json_objects(raw):
        note = str(item.get("note", "")).strip()
        if not note:
            continue
        quote = str(item.get("quote", "")).strip()
        flags.append(
            Flag(
                reviewer=name,
                severity=advisory_severity(item.get("severity")),
                note=note,
                payload={"quote": quote} if quote else None,
            )
        )
    return flags


# --- Review lane singletons (DESIGN §6, OPEN-8) ---------------------------------------------------
# The combat / sensory / dialogue lanes are one shape (`lane_review`) differing only in their focus.
# router.py imports these singletons directly; each fires only when the beat carries its tag.

_COMBAT_FOCUS = (
    "the fight choreography — confusing spatial geography (who is where, what moves), blows that do not "
    "connect or land logically, and action that contradicts the combatants' established stats or abilities"
)

_DIALOGUE_FOCUS = (
    "the dialogue — flat or interchangeable voices, on-the-nose lines that state what should be subtext, "
    "and exchanges that do not land or carry the weight the moment needs"
)

_SENSORY_FOCUS = (
    "the concreteness of sensory grounding — passages that stay abstract, generic, or told where specific "
    "physical sense detail (sight, sound, smell, taste, touch) is called for"
)


class _LaneReviewer:
    """One tag-gated advisory review lane assessing a single `focus` dimension (never blocks, never HARD)."""

    def __init__(self, name: str, focus: str) -> None:
        self.name = name
        self._focus = focus

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        return await lane_review(scene_prose, ctx, name=self.name, focus=self._focus)


combat_reviewer = _LaneReviewer("combat", _COMBAT_FOCUS)
dialogue_reviewer = _LaneReviewer("dialogue", _DIALOGUE_FOCUS)
sensory_reviewer = _LaneReviewer("sensory", _SENSORY_FOCUS)
