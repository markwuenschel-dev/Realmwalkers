"""Learning from edits — Tier 3: distilled voice/dialogue rules (LEARNING_FROM_EDITS).

`POST /books/{id}/distill` runs a review-model pass over the author's recent edits and stores proposed
rules (status `pending`) for review — synchronous, so it can take a few seconds and may 504 on a hung
call. `GET /books/{id}/rule-proposals` lists them; `POST /rule-proposals/{id}/decision` accepts (the
rule is appended to the POV's `PovProfile.voice_spec`, read fresh on the next draft) or rejects one.
Nothing here changes a draft until the author accepts — the same human gate as any edit (DESIGN §11).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import RuleProposalStatus
from dominion.shared.models import PovProfile, RuleProposal
from dominion.shared.schemas import RuleProposalDecisionIn, RuleProposalOut
from dominion.workers.budget import TokenBudget
from dominion.workers.learning import distill

router = APIRouter(tags=["learning"])


@router.post("/books/{book_id}/distill", response_model=list[RuleProposalOut])
async def distill_rules(
    book_id: uuid.UUID, session: SessionDep, pov: str | None = None
) -> list[RuleProposal]:
    """Distill recent edits into proposed rules for one POV (or every POV with edits) and persist the
    new ones as `pending`. Deduped against existing non-rejected proposals so re-running doesn't pile
    up the same rule. Returns the freshly created proposals."""
    budget = TokenBudget(max_tokens=settings.scene_token_budget)
    povs = [pov] if pov else await distill.candidate_povs(session, book_id=book_id)

    created: list[RuleProposal] = []
    for p in povs:
        pairs = await distill.load_recent_pairs(
            session, book_id=book_id, pov=p, limit=settings.distill_max_pairs
        )
        try:
            proposals = await distill.propose_rules(
                pairs, pov=p, budget=budget, time_budget_s=settings.distill_time_budget_s
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc

        # Don't re-propose a rule already pending or accepted for this (book, pov).
        seen = {
            (r.rule_text or "").strip().lower()
            for r in (await session.execute(
                select(RuleProposal).where(
                    RuleProposal.book_id == book_id,
                    RuleProposal.pov == p,
                    RuleProposal.status != RuleProposalStatus.REJECTED,
                )
            )).scalars().all()
        }
        pair_ids = [str(pair.id) for pair in pairs]
        for pr in proposals:
            key = pr["rule"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            row = RuleProposal(
                book_id=book_id, pov=p, kind=pr["kind"], rule_text=pr["rule"],
                rationale=pr["rationale"] or None, source_pair_ids=pair_ids or None,
                status=RuleProposalStatus.PENDING,
            )
            session.add(row)
            created.append(row)

    await session.commit()
    return created


@router.get("/books/{book_id}/rule-proposals", response_model=list[RuleProposalOut])
async def list_rule_proposals(
    book_id: uuid.UUID, session: SessionDep, status: str | None = None
) -> list[RuleProposal]:
    stmt = select(RuleProposal).where(RuleProposal.book_id == book_id)
    if status:
        stmt = stmt.where(RuleProposal.status == status)
    rows = (await session.execute(stmt.order_by(RuleProposal.created_at.desc()))).scalars().all()
    return list(rows)


async def _apply_rule(session: SessionDep, proposal: RuleProposal) -> None:
    """Append an accepted rule to the POV's voice spec (find-or-create the profile). The drafter reads
    `voice_spec` fresh per scene, so the rule lands on the next draft with no redeploy. Stored as a
    bullet line, distinct from any hand-authored spec text above it."""
    profile = (await session.execute(
        select(PovProfile).where(
            PovProfile.book_id == proposal.book_id, PovProfile.character == proposal.pov
        )
    )).scalar_one_or_none()
    line = f"- {proposal.rule_text.strip()}"
    if profile is None:
        session.add(PovProfile(book_id=proposal.book_id, character=proposal.pov, voice_spec=line))
    else:
        existing = (profile.voice_spec or "").rstrip()
        profile.voice_spec = f"{existing}\n{line}" if existing else line


@router.post("/rule-proposals/{proposal_id}/decision", response_model=RuleProposalOut)
async def decide_rule_proposal(
    proposal_id: uuid.UUID, body: RuleProposalDecisionIn, session: SessionDep
) -> RuleProposal:
    proposal = await session.get(RuleProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="rule proposal not found")
    if body.rule_text and body.rule_text.strip():
        proposal.rule_text = body.rule_text.strip()  # author may edit the text before accepting
    # Apply only on the pending→accepted transition, so re-accepting can't append the rule twice.
    if body.status == RuleProposalStatus.ACCEPTED and proposal.status != RuleProposalStatus.ACCEPTED:
        await _apply_rule(session, proposal)
    proposal.status = body.status
    await session.commit()
    return proposal
