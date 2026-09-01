"""End-to-end chapter-run harness: drive a whole multi-scene chapter through the REAL serial stage
machine with the LLM mocked (audit candidate C16).

Today's suite tests the run stage machine one boundary at a time (``tests/test_run_orchestration.py``
drives the pure ``run_stages`` decisions) and drives at most a single scene job
(``tests/test_worker_db.py``). Nothing exercises the CROSS-STAGE flow the production engine actually
runs: book -> chapter -> production run -> sequential scene drafting -> per-scene timeline hand-off
-> chapter assembly gating. This module is that safety net — a characterization test of CURRENT
behavior that later makes the C2 ``production_sequence`` god-module split provable.

It reuses the established seams verbatim:
- ``db_factory`` (Postgres + hash embeddings) from ``tests/conftest.py``.
- ``worker.run_once(session_factory=db_factory)`` to draft ONE queued job at a time, exactly as
  ``tests/test_worker_db.py`` drives the worker.
- the real production driver: ``production.create_production_run`` /
  ``queue_draft_jobs_for_missing_sequence_scenes`` / ``assemble_run`` — the same calls the
  ``/production-runs/.../draft-missing-scenes`` and ``/assemble`` routers make.
- the LLM is mocked at ``llm.complete`` (the drafter's seam, per ``tests/test_drafter.py``); the
  optional enrichment + reviewer lanes are switched off via the ``agent_auto_run`` policy toggle so
  the ONLY model call per scene is the deterministic drafter — no non-determinism leaks into the
  cross-stage assertions.

Cross-stage invariants asserted (none catchable by the per-stage unit tests):
1. Assembly GATING — with no prose the run refuses to assemble (no ``chapter_draft`` artifact, parks
   in ``waiting_for_scene_drafts``); once every sequence scene has prose, assembly proceeds and the
   ``chapter_draft`` + ``chapter_draft_qa`` artifacts appear. "Assembly only proceeds when the
   sequence gating says so."
2. Sequential SCENE ORDERING — the driver queues scenes strictly one at a time in dependency order
   (scene N+1 is not queued until scene N has prose): queued order is [1, 2, 3].
3. Stage HAND-OFF — queueing a draft moves the run to ``drafting_scenes``; persisting the scene moves
   it to ``scene_qa`` via the live ``DraftRunTimeline`` update.
4. Timeline STATE HAND-OFF — the ``DraftRunTimeline`` accumulates one entry per drafted scene and
   tracks the current scene, so each scene is handed the prior scene's state (not restarted).
5. Assembled-chapter ORDERING — the ``chapter_draft`` body lists scenes in scene_no order and each
   scene's prose maps to its own scene (no misorder / duplication across the assembly join).
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select

from dominion.shared.enums import BeatStatus, IssueStatus, ProductionRunStatus, SceneStatus
from dominion.shared.models import (
    Artifact,
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    Issue,
    Job,
    ProductionRun,
    Scene,
    ScenePacket,
)
from dominion.workers import llm, pipeline, production, production_sequence, run_stages, worker
from dominion.workers.budget import Usage

SCENE_COUNT = 3

# Mirrors production_sequence.assemble_run's "still open" set — an Issue in any of these statuses
# blocks a run from reaching final_ready.
_OPEN_ISSUE_STATUSES = {
    IssueStatus.PROPOSED,
    IssueStatus.ACCEPTED,
    IssueStatus.REPAIR_QUEUED,
    IssueStatus.REPAIRED,
    IssueStatus.ESCALATED,
}


# --- deterministic canned LLM -----------------------------------------------------------------------


def _canned_complete():
    """A stand-in for ``llm.complete`` that returns deterministic prose sized to the drafter's own
    length instruction and tagged with the scene's marker, so each scene's prose is distinct and
    lands WITHIN its word budget (no length-guard rewrite call is triggered)."""

    async def fake_complete(**kwargs):
        user = str(kwargs.get("user") or "")
        budget = kwargs["budget"]
        budget.charge(Usage(64, 64))
        # Size to the drafter's "Aim for about N words." instruction; every seed carries a word
        # budget, so the instruction is always present. Default is a safe mid-range fallback.
        target_match = re.search(r"about (\d+) words", user)
        target = int(target_match.group(1)) if target_match else 700
        # The beat text carries a MARK<n> tag -> a unique opening keeps scene openings distinct.
        marker_match = re.search(r"MARK(\d+)", user)
        marker = marker_match.group(1) if marker_match else "0"
        opening = f"Scene {marker} opens on the breach and Mara moves."
        filler_needed = max(target - len(opening.split()), 0)
        prose = (opening + " " + " ".join(["forward"] * filler_needed)).strip()
        return prose, Usage(64, 64)

    return fake_complete


# --- seed -------------------------------------------------------------------------------------------


async def _seed_full_chapter(factory) -> uuid.UUID:
    """Seed a book + chapter + APPROVED chapter packet with SCENE_COUNT seeds, and — for every scene —
    an approved Beat and an approved ScenePacket (the preconditions the production drafting gate
    requires for each sequence scene). No scenes are pre-drafted: the run drives all drafting.

    Returns the chapter id. Budgets are explicit and generous so the arithmetic is trivially
    consistent (the draft-readiness budget gate never trips) and each scene's prose fits its window.
    """
    async with factory() as s:
        book = Book(title="Realmwalkers")
        s.add(book)
        await s.flush()

        chapter = Chapter(book_id=book.id, chapter_no=7, pov="Mara", title="Signal Fire")
        s.add(chapter)
        await s.flush()

        seeds = [
            {
                "seed_id": str(uuid.uuid4()),
                "scene_no": n,
                "scene_job": f"Scene {n} pushes Mara one step further.",
                "required_beats": [f"Beat {n} lands."],
                "forbidden_beats": [],
                # Explicit small budget per scene: 3 * hard_max(=1120) = 3360 <= chapter hard max 6000.
                "word_budget": {"target": 700},
            }
            for n in range(1, SCENE_COUNT + 1)
        ]
        packet = ChapterPacket(
            book_id=book.id,
            chapter_id=chapter.id,
            status="approved",
            confidence="green",
            body={
                "chapter_no": chapter.chapter_no,
                "chapter_job": "Push Mara through the breach to the relay tower.",
                "one_sentence_spine": "Mara chooses speed over certainty.",
                "entry_state": "Mara is pinned at the breach mouth.",
                "exit_state": "Mara reaches the relay tower.",
                "chapter_target_words": 6000,
                "chapter_max_words": 6000,
                "scene_seeds": seeds,
            },
            open_questions={"items": []},
        )
        s.add(packet)
        await s.flush()

        for n, seed in enumerate(seeds, start=1):
            beat = Beat(
                chapter_id=chapter.id,
                scene_seed_id=uuid.UUID(seed["seed_id"]),
                scene_no=n,
                # MARK<n> tag lets the canned LLM produce a scene-specific opening.
                beat_text=f"MARK{n}. Mara advances through stage {n} of the breach.",
                characters_present=["Mara"],
                tags=[],
                status=BeatStatus.APPROVED,
            )
            s.add(beat)
            await s.flush()

            scene_packet = ScenePacket(
                book_id=book.id,
                chapter_id=chapter.id,
                chapter_packet_id=packet.id,
                scene_seed_id=uuid.UUID(seed["seed_id"]),
                scene_no=n,
                status="approved",
                qa_verdict="approve",
                body={
                    "scene_no": n,
                    "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
                    "learned_during_scene": {
                        "reader_must_learn": [],
                        "reader_may_learn": [],
                        "reader_may_infer_only": [],
                    },
                    "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
                },
                source_hash=f"seed-{n}",
            )
            s.add(scene_packet)
            await s.flush()
            beat.scene_packet_id = scene_packet.id
            await s.flush()

        await s.commit()
        return chapter.id


# --- the harness ------------------------------------------------------------------------------------


async def test_full_chapter_run_drives_scenes_in_order_and_gates_assembly(db_factory, monkeypatch):
    # Deterministic drafter; enrichment + reviewer lanes off so the drafter is the only model call.
    monkeypatch.setattr(llm, "complete", _canned_complete())
    monkeypatch.setattr(pipeline, "agent_auto_run", lambda key: False)

    chapter_id = await _seed_full_chapter(db_factory)

    # --- create the production run (builds sequence + issue snapshot, attempts assembly) -----------
    async with db_factory() as s:
        run = await production.create_production_run(s, chapter_id=chapter_id, auto_triage=False)
        run_id = run.id
        await s.commit()

    # INVARIANT 1a — assembly GATE refuses with zero prose: no chapter_draft, parked at the drafting
    # boundary (the ch1 failure mode was assembling a half-empty chapter anyway).
    async with db_factory() as s:
        run = await s.get(ProductionRun, run_id)
        assert run.current_stage == run_stages.STAGE_WAITING_FOR_SCENE_DRAFTS
        types0 = await _artifact_types(s, run_id)
        assert "chapter_draft" not in types0
        assert "chapter_draft_qa" not in types0

    # --- drive the whole chapter: queue ONE scene, draft it, repeat --------------------------------
    queued_scene_order: list[int] = []
    for _iteration in range(SCENE_COUNT + 3):  # generous cap; expect exactly SCENE_COUNT drafts
        async with db_factory() as s:
            run = await s.get(ProductionRun, run_id)
            job_ids = await production.queue_draft_jobs_for_missing_sequence_scenes(s, run)
            if job_ids:
                job = await s.get(Job, job_ids[0])
                queued_scene_order.append(job.scene_no)
                # INVARIANT 3a — queueing a draft advances the run to drafting_scenes.
                assert run.current_stage == run_stages.STAGE_DRAFTING_SCENES
            await s.commit()

        if not job_ids:
            break

        drafted = await worker.run_once(session_factory=db_factory)
        assert drafted is True

        # INVARIANT 3b — persisting the scene hands the run to scene_qa via the timeline update.
        async with db_factory() as s:
            run = await s.get(ProductionRun, run_id)
            assert run.current_stage == run_stages.STAGE_SCENE_QA

    # INVARIANT 2 — scenes were queued strictly one at a time, in dependency order.
    assert queued_scene_order == list(range(1, SCENE_COUNT + 1))

    # Every sequence scene now has exactly one drafted, pending-review scene.
    async with db_factory() as s:
        scenes = (
            (await s.execute(select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.scene_no)))
            .scalars()
            .all()
        )
        assert [sc.scene_no for sc in scenes] == list(range(1, SCENE_COUNT + 1))
        assert all(sc.status == SceneStatus.PENDING_REVIEW for sc in scenes)
        assert all((sc.prose or "").strip() for sc in scenes)

        # INVARIANT 4 — the live timeline accumulated one entry per scene and tracks the last one.
        timeline = await production_sequence.latest_draft_timeline(s, run_id)
        assert timeline is not None
        assert timeline.current_scene_no == SCENE_COUNT
        drafted_with_prose = [d for d in (timeline.drafted_scenes or []) if d.get("scene_id")]
        assert len(drafted_with_prose) == SCENE_COUNT

    # --- assemble the now-complete chapter ---------------------------------------------------------
    async with db_factory() as s:
        run = await s.get(ProductionRun, run_id)
        await production_sequence.assemble_run(s, run)
        await s.commit()

    # INVARIANT 1b — with all prose present the assembly gate OPENS: chapter_draft + chapter_draft_qa
    # exist and the run has advanced past the drafting boundary.
    async with db_factory() as s:
        run = await s.get(ProductionRun, run_id)
        assert run.current_stage != run_stages.STAGE_WAITING_FOR_SCENE_DRAFTS
        types1 = await _artifact_types(s, run_id)
        assert "chapter_draft" in types1
        assert "chapter_draft_qa" in types1

        # INVARIANT 5 — the assembled chapter lists scenes in order and each prose maps to its scene.
        chapter_draft = (
            (
                await s.execute(
                    select(Artifact)
                    .where(
                        Artifact.production_run_id == run_id,
                        Artifact.artifact_type == "chapter_draft",
                    )
                    .order_by(Artifact.version.desc(), Artifact.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one()
        )
        scene_rows = chapter_draft.body["scenes"]
        assert [row["scene_no"] for row in scene_rows] == list(range(1, SCENE_COUNT + 1))
        for row in scene_rows:
            # The canned prose opens with "Scene <n> opens ..." — proves scene N's prose landed in
            # scene N's row (no cross-scene misorder in the assembly join).
            assert f"scene {row['scene_no']} opens" in (row["prose"] or "").lower()


async def _artifact_types(session, run_id: uuid.UUID) -> set[str]:
    rows = (
        (await session.execute(select(Artifact.artifact_type).where(Artifact.production_run_id == run_id)))
        .scalars()
        .all()
    )
    return set(rows)


async def _seed_create_drive_assemble(db_factory, monkeypatch) -> uuid.UUID:
    """Seed a chapter, create the production run, drive every scene draft through the run, then
    assemble. No intermediate assertions — used by the final-state expectation test below. Returns
    the production run id."""
    monkeypatch.setattr(llm, "complete", _canned_complete())
    monkeypatch.setattr(pipeline, "agent_auto_run", lambda key: False)

    chapter_id = await _seed_full_chapter(db_factory)

    async with db_factory() as s:
        run = await production.create_production_run(s, chapter_id=chapter_id, auto_triage=False)
        run_id = run.id
        await s.commit()

    for _iteration in range(SCENE_COUNT + 3):
        async with db_factory() as s:
            run = await s.get(ProductionRun, run_id)
            job_ids = await production.queue_draft_jobs_for_missing_sequence_scenes(s, run)
            await s.commit()
        if not job_ids:
            break
        assert await worker.run_once(session_factory=db_factory) is True

    async with db_factory() as s:
        run = await s.get(ProductionRun, run_id)
        await production_sequence.assemble_run(s, run)
        await s.commit()

    return run_id


@pytest.mark.xfail(
    strict=True,
    reason=(
        "missing_scene issues snapshotted at run-creation are not cleared when scenes are drafted "
        "through the run — confirmed bug, fix in C2 lane"
    ),
)
async def test_fully_drafted_chapter_through_run_reaches_final_ready(db_factory, monkeypatch):
    """INTENDED end state (confirmed bug today): when every sequence scene is drafted THROUGH the
    production run, the stale ``missing_scene`` issues snapshotted at run-creation must be cleared so
    the fully-drafted chapter reaches ``final_ready`` / COMPLETED — not park in ``chapter_qa`` /
    WAITING_FOR_HUMAN behind issues that no longer describe reality.

    Strict xfail: the moment the C2 production-run issue-lifecycle fix lands, this xpasses and the
    strict marker turns that into a hard failure, forcing removal of the marker.
    """
    run_id = await _seed_create_drive_assemble(db_factory, monkeypatch)

    async with db_factory() as s:
        run = await s.get(ProductionRun, run_id)
        issues = (await s.execute(select(Issue).where(Issue.production_run_id == run_id))).scalars().all()
        open_missing_scene = [
            issue for issue in issues if issue.issue_kind == "missing_scene" and issue.status in _OPEN_ISSUE_STATUSES
        ]
        types = await _artifact_types(s, run_id)

        # The snapshotted missing_scene issues must be cleared once the scenes actually exist.
        assert open_missing_scene == []
        # And the run must reach its terminal success state with a final chapter artifact.
        assert run.current_stage == "final_ready"
        assert run.status == ProductionRunStatus.COMPLETED
        assert "final_chapter" in types


async def test_empty_queue_when_no_production_run(db_factory):
    """Sanity: with a seeded chapter but no queued jobs, the worker idles (mirrors test_worker_db)."""
    await _seed_full_chapter(db_factory)
    assert await worker.run_once(session_factory=db_factory) is False
