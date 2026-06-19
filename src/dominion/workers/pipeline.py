"""generate_one_scene — the bounded, deterministic spine (DESIGN §4-5).

Order is fixed code, not an LLM decision: draft the spine -> run only the tagged enrichment passes
-> persist as pending_review -> attach advisory reviewer flags. A failed enrichment pass lands the
partial spine and flags it; it never fails the job or blocks the inbox (DESIGN §4). Then the process
exits — nothing keeps running, so there's nothing to re-verify on the next boot.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import SceneStatus, Severity
from dominion.shared.models import Critique, Job, Scene
from dominion.workers.budget import BudgetExceeded
from dominion.workers.context import assemble_context
from dominion.workers.router import passes_for, reviewers_for
from dominion.workers.specialists.base import PassError
from dominion.workers.specialists.drafter import drafter
from dominion.workers.stat_render import render_stat_blocks


async def generate_one_scene(session: AsyncSession, job: Job) -> Scene:
    ctx = await assemble_context(session, job)
    # A revision job targets an existing scene; the new prose becomes a new version of it.
    prior = await session.get(Scene, job.target_scene_id) if job.target_scene_id is not None else None

    # 1) the spine (POV-voiced) — or a rewrite, if ctx carries revision feedback
    prose = await drafter.run(ctx.prior_prose, ctx)
    passes_run: list[str] = ["drafter"]

    # 2) tagged enrichment passes, fixed order; PassError lands partial + flag (never block). A
    # BudgetExceeded mid-pass aborts the rest — the spine already exists, so we save it (DESIGN §10).
    pass_failures: list[tuple[str, str]] = []
    budget_exceeded = False
    try:
        for specialist in passes_for(ctx.tags):
            try:
                prose = await specialist.run(prose, ctx)
                passes_run.append(specialist.name)
            except PassError as exc:
                pass_failures.append((specialist.name, str(exc)))
    except BudgetExceeded:
        budget_exceeded = True

    # 3) persist. Normal -> pending_review (draft v1 / revision v+1). Budget-exceeded -> a quarantined
    # DRAFT that never enters the inbox and never retires the prior good version (DESIGN §10).
    # The drafter emits ```stat``` markers (values only); deterministic code draws the aligned box, so
    # the model never does monospace column math. The marker form stays in agent_original (edit-safe +
    # training capture); the rendered box goes in prose, which is what the inbox shows.
    rendered_prose = render_stat_blocks(prose)
    scene = Scene(
        chapter_id=ctx.chapter_id,
        scene_no=ctx.scene_no,
        version=(prior.version + 1) if prior is not None else 1,
        parent_scene_id=prior.id if prior is not None else None,
        status=SceneStatus.DRAFT if budget_exceeded else SceneStatus.PENDING_REVIEW,
        prose=rendered_prose,
        prose_source="agent",
        agent_original=prose,            # marker form preserved for training capture (DESIGN §11)
        passes_run=passes_run,
        token_count=ctx.budget.used,
        model=settings.draft_model,
    )
    session.add(scene)
    await session.flush()                # get scene.id for the critique rows

    for name, msg in pass_failures:
        session.add(Critique(
            scene_id=scene.id, version=scene.version, reviewer=name,
            severity=Severity.WARN, note=f"enrichment pass failed: {msg}",
        ))

    # 4) advisory reviewers (read-only) -> Critique rows. Never changes status, never blocks. Skipped
    # once the budget is spent; a reviewer that tips it over downgrades the scene to a partial DRAFT.
    if not budget_exceeded:
        try:
            for reviewer in reviewers_for(ctx.tags):
                for flag in await reviewer.review(prose, ctx):
                    session.add(Critique(
                        scene_id=scene.id, version=scene.version, reviewer=flag.reviewer,
                        severity=flag.severity, note=flag.note, payload=flag.payload,
                    ))
        except BudgetExceeded:
            budget_exceeded = True
            scene.status = SceneStatus.DRAFT

    # 5) finalize: a budget-exceeded scene is flagged and leaves the prior version intact; otherwise a
    # revision supersedes its parent (DESIGN §10, §3).
    if budget_exceeded:
        session.add(Critique(
            scene_id=scene.id, version=scene.version, reviewer="budget", severity=Severity.HARD,
            note=f"token budget exceeded (used {ctx.budget.used} / {ctx.budget.max_tokens}); "
                 "saved partial draft",
        ))
    elif prior is not None:
        prior.status = SceneStatus.SUPERSEDED

    return scene
