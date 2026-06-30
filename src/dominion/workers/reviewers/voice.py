"""Voice reviewer (DESIGN §6). Advisory: flags where the prose drifts from the POV's voice spec.

Read-only. It compares the drafted scene against the POV's `voice_spec` (and exemplars, if any) and
reports drift as INFO/WARN flags — it never edits prose, never blocks, and never emits HARD. With no
voice spec to measure against, it stays silent and spends no tokens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.reviewers.base import Flag, advisory_severity, parse_json_objects

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_REVIEW_MAX_TOKENS = 1200

_SYSTEM = (
    "You audit whether a scene's prose matches a target NARRATIVE VOICE for its POV character. "
    "Report only concrete, specific drift — a line or move that reads as the wrong register, rhythm, "
    "or sensibility for this voice. Do not rewrite, do not praise, do not invent. If the prose holds "
    "the voice, report nothing."
)


def _prompt(prose: str, voice_spec: str, exemplars: list[str]) -> str:
    parts = [f"TARGET VOICE SPEC:\n{voice_spec}"]
    if exemplars:
        parts.append("VOICE EXEMPLARS (match this register; do not quote them back):\n" + "\n\n---\n\n".join(exemplars))
    parts.append("SCENE:\n" + prose)
    parts.append(
        "\nReturn ONLY a JSON array (no prose, no code fences). Each item: "
        '{"severity": "info"|"warn", "note": str, "quote": str (the drifting phrase, optional)}. '
        "Empty array [] if the prose holds the voice."
    )
    return "\n".join(parts)


class VoiceReviewer:
    name = "voice"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        if not ctx.voice_spec or not scene_prose.strip():
            return []
        raw, _usage = await llm.complete(
            model=settings.review_model,
            system=_SYSTEM,
            user=_prompt(scene_prose, ctx.voice_spec, ctx.exemplars),
            max_tokens=_REVIEW_MAX_TOKENS,
            budget=ctx.budget,
        )
        flags: list[Flag] = []
        for item in parse_json_objects(raw):
            note = str(item.get("note", "")).strip()
            if not note:
                continue
            quote = str(item.get("quote", "")).strip()
            flags.append(
                Flag(
                    reviewer=self.name,
                    severity=advisory_severity(item.get("severity")),
                    note=note,
                    payload={"quote": quote} if quote else None,
                )
            )
        return flags


voice_reviewer = VoiceReviewer()
