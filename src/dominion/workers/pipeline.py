"""generate_one_scene — the bounded, deterministic spine (DESIGN §4-5).

Order is fixed code, not an LLM decision: draft the spine -> run only the tagged enrichment passes
-> persist as pending_review -> attach advisory reviewer flags. A failed enrichment pass lands the
partial spine and flags it; it never fails the job or blocks the inbox (DESIGN §4). Then the process
exits — nothing keeps running, so there's nothing to re-verify on the next boot.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import agent_auto_run
from dominion.shared.config import settings
from dominion.shared.enums import DraftStage, SceneStatus, Severity
from dominion.shared.models import Critique, DraftAttempt, Job, Scene
from dominion.workers import progress, telemetry, telemetry_db
from dominion.workers.budget import BudgetExceeded
from dominion.workers.context import assemble_context
from dominion.workers.length import guard as length_guard
from dominion.workers.router import passes_for, reviewers_for
from dominion.workers.specialists.base import PassError
from dominion.workers.specialists.drafter import drafter
from dominion.workers.stat_render import render_stat_blocks

log = structlog.get_logger()

# Enrichment specialist names → their DraftStage (for provenance). An unmapped pass records its name.
_ENRICH_STAGE = {
    "combat": DraftStage.ENRICHMENT_COMBAT,
    "sensory": DraftStage.ENRICHMENT_SENSORY,
    "dialogue": DraftStage.ENRICHMENT_DIALOGUE,
}


async def generate_one_scene(session: AsyncSession, job: Job) -> Scene:
    # Capture once: progress is keyed by job id and reported live to GET /jobs/status (best-effort).
    jid = str(job.id)
    progress.set_phase(jid, "preparing")
    ctx = await assemble_context(session, job)

    # Telemetry: collect every stage's model calls (drafter / enrichment / length / reviewers) into one
    # sink tagged with this scene's dimensions, then persist once before returning — so scene drafting,
    # the most expensive stage, shows up in the Desk telemetry alongside the scene-packet derive.
    sink = telemetry.TelemetrySink()

    def _tctx(stage: str) -> telemetry.CallContext:
        return telemetry.CallContext(
            sink=sink,
            stage=stage,
            book_id=str(ctx.book_id),
            chapter_id=str(ctx.chapter_id),
            scene_no=ctx.scene_no,
        )

    # A revision job targets an existing scene; the new prose becomes a new version of it.
    prior = await session.get(Scene, job.target_scene_id) if job.target_scene_id is not None else None

    # Provenance: every stage that changes the prose is preserved as a DraftAttempt (written after the
    # scene exists, so each row carries scene_id). (stage, prose, word_count, model).
    attempts: list[tuple[str, str, int, str | None]] = []

    def record(stage: str, text: str, model: str | None) -> None:
        attempts.append((stage, text, length_guard.count_words(text), model))

    # 1) the spine (POV-voiced) — or a rewrite, if ctx carries revision feedback
    progress.set_phase(jid, "drafting prose")
    with telemetry.call_context(_tctx("drafter")):
        prose = await drafter.run(ctx.prior_prose, ctx)
    passes_run: list[str] = ["drafter"]
    record(DraftStage.DRAFTER_RAW, prose, settings.draft_model)

    # 2) tagged enrichment passes, fixed order; PassError lands partial + flag (never block). A
    # BudgetExceeded mid-pass aborts the rest — the spine already exists, so we save it (DESIGN §10).
    pass_failures: list[tuple[str, str]] = []
    budget_exceeded = False
    try:
        if agent_auto_run("enrich_model"):
            with telemetry.call_context(_tctx("enrichment")):
                for specialist in passes_for(ctx.tags):
                    try:
                        progress.set_phase(jid, f"enriching · {specialist.name}")
                        prose = await specialist.run(prose, ctx)
                        passes_run.append(specialist.name)
                        record(_ENRICH_STAGE.get(specialist.name, specialist.name), prose, settings.enrich_model)
                    except PassError as exc:
                        pass_failures.append((specialist.name, str(exc)))
        else:
            log.info("pipeline.enrichment_skipped", reason="enrich_model auto_run disabled")
    except BudgetExceeded:
        budget_exceeded = True

    # 3) Length Guard — count words, compress/expand against the ScenePacket budget. Runs on the same
    # job budget; a BudgetExceeded here just skips the rewrite and persists what we have.
    length_status: str | None = None
    word_count = length_guard.count_words(prose)
    length_critique: tuple[str, str] | None = None
    if not budget_exceeded and ctx.word_budget:
        progress.set_phase(jid, "length guard")
        try:
            with telemetry.call_context(_tctx("length")):
                guard_result = await length_guard.apply_length_guard(
                    prose,
                    word_budget=ctx.word_budget,
                    scene_contract=ctx.scene_contract,
                    budget=ctx.budget,
                )
            prose = guard_result.prose
            word_count = guard_result.word_count
            length_status = guard_result.length_status
            length_critique = guard_result.critique
            for stage_rec in guard_result.stages:
                record(stage_rec.stage, stage_rec.prose, stage_rec.model)
            if guard_result.quarantine:
                budget_exceeded = True  # reuse the quarantine path: persist as DRAFT, keep prior
        except BudgetExceeded:
            budget_exceeded = True

    # 4) persist. Normal -> pending_review (draft v1 / revision v+1). Budget-exceeded/quarantine -> a
    # DRAFT that never enters the inbox and never retires the prior good version (DESIGN §10).
    # The drafter emits ```stat``` markers (values only); deterministic code draws the aligned box, so
    # the model never does monospace column math. The marker form stays in agent_original (edit-safe +
    # training capture); the rendered box goes in prose, which is what the inbox shows.
    progress.set_phase(jid, "saving draft")
    rendered_prose = render_stat_blocks(prose)
    scene = Scene(
        chapter_id=ctx.chapter_id,
        scene_no=ctx.scene_no,
        version=(prior.version + 1) if prior is not None else 1,
        parent_scene_id=prior.id if prior is not None else None,
        status=SceneStatus.DRAFT if budget_exceeded else SceneStatus.PENDING_REVIEW,
        scene_packet_id=ctx.scene_packet_id,
        word_count=word_count,
        length_status=length_status,
        prose=rendered_prose,
        prose_source="agent",
        agent_original=prose,  # marker form preserved for training capture (DESIGN §11)
        passes_run=passes_run,
        token_count=ctx.budget.total_input + ctx.budget.total_output,
        model=settings.draft_model,
    )
    session.add(scene)
    await session.flush()  # get scene.id for the critique + draft-attempt rows

    # Preserve every prose stage + the final rendered output (provenance).
    record(DraftStage.FINAL_RENDERED, rendered_prose, settings.draft_model)
    for stage, text, wc, model in attempts:
        session.add(
            DraftAttempt(
                job_id=job.id,
                scene_id=scene.id,
                scene_packet_id=ctx.scene_packet_id,
                stage=stage,
                prose=text,
                word_count=wc,
                model=model,
            )
        )

    if length_critique is not None:
        sev, note = length_critique
        session.add(
            Critique(
                scene_id=scene.id,
                scene_packet_id=ctx.scene_packet_id,
                version=scene.version,
                reviewer="length",
                severity=sev,
                note=note,
            )
        )

    for name, msg in pass_failures:
        session.add(
            Critique(
                scene_id=scene.id,
                scene_packet_id=ctx.scene_packet_id,
                version=scene.version,
                reviewer=name,
                severity=Severity.WARN,
                note=f"enrichment pass failed: {msg}",
            )
        )

    # 4) advisory reviewers (read-only) -> Critique rows. They're independent, so run them concurrently
    # and collapse N sequential review-model round-trips into ~one. Never changes status, never blocks.
    # Skipped once the budget is spent; a reviewer that tips it over downgrades the scene to a partial
    # DRAFT, while any other reviewer error fails the job (the spine is already persisted). Critiques are
    # still added in reviewer order (continuity first). NOTE: parallel calls each charge on their own
    # response, so a scene near its ceiling can overshoot a little more than the old serial path did —
    # acceptable for these cheap, advisory calls that run after the costly drafting work.
    if agent_auto_run("review_model") and not budget_exceeded:
        reviewers = reviewers_for(ctx.tags)
        progress.set_phase(jid, "reviewing")

        def _reviewer_label(reviewer: Any) -> str:
            return getattr(reviewer, "name", type(reviewer).__name__)

        async def _review_one(reviewer: Any) -> list[Any]:
            from dominion.shared.reviewer_telemetry import reviewer_telemetry_stage

            stage = reviewer_telemetry_stage(_reviewer_label(reviewer))
            with telemetry.call_context(_tctx(stage)):
                return await reviewer.review(prose, ctx)

        results = await asyncio.gather(*(_review_one(reviewer) for reviewer in reviewers), return_exceptions=True)
        for reviewer, result in zip(reviewers, results, strict=True):
            if isinstance(result, BudgetExceeded):
                budget_exceeded = True
                scene.status = SceneStatus.DRAFT
            elif isinstance(result, BaseException):
                # Advisory reviewers must never fail the job or discard the drafted spine (a raise here
                # propagates to run_once, which rolls the whole scene back). Land a flag like a failed
                # enrichment pass and keep the good prose — same philosophy as PassError above.
                session.add(
                    Critique(
                        scene_id=scene.id,
                        scene_packet_id=ctx.scene_packet_id,
                        version=scene.version,
                        reviewer=_reviewer_label(reviewer),
                        severity=Severity.WARN,
                        note=f"reviewer failed: {result}",
                    )
                )
            else:
                for flag in result:
                    session.add(
                        Critique(
                            scene_id=scene.id,
                            scene_packet_id=ctx.scene_packet_id,
                            version=scene.version,
                            reviewer=flag.reviewer,
                            severity=flag.severity,
                            note=flag.note,
                            payload=flag.payload,
                        )
                    )

    # 5) finalize: a budget-exceeded scene is flagged and leaves the prior version intact; otherwise a
    # revision supersedes its parent (DESIGN §10, §3).
    if budget_exceeded:
        session.add(
            Critique(
                scene_id=scene.id,
                scene_packet_id=ctx.scene_packet_id,
                version=scene.version,
                reviewer="budget",
                severity=Severity.HARD,
                note=f"token budget exceeded (used {ctx.budget.used} / {ctx.budget.max_tokens}); saved partial draft",
            )
        )
    elif prior is not None:
        prior.status = SceneStatus.SUPERSEDED

    log.info(
        "scene.cache_summary",
        scene=str(scene.id),
        total_tokens=ctx.budget.used,
        cache_creation_tokens=ctx.budget.total_cache_creation,
        cache_read_tokens=ctx.budget.total_cache_read,
        cache_hit_ratio=round(ctx.budget.cache_hit_ratio, 3),
        cache_tokens_saved=ctx.budget.cache_tokens_saved,
    )
    progress.set_cache_stats(
        str(job.id),
        cache_hit_ratio=round(ctx.budget.cache_hit_ratio, 3),
        total_cache_read_tokens=ctx.budget.total_cache_read,
        total_cache_creation_tokens=ctx.budget.total_cache_creation,
        cache_tokens_saved=ctx.budget.cache_tokens_saved,
    )
    from dominion.workers.telemetry_settings import telemetry_settings_snapshot

    telemetry_db.persist_sink(
        session,
        sink,
        run_id=job.run_id,
        book_id=ctx.book_id,
        chapter_id=ctx.chapter_id,
        settings_snapshot=telemetry_settings_snapshot(),
    )
    return scene
