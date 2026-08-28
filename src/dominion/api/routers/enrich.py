"""Enrich prose the author already wrote — the direct path, no contract apparatus.

The enrichment passes take a plain string (`run_enrichment(prose, ctx, ...)`), so none of the
contract-first machinery applies: packets, beats, and approval exist to tell the DRAFTER what to
write from scratch. Injected prose is already written. This router is that seam, and deliberately
WRITES no table — nothing persists, so there is no adoption, fingerprint, or staleness to reason
about. Landing the result as a scene is a separate, later decision.

It does take a session, for one read: the dialogue rules. They live in `style_documents` because
`series/` never reaches the deploy box, and the dialogue lane without its rules is the lane with the
one thing that defines it removed — the same silent production failure the drafting path had.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.workers.budget import BudgetExceeded, TokenBudget
from dominion.workers.context.dialogue_rules import load_dialogue_rules
from dominion.workers.context.types import SceneContext
from dominion.workers.router import DRAFT_PASSES, passes_for
from dominion.workers.specialists.base import PassError

router = APIRouter(tags=["enrich"])

# Canonical run order, derived from the router rather than restated here. `passes_for` orders by the
# drafting pipeline's fixed sequence, so asking it for every lane yields that sequence — and this
# endpoint can never drift from the order the pipeline uses. Restating the lane list locally is what
# silently killed the sensory lane once already (router.py:18).
_ALL_LANES: list[str] = [p.name for p in passes_for(list(DRAFT_PASSES))]


class EnrichIn(BaseModel):
    prose: str = Field(min_length=1)
    # Optional on purpose: a prologue or omniscient interlude has no POV character, and naming a fake
    # one ("none") poisons the prompt — the lane is told to stay in that character's head and, unable
    # to, changes nothing. Blank => the POV-free transform, which preserves the viewpoint on the page.
    pov: str | None = None
    # Empty or omitted => every lane. "Deepen all of it" is the common ask, and the alternative
    # reading of an empty selection ("run nothing") would make Enrich a no-op button. Order here is
    # ignored: lanes always run in the pipeline's canonical order, so the result is a function of
    # WHICH lanes you picked, never the sequence you clicked them in.
    lanes: list[str] | None = None
    beat_text: str | None = None


class LaneFailure(BaseModel):
    lane: str
    reason: str


class EnrichOut(BaseModel):
    enriched: str
    # Lanes that actually transformed the prose, in the order they ran, and the ones that did not.
    # Both are needed to read `enriched` honestly: a partial chain is still a real result, but the
    # author must be able to see it is missing a lane they asked for.
    lanes_run: list[str]
    lanes_failed: list[LaneFailure]
    model: str
    pov_free: bool
    dialogue_rules_loaded: bool
    source_chars: int
    enriched_chars: int
    tokens_used: int


@router.get("/enrich/lanes")
async def lanes() -> dict[str, list[str]]:
    # Canonical order, not alphabetical: this list IS the run order, so sorting it would describe a
    # chain that never happens.
    return {"lanes": _ALL_LANES}


@router.post("/enrich", response_model=EnrichOut)
async def enrich(body: EnrichIn, session: SessionDep) -> EnrichOut:
    requested = body.lanes or _ALL_LANES
    unknown = [lane for lane in requested if lane not in DRAFT_PASSES]
    if unknown:
        raise HTTPException(422, f"unknown lane(s) {unknown}; expected any of {_ALL_LANES}")
    # Canonical order, duplicates collapsed — `passes_for` owns both.
    specialists = passes_for(requested)

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
    dialogue_rules = await load_dialogue_rules(session, [])

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

    # Chain the lanes: each transform reads the PREVIOUS lane's output, exactly as the drafting
    # pipeline runs them (pipeline.py). A lane deepens one dimension and preserves the rest, so the
    # composition is the scene with each requested dimension deepened in turn.
    #
    # Enrichment soft-fails by design — the pipeline keeps the spine and flags the failed pass rather
    # than losing the job. Mirror that: one lane failing must not discard the lanes that already
    # succeeded (real prose, real tokens, ~12s each), so record it and keep going with the best prose
    # we have. The exception is a chain where NOTHING ran: there is no enrichment to return, and
    # handing the author's own prose back as a "result" would be a lie, so that still 502s.
    prose = body.prose
    lanes_run: list[str] = []
    lanes_failed: list[LaneFailure] = []
    for specialist in specialists:
        try:
            prose = await specialist.run(prose, ctx)
            lanes_run.append(specialist.name)
        except PassError as exc:
            lanes_failed.append(LaneFailure(lane=specialist.name, reason=str(exc)))
        except BudgetExceeded as exc:
            # The budget is shared across the whole chain, so once it is gone every remaining lane
            # would fail the same way. Stop rather than bill for calls that cannot succeed.
            lanes_failed.append(LaneFailure(lane=specialist.name, reason=f"token budget exhausted: {exc}"))
            break

    if not lanes_run:
        raise HTTPException(502, "; ".join(f.reason for f in lanes_failed))

    return EnrichOut(
        enriched=prose,
        lanes_run=lanes_run,
        lanes_failed=lanes_failed,
        model=settings.enrich_model,
        pov_free=not pov,
        dialogue_rules_loaded=bool(dialogue_rules),
        source_chars=len(body.prose),
        enriched_chars=len(prose),
        tokens_used=ctx.budget.used,
    )
