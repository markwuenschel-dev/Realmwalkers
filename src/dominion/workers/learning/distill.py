"""Distill the author's edits into proposed voice/dialogue rules (LEARNING_FROM_EDITS Tier 3).

A periodic, human-gated job: a review-model pass reads recent before→after pairs (Tier 1's `EditPair`)
for one POV and PROPOSES durable style rules — "trims filter verbs (saw/felt/noticed)", "dialogue tags
stay 'said'/'asked'". The author approves/edits/rejects each (the `learning` router); an accepted rule
is appended to that POV's `PovProfile.voice_spec`, which the drafter reads fresh on the next scene.

Like the planner and the advisory reviewers (DESIGN §6), parsing is tolerant — a malformed model
response yields no proposals rather than an error. The one bounded failure is a hung call: the endpoint
runs synchronously, so a timeout raises (the router maps it to 504) instead of spinning the browser.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import RuleKind
from dominion.shared.models import Chapter, EditPair, Scene
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.reviewers.base import parse_json_objects

_DISTILL_MAX_TOKENS = 1500

_SYSTEM = (
    "You study how a novelist edits their drafting assistant's prose. Given BEFORE (the assistant's "
    "draft) and AFTER (the author's edit) pairs for ONE point-of-view character, infer the author's "
    "DURABLE style preferences and state each as a short, imperative rule the assistant can follow on "
    "the next draft — e.g. 'trim filter verbs (saw/felt/noticed)', 'keep dialogue tags to said/asked', "
    "'cut throat-clearing openings'. Propose only patterns you see REPEATED or clearly intentional; "
    "ignore one-off factual or continuity fixes (those are not style). If nothing durable stands out, "
    "propose nothing."
)


async def load_recent_pairs(session: AsyncSession, *, book_id: uuid.UUID, pov: str, limit: int) -> list[EditPair]:
    """The POV's most-recent agent→human edit pairs in this book (newest first), joined through the
    scene's chapter (EditPair has no book_id of its own). Drops empty or no-op pairs so the model only
    sees real edits."""
    rows = (
        (
            await session.execute(
                select(EditPair)
                .join(Scene, EditPair.scene_id == Scene.id)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(Chapter.book_id == book_id, EditPair.pov == pov)
                .order_by(EditPair.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        p
        for p in rows
        if (p.agent_text or "").strip() and (p.human_text or "").strip() and p.agent_text != p.human_text
    ]


async def candidate_povs(session: AsyncSession, *, book_id: uuid.UUID) -> list[str]:
    """Distinct POVs that have at least one edit pair in this book — the set worth distilling."""
    rows = (
        (
            await session.execute(
                select(EditPair.pov)
                .join(Scene, EditPair.scene_id == Scene.id)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(Chapter.book_id == book_id, EditPair.pov.is_not(None))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return [p for p in rows if p]


def _prompt(pairs: list[EditPair], *, pov: str, max_chars: int) -> str:
    parts = [f"POV character: {pov}", "EDIT PAIRS (BEFORE = assistant draft, AFTER = author's edit):"]
    for i, p in enumerate(pairs, start=1):
        before = (p.agent_text or "")[:max_chars]
        after = (p.human_text or "")[:max_chars]
        parts.append(f"--- pair {i} ---\nBEFORE:\n{before}\n\nAFTER:\n{after}")
    parts.append(
        "\nReturn ONLY a JSON array (no prose, no code fences). Each item: "
        '{"kind": "voice"|"dialogue", "rule": str (imperative, one line), '
        '"why": str (the pattern you saw, one short sentence)}. Empty array [] if nothing durable stands out.'
    )
    return "\n\n".join(parts)


def _coerce(item: dict[str, object]) -> dict[str, str] | None:
    """Normalize one model-proposed rule; drop unusable items (mirrors planner._coerce_beat)."""
    rule = str(item.get("rule", "")).strip()
    if not rule:
        return None
    kind = str(item.get("kind", "")).strip().lower()
    if kind not in (RuleKind.VOICE, RuleKind.DIALOGUE):
        kind = RuleKind.VOICE
    why = str(item.get("why", "")).strip()
    return {"kind": kind, "rule": rule, "rationale": why}


async def propose_rules(
    pairs: list[EditPair], *, pov: str, budget: TokenBudget | None = None, time_budget_s: int
) -> list[dict[str, str]]:
    """One bounded review-model call → a list of normalized rule dicts (possibly empty). Tolerant of a
    malformed response; raises TimeoutError only if the call exceeds `time_budget_s` (caller → 504)."""
    if not pairs:
        return []
    try:
        raw, _usage = await asyncio.wait_for(
            llm.complete(
                model=settings.review_model,
                system=_SYSTEM,
                user=_prompt(pairs, pov=pov, max_chars=settings.distill_pair_max_chars),
                max_tokens=_DISTILL_MAX_TOKENS,
                budget=budget or TokenBudget(max_tokens=settings.scene_token_budget),
            ),
            timeout=time_budget_s,
        )
    except TimeoutError:
        raise TimeoutError(f"rule distillation exceeded {time_budget_s}s — try again") from None
    rules: list[dict[str, str]] = []
    for item in parse_json_objects(raw):
        coerced = _coerce(item)
        if coerced is not None:
            rules.append(coerced)
    return rules
