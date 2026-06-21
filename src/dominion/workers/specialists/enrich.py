"""Shared machinery for the enrichment passes (DESIGN §5-6).

The three passes (combat / sensory / dialogue) are the same shape: a TRANSFORM of the drafted spine
that deepens exactly one dimension and leaves everything else — events, structure, POV, canon, and the
```stat``` marker blocks — untouched. Only the lane instruction differs, so it lives here once.

The pass receives the marker-form prose (the pipeline renders ```stat``` blocks only at the very end,
`pipeline.py`), so a pass MUST return the markers verbatim or the rendered window is lost.

Failure contract (DESIGN §4 / OPEN-10): `BudgetExceeded` propagates so the pipeline keeps the spine and
aborts remaining passes; any other failure — including empty/degenerate output — becomes a `PassError`
so the spine still lands, flagged, instead of hard-failing the job.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import BudgetExceeded
from dominion.workers.specialists.base import PassError

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

# A transform returns the whole scene; bound a single call the same as the drafter's spine.
ENRICH_MAX_TOKENS = 4000
# Below this fraction of the input, the model has dropped scene content rather than deepened it —
# treat that (and empty output) as a failed pass so the spine lands flagged instead of truncated.
_MIN_RETAINED_FRACTION = 0.5
_RATIO_FLOOR_CHARS = 200  # only apply the ratio guard once there's a real scene to shrink

_TRANSFORM = """You are revising ONE scene of THE DOMINION REALM, a LitRPG / progression-fantasy novel, \
written in tight third-person limited anchored entirely in {pov}'s perception.

Your ONLY job in this pass: {dimension}

Hard rules:
- This is a TRANSFORM, not a rewrite. Preserve the scene's events, structure, outcome, and meaning. \
Change nothing except what deepening this one dimension requires; do not touch any other dimension.
- Stay entirely in {pov}'s point of view — only what {pov} senses, knows, and feels. Introduce no new POV.
- Invent no canon: add no named people, places, lore, stats, levels, skills, or numbers beyond what the \
prose already contains. Deepen what is on the page; do not introduce new facts.
- Preserve every fenced ```stat``` block EXACTLY as written — verbatim, including the ```stat fence and \
every `Label: Value` line. Do not reformat it, draw borders, or alter any value.
- Output ONLY the full scene prose. No preamble, no commentary, no headers, and no code fences around \
the scene itself."""

_DIALOGUE_RULES = (
    "\n\nDIALOGUE RULES — AUTHORITATIVE. These are the source of truth for ALL dialogue. Where they "
    "conflict with anything above on how dialogue is written, formatted, or differentiated between "
    "characters, follow THESE rules:\n{rules}"
)


def _user(prose: str, beat_text: str | None) -> str:
    parts: list[str] = []
    if beat_text:
        parts.append(
            "INTENDED BEAT (do not change what happens; only deepen the prose):\n" + beat_text
        )
    parts.append("SCENE TO REVISE:\n" + prose)
    parts.append("\nReturn the full scene, revised in place. Output only the prose.")
    return "\n\n".join(parts)


async def run_enrichment(
    prose: str | None, ctx: SceneContext, *, name: str, dimension: str, use_dialogue_rules: bool = False
) -> str:
    """Run one enrichment transform over `prose` and return the deepened scene.

    Lane files supply only `name` (for the error message) and `dimension` (the one thing to deepen);
    `use_dialogue_rules` appends the authoritative dialogue rules for the dialogue lane.
    """
    source = (prose or "").strip()
    system = _TRANSFORM.format(pov=ctx.pov, dimension=dimension)
    if use_dialogue_rules and ctx.dialogue_rules:
        system += _DIALOGUE_RULES.format(rules=ctx.dialogue_rules)

    try:
        text, _usage = await llm.complete(
            model=settings.enrich_model,
            system=system,
            user=_user(source, ctx.beat_text),
            max_tokens=ENRICH_MAX_TOKENS,
            budget=ctx.budget,
        )
    except BudgetExceeded:
        raise  # pipeline keeps the spine and aborts the remaining passes (DESIGN §10)
    except Exception as exc:  # any other failure soft-fails the pass (OPEN-10)
        raise PassError(f"{name} enrichment pass failed: {exc}") from exc

    out = text.strip()
    if not out:
        raise PassError(f"{name} enrichment pass returned empty output")
    if len(source) >= _RATIO_FLOOR_CHARS and len(out) < _MIN_RETAINED_FRACTION * len(source):
        raise PassError(
            f"{name} enrichment pass returned degenerate output "
            f"({len(out)} chars from {len(source)} — scene content lost)"
        )
    return out
