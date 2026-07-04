"""Rolling summaries — per-POV (drafter) + omniscient (planner/reviewer) (DESIGN §7).

Derived from the FINAL (possibly hand-edited) approved text. One row per (book, scope, pov), folded
forward on each approval via a cheap review-model call.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import quality_effort, quality_temperature
from dominion.shared.config import settings
from dominion.shared.models import Chapter, Scene, Summary
from dominion.workers import telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.llm_escalation import complete_with_rate_limit_fallback

_SUMMARY_MAX_TOKENS = 600


async def pov_summary(
    session: AsyncSession, *, book_id: uuid.UUID, pov: str, up_to_scene_id: uuid.UUID | None = None
) -> str | None:
    """What THIS character knows: their accumulated rolling summary (knowledge-asymmetry)."""
    # limit(1): a duplicate (book, pov) summary row would otherwise raise MultipleResultsFound and
    # fail every draft for this POV before it begins.
    return (
        await session.execute(
            select(Summary.rolling_summary)
            .where(Summary.book_id == book_id, Summary.scope == "pov", Summary.pov == pov)
            .order_by(Summary.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def refresh_on_approval(session: AsyncSession, *, scene_id: uuid.UUID) -> None:
    """Fold the approved scene into the POV summary and the omniscient summary."""
    scene = await session.get(Scene, scene_id)
    if scene is None or not scene.prose:
        return
    chapter = await session.get(Chapter, scene.chapter_id)
    if chapter is None:
        return
    # Telemetry: both fold-forward calls (POV + omniscient) roll up under one "summary" run row, so
    # memory regeneration is visible in the Desk telemetry. A fresh run_id per refresh keeps each
    # approval its own row.
    sink = telemetry.TelemetrySink()
    with telemetry.call_context(
        telemetry.CallContext(
            sink=sink,
            stage="summary",
            book_id=str(chapter.book_id),
            chapter_id=str(scene.chapter_id),
        )
    ):
        await _upsert(
            session,
            book_id=chapter.book_id,
            scope="pov",
            pov=chapter.pov,
            scene=scene,
            lens=f"what {chapter.pov} has personally experienced and knows",
        )
        await _upsert(
            session,
            book_id=chapter.book_id,
            scope="omniscient",
            pov=None,
            scene=scene,
            lens="the whole story so far, across all viewpoints",
        )
    telemetry_db.persist_sink(session, sink, run_id=uuid.uuid4(), book_id=chapter.book_id, chapter_id=scene.chapter_id)
    await session.flush()


async def _upsert(
    session: AsyncSession, *, book_id: uuid.UUID, scope: str, pov: str | None, scene: Scene, lens: str
) -> None:
    row = (
        await session.execute(
            select(Summary)
            .where(Summary.book_id == book_id, Summary.scope == scope, Summary.pov == pov)
            .order_by(Summary.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    previous = row.rolling_summary if row else None
    updated = await _summarize(previous, scene.prose or "", lens)
    if row is None:
        session.add(Summary(book_id=book_id, scope=scope, pov=pov, rolling_summary=updated, up_to_scene_id=scene.id))
    else:
        row.rolling_summary = updated
        row.up_to_scene_id = scene.id


async def _summarize(previous: str | None, scene_prose: str, lens: str) -> str:
    system = (
        f"You maintain a running story summary capturing {lens}. "
        "Keep it under 400 words, plot-relevant, and in present narrative continuity."
    )
    user = (
        f"Existing summary:\n{previous or '(none yet)'}\n\n"
        f"New scene just approved:\n{scene_prose}\n\n"
        "Rewrite the running summary to fold in the new scene. Output only the summary."
    )
    # Budget must fit a whole scene of *input*: a full hand-written/imported scene can be 8k+ tokens,
    # well past a tight cap. Size it to the per-scene budget — folding a scene shouldn't cost more
    # than drafting one — so long authored scenes (e.g. the seed import) fold instead of aborting.
    text, _usage = await complete_with_rate_limit_fallback(
        setting_key="review_model",
        model=settings.review_model,
        system=system,
        user=user,
        max_tokens=_SUMMARY_MAX_TOKENS,
        budget=TokenBudget(max_tokens=settings.scene_token_budget),
        expect_cache=False,
        temperature=quality_temperature("review_model"),
        effort=quality_effort("review_model"),
    )
    return text.strip()
