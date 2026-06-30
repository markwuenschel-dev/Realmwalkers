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
# A revision reworks an existing scene against the author's notes — give it a little more headroom so
# the model can expand/restructure without getting clipped mid-fix.
REVISE_MAX_TOKENS = 5000

_CRAFT = """You are a novelist drafting ONE scene of THE DOMINION REALM, a LitRPG / progression-fantasy novel. \
Write in tight third-person limited, anchored entirely in {pov}'s perception — only what {pov} senses, knows, and feels.

Craft rules:
- Write continuous narrative prose: action, sensation, interiority, dialogue. Dramatize; do not summarize.
- When the POV character is analytical or observational, the NARRATION must carry the POV's reading of \
the scene — what they notice, the pattern they are assembling, the conclusion they are weighing and \
whether to trust it — not just external action and dialogue. This interiority is the POV's primary mode \
on the page; do not sacrifice it to keep the scene moving. Humor is seasoning, not the default register: \
a witty POV still thinks more than he quips, so do not make wit an every-beat reflex. Keep internal \
calculation and analysis in the narration — the POV does not speak his reasoning aloud as a bit unless \
the beat explicitly calls for it.
- Open inside the scene, not with throat-clearing. End when the beat's purpose is met — do not append a \
resolution the beat does not call for.
- No chapter or scene headers, no author notes, no commentary. Output ONLY the scene itself.
- This is LitRPG: the game-like interface is real and expected on the page. When {pov} pulls up a status \
window or sees a level-up, a stat or skill panel, or a system notification worth framing, emit it as a \
fenced ```stat block — one `Label: Value` per line, plus a short all-caps header line (no colon) if apt. \
Do NOT draw borders, boxes, or align columns yourself; emit only the label/value lines and the system \
formats the window into an aligned box. A brief one-line system mention may stay inline in prose instead. \
Still filter the experience through {pov}'s perception: the numbers are real, but {pov}'s reading of them \
can be wrong. Emit such a window exactly like this:
```stat
LEVEL UP
Perception: 15
Reflexes: 11
```
- Only show system values you have actually been given (from canon, the ledger, or the beat). Do not invent \
stats, levels, skills, or numbers you were not provided.
- Stay consistent with every fact you are given. Do not invent named people, places, or lore that contradict them."""


def _voice_system(ctx: SceneContext) -> str:
    system = _CRAFT.format(pov=ctx.pov)
    if ctx.voice_spec:
        system += f"\n\nVoice for {ctx.pov}:\n{ctx.voice_spec}"
    if ctx.exemplars:
        joined = "\n\n---\n\n".join(ctx.exemplars)
        system += f"\n\nMatch the voice of these passages:\n{joined}"
    if ctx.dialogue_rules:
        # The source of truth for dialogue: it wins over the per-POV voice guidance above wherever
        # they disagree on how dialogue is written, formatted, or differentiated between characters.
        system += (
            "\n\nDIALOGUE RULES — AUTHORITATIVE. These are the source of truth for ALL dialogue. "
            "Where they conflict with the voice guidance above on how dialogue is written, "
            "formatted, or differentiated between characters, follow THESE rules:\n"
            f"{ctx.dialogue_rules}"
        )
    return system


def _contract_block(ctx: SceneContext) -> str | None:
    """The chapter packet's binding constraints (contract-first drafting, Phase 2), as a high-salience
    block the writer must obey. None when the scene isn't packet-derived. Leads the prompt so it
    dominates the beat + canon: a good packet prevents beautiful wrong scenes."""
    c = ctx.contract
    if not c:
        return None

    def lst(key: str) -> list[str]:
        value = c.get(key)
        return [str(v) for v in value] if isinstance(value, list) else []

    sections: list[str] = []
    must_not = (
        [f"reveal or foreshadow: {x}" for x in lst("forbidden_reveals")]
        + [f"let the reader learn yet: {x}" for x in lst("forbidden_knowledge")]
        + [f"let this happen in the scene: {x}" for x in lst("forbidden_beats")]
        + [f"put this system/UI concept on the page: {x}" for x in lst("forbidden_ui_concepts")]
    )
    if must_not:
        sections.append("MUST NOT:\n" + "\n".join(f"- {m}" for m in must_not))

    must = [f"reveal in this scene: {x}" for x in lst("required_reveals")]
    exit_state = c.get("exit_state")
    if isinstance(exit_state, str) and exit_state.strip():
        must.append(f"end the scene at this state: {exit_state.strip()}")
    if must:
        sections.append("MUST:\n" + "\n".join(f"- {m}" for m in must))

    locks = lst("canon_locks") + lst("roster_locks") + lst("relationship_locks") + lst("timeline_locks")
    if locks:
        sections.append("IMMUTABLE — do not contradict:\n" + "\n".join(f"- {x}" for x in locks))

    if not sections:
        return None
    header = (
        "CONTRACT — obey exactly. This scene is bound by its approved scene packet. "
        "These constraints OVERRIDE the beat and any canon below wherever they conflict; violating one "
        "is a failed scene, however well-written. Do NOT echo these contract labels or phrasings into "
        "the prose — translate every constraint into lived action, perception, dialogue, and consequence."
    )
    return header + "\n\n" + "\n\n".join(sections)


def _length_instruction(ctx: SceneContext) -> str | None:
    """A firm length instruction from the ScenePacket word budget (falls back to target_words)."""
    wb = ctx.word_budget or {}
    target = wb.get("target") or ctx.target_words
    if not target:
        return None
    parts = [f"Aim for about {target} words."]
    if wb.get("max"):
        parts.append(f"Do not exceed {wb['max']} unless the required beats cannot fit.")
    if wb.get("hard_max"):
        parts.append(f"Never exceed {wb['hard_max']}.")
    mns = [str(m).strip() for m in (wb.get("must_not_spend_words_on") or []) if str(m).strip()]
    if mns:
        parts.append("Do not spend words on: " + "; ".join(mns) + ".")
    if wb.get("compression_priority"):
        parts.append(
            "If space is tight, compress in this order: " + " ".join(str(p) for p in wb["compression_priority"])
        )
    return "LENGTH:\n" + " ".join(parts)


def _phrase_avoidance(ctx: SceneContext) -> str | None:
    """Phrases the drafter must not echo into prose, from the scene contract — keeps packet/contract
    language out of the lived scene."""
    body = ctx.scene_contract or {}
    phrases = [str(p).strip() for p in (body.get("phrases_to_avoid_echoing") or []) if str(p).strip()]
    if not phrases:
        return None
    return (
        "DO NOT echo contract labels or packet phrasing into prose. Translate constraints into lived "
        "action, perception, dialogue, and consequence. Avoid these phrasings:\n" + "\n".join(f"- {p}" for p in phrases)
    )


def _beat_prompt(ctx: SceneContext) -> tuple[str | None, str]:
    """Return (stable_prefix, volatile_user). The prefix carries canon/context that doesn't change
    across revision attempts and gets its own cache breakpoint; the volatile part has the beat + write
    instruction which is unique per call."""
    prefix_parts: list[str] = []
    if contract := _contract_block(ctx):
        prefix_parts.append(contract)
    if ctx.canon:
        prefix_parts.append("Canon (treat as true):\n" + "\n".join(f"- {c}" for c in ctx.canon))
    if ctx.pov_summary:
        prefix_parts.append(f"Story so far, as {ctx.pov} understands it:\n{ctx.pov_summary}")
    if ctx.prior_scene_tail:
        prefix_parts.append("The previous scene ended:\n" + ctx.prior_scene_tail)

    volatile_parts: list[str] = []
    if ctx.characters_present:
        volatile_parts.append("Characters present: " + ", ".join(ctx.characters_present))
    if ctx.knowledge_injections:
        volatile_parts.append(
            f"In this scene, {ctx.pov} learns or already knows:\n"
            + "\n".join(f"- {k}" for k in ctx.knowledge_injections)
        )
    if ctx.expected_state_changes:
        changes = "; ".join(f"{k}: {v}" for k, v in ctx.expected_state_changes.items())
        volatile_parts.append(
            "Developments to land by the end (reflect naturally; do NOT write a stat block): " + changes
        )
    volatile_parts.append("THE BEAT — what happens in this scene:\n" + (ctx.beat_text or "(no beat text provided)"))
    if phrases := _phrase_avoidance(ctx):
        volatile_parts.append(phrases)
    if length := _length_instruction(ctx):
        volatile_parts.append(length)
    elif ctx.target_words:
        volatile_parts.append(
            f"Length: aim for roughly {ctx.target_words} words — a guide for scope, not a hard limit."
        )
    volatile_parts.append(f"\nWrite the scene now, in {ctx.pov}'s point of view. Output only the prose.")

    prefix = "\n\n".join(prefix_parts) if prefix_parts else None
    return prefix, "\n\n".join(volatile_parts)


def _revise_prompt(ctx: SceneContext) -> tuple[str | None, str]:
    """Return (stable_prefix, volatile_user). The prefix carries canon/context; the volatile part
    has the prior draft + revision notes, which differ on every revision attempt."""
    prefix_parts: list[str] = []
    if contract := _contract_block(ctx):
        prefix_parts.append(contract)
    if ctx.canon:
        prefix_parts.append("Canon (treat as true):\n" + "\n".join(f"- {c}" for c in ctx.canon))
    if ctx.pov_summary:
        prefix_parts.append(f"Story so far, as {ctx.pov} understands it:\n{ctx.pov_summary}")

    volatile_parts: list[str] = []
    volatile_parts.append("THE BEAT this scene must hit:\n" + (ctx.beat_text or "(no beat text provided)"))
    volatile_parts.append("YOUR PRIOR DRAFT of this scene:\n" + (ctx.prior_prose or "(none)"))
    volatile_parts.append("REVISION NOTES from the author — address these:\n" + (ctx.revise_feedback or "(none)"))
    if phrases := _phrase_avoidance(ctx):
        volatile_parts.append(phrases)
    if length := _length_instruction(ctx):
        volatile_parts.append(length)
    elif ctx.target_words:
        volatile_parts.append(
            f"Length: aim for roughly {ctx.target_words} words — a guide for scope, not a hard limit."
        )
    volatile_parts.append(
        f"\nRewrite the scene in {ctx.pov}'s POV, addressing the notes while keeping what already "
        "works. Output only the revised prose."
    )

    prefix = "\n\n".join(prefix_parts) if prefix_parts else None
    return prefix, "\n\n".join(volatile_parts)


class Drafter:
    name = "drafter"

    async def run(self, prose: str | None, ctx: SceneContext) -> str:
        revising = ctx.revise_feedback is not None
        user_prefix, user = _revise_prompt(ctx) if revising else _beat_prompt(ctx)
        text, _usage = await llm.complete(
            model=settings.draft_model,
            system=_voice_system(ctx),
            user=user,
            user_prefix=user_prefix,
            max_tokens=REVISE_MAX_TOKENS if revising else DRAFT_MAX_TOKENS,
            budget=ctx.budget,
        )
        return text.strip()


drafter = Drafter()
