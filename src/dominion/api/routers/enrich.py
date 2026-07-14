"""Enrich prose the author already wrote — the direct path, no contract apparatus.

The enrichment passes take a plain string (`run_enrichment(prose, ctx, ...)`), so none of the
contract-first machinery applies: packets, beats, and approval exist to tell the DRAFTER what to
write from scratch. Injected prose is already written. This router is that seam, and deliberately
touches no table — nothing persists, so there is no adoption, fingerprint, or staleness to reason
about. Landing the result as a scene is a separate, later decision.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dominion.shared.config import settings
from dominion.workers.budget import TokenBudget
from dominion.workers.context.dialogue_rules import load_dialogue_rules
from dominion.workers.context.types import SceneContext
from dominion.workers.specialists.base import PassError
from dominion.workers.specialists.enrich import combat_pass, dialogue_pass, sensory_pass

router = APIRouter(tags=["enrich"])

_LANES = {"combat": combat_pass, "sensory": sensory_pass, "dialogue": dialogue_pass}


class EnrichIn(BaseModel):
    prose: str = Field(min_length=1)
    # Optional on purpose: a prologue or omniscient interlude has no POV character, and naming a fake
    # one ("none") poisons the prompt — the lane is told to stay in that character's head and, unable
    # to, changes nothing. Blank => the POV-free transform, which preserves the viewpoint on the page.
    pov: str | None = None
    lane: str
    beat_text: str | None = None


class EnrichOut(BaseModel):
    enriched: str
    lane: str
    model: str
    pov_free: bool
    dialogue_rules_loaded: bool
    source_chars: int
    enriched_chars: int
    tokens_used: int


@router.get("/enrich/lanes")
async def lanes() -> dict[str, list[str]]:
    return {"lanes": sorted(_LANES)}


@router.post("/enrich", response_model=EnrichOut)
async def enrich(body: EnrichIn) -> EnrichOut:
    specialist = _LANES.get(body.lane)
    if specialist is None:
        raise HTTPException(422, f"unknown lane {body.lane!r}; expected one of {sorted(_LANES)}")

    pov = (body.pov or "").strip()

    # The dialogue lane's rules are AUTHORITATIVE for how dialogue is written — running that lane
    # without them silently strips out the one thing that makes it the dialogue lane.
    #
    # Pass an EMPTY roster deliberately. `_scope_dialogue_rules` keeps only the `###` blocks naming a
    # character in the roster, so scoping to a partial cast DROPS the rules for everyone else on the
    # page. The drafting path can scope safely because a Beat tells it the full cast; injected prose
    # carries no cast list, so any roster we could build here would be a guess that silently discards
    # real rules. An empty roster short-circuits scoping and loads the full ruleset — the only honest
    # option when the cast is unknown.
    dialogue_rules = load_dialogue_rules([])

    # The minimum context an enrichment pass reads: pov, beat_text, dialogue_rules, budget. The ids
    # are structural (never read here) and the rest of SceneContext serves the drafter/reviewers.
    ctx = SceneContext(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov=pov,
        scene_no=1,
        tags=[],
        characters_present=[],
        beat_text=body.beat_text,
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        dialogue_rules=dialogue_rules,
        budget=TokenBudget(max_tokens=settings.scene_token_budget),
    )

    try:
        out = await specialist.run(body.prose, ctx)
    except PassError as exc:
        # Enrichment soft-fails by design (the pipeline keeps the un-enriched spine). Here there is no
        # spine to keep, so surface the reason instead of returning the author's prose as a "result".
        raise HTTPException(502, str(exc)) from exc

    return EnrichOut(
        enriched=out,
        lane=body.lane,
        model=settings.enrich_model,
        pov_free=not pov,
        dialogue_rules_loaded=bool(dialogue_rules),
        source_chars=len(body.prose),
        enriched_chars=len(out),
        tokens_used=ctx.budget.used,
    )
