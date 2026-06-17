"""The spine: one continuous, POV-voiced draft of the whole scene (DESIGN §4)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.shared.config import settings
from dominion.workers import llm

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

# Ceiling for one scene's prose (~3k words). The job-level token budget (ctx.budget) is the real
# guard; this just bounds a single call.
DRAFT_MAX_TOKENS = 4000

_CRAFT = """You are a novelist drafting ONE scene of THE DOMINION REALM, a LitRPG / progression-fantasy novel. \
Write in tight third-person limited, anchored entirely in {pov}'s perception — only what {pov} senses, knows, and feels.

Craft rules:
- Write continuous narrative prose: action, sensation, interiority, dialogue. Dramatize; do not summarize.
- Open inside the scene, not with throat-clearing. End when the beat's purpose is met — do not append a \
resolution the beat does not call for.
- No chapter or scene headers, no author notes, no markdown. Output ONLY the scene's prose.
- This world runs on a game-like interface, but the stats and system text are an interpretation layer in \
{pov}'s mind, not the truth of the place. If {pov} perceives system readouts, render them sparingly and \
in-world; never dump a bare stat sheet.
- Stay consistent with every fact you are given. Do not invent named people, places, or lore that contradict them."""


def _voice_system(ctx: SceneContext) -> str:
    system = _CRAFT.format(pov=ctx.pov)
    if ctx.voice_spec:
        system += f"\n\nVoice for {ctx.pov}:\n{ctx.voice_spec}"
    if ctx.exemplars:
        joined = "\n\n---\n\n".join(ctx.exemplars)
        system += f"\n\nMatch the voice of these passages:\n{joined}"
    return system


def _beat_prompt(ctx: SceneContext) -> str:
    parts: list[str] = []
    if ctx.canon:  # Phase 2: retrieved canon
        parts.append("Canon (treat as true):\n" + "\n".join(f"- {c}" for c in ctx.canon))
    if ctx.pov_summary:  # Phase 2: what this POV knows so far
        parts.append(f"Story so far, as {ctx.pov} understands it:\n{ctx.pov_summary}")
    if ctx.prior_scene_tail:  # Phase 2: in-chapter continuity
        parts.append("The previous scene ended:\n" + ctx.prior_scene_tail)
    if ctx.characters_present:
        parts.append("Characters present: " + ", ".join(ctx.characters_present))
    if ctx.knowledge_injections:
        parts.append(
            f"In this scene, {ctx.pov} learns or already knows:\n"
            + "\n".join(f"- {k}" for k in ctx.knowledge_injections)
        )
    if ctx.expected_state_changes:
        changes = "; ".join(f"{k}: {v}" for k, v in ctx.expected_state_changes.items())
        parts.append(
            "Developments to land by the end (reflect naturally; do NOT write a stat block): " + changes
        )
    parts.append("THE BEAT — what happens in this scene:\n" + (ctx.beat_text or "(no beat text provided)"))
    parts.append(f"\nWrite the scene now, in {ctx.pov}'s point of view. Output only the prose.")
    return "\n\n".join(parts)


def _revise_prompt(ctx: SceneContext) -> str:
    parts: list[str] = []
    if ctx.canon:
        parts.append("Canon (treat as true):\n" + "\n".join(f"- {c}" for c in ctx.canon))
    if ctx.pov_summary:
        parts.append(f"Story so far, as {ctx.pov} understands it:\n{ctx.pov_summary}")
    parts.append("THE BEAT this scene must hit:\n" + (ctx.beat_text or "(no beat text provided)"))
    parts.append("YOUR PRIOR DRAFT of this scene:\n" + (ctx.prior_prose or "(none)"))
    parts.append("REVISION NOTES from the author — address these:\n" + (ctx.revise_feedback or "(none)"))
    parts.append(
        f"\nRewrite the scene in {ctx.pov}'s POV, addressing the notes while keeping what already "
        "works. Output only the revised prose."
    )
    return "\n\n".join(parts)


class Drafter:
    name = "drafter"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        user = _revise_prompt(ctx) if ctx.revise_feedback else _beat_prompt(ctx)
        text, _usage = await llm.complete(
            model=settings.draft_model,
            system=_voice_system(ctx),
            user=user,
            max_tokens=DRAFT_MAX_TOKENS,
            budget=ctx.budget,
        )
        return text.strip()


drafter = Drafter()
