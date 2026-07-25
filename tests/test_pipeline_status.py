"""Coverage for the live pipeline-status endpoint (routers/pipeline.py + workers/pipeline_status.py)
and the sweeper heartbeat (workers/sweeper.py).

Seeds a book with production runs in several real states (running / waiting_for_human /
structural_repair_required / completed) plus queued/failed jobs and repair tasks/issues, then asserts
each PipelineStatusOut section is populated with the right items and that the pre-computed
reason/suggested_action strings are present. Direct handler calls with db_factory, mirroring
tests/test_production_runs.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from dominion.api.routers import pipeline as pipeline_router
from dominion.shared.models import (
    AgentEvent,
    AgentRun,
    Artifact,
    Book,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    DraftRunTimeline,
    Issue,
    Job,
    ProductionRun,
    RepairTask,
)


async def _chapter(s, book, no: int) -> Chapter:
    c = Chapter(book_id=book.id, chapter_no=no, pov="Mara", title=f"Chapter {no}")
    s.add(c)
    await s.flush()
    return c


async def _run(s, book, chapter, *, status: str, stage: str | None) -> ProductionRun:
    run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status=status, current_stage=stage)
    s.add(run)
    await s.flush()
    return run


async def _seed_pipeline(s) -> Book:
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()

    # --- NOW: a running run (with drafting progress), a RUNNING job, a RUNNING agent run -----------
    ch_now = await _chapter(s, book, 1)
    run_now = await _run(s, book, ch_now, status="running", stage="drafting_scenes")
    packet = ChapterPacket(
        book_id=book.id,
        chapter_id=ch_now.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": []},
        open_questions={"items": []},
    )
    s.add(packet)
    await s.flush()
    s.add(
        ChapterSequence(
            book_id=book.id,
            chapter_id=ch_now.id,
            chapter_packet_id=packet.id,
            status="approved",
            body={"scenes": [{"scene_no": 1}, {"scene_no": 2}]},
        )
    )
    s.add(
        DraftRunTimeline(
            production_run_id=run_now.id,
            chapter_id=ch_now.id,
            current_scene_no=1,
            drafted_scenes=[{"scene_no": 1}],
        )
    )
    s.add(
        Job(
            kind="draft",
            status="running",
            token_budget=1000,
            book_id=book.id,
            chapter_id=ch_now.id,
            chapter_no=1,
            scene_no=1,
            claimed_at=datetime.now(UTC),
        )
    )
    s.add(
        AgentRun(
            production_run_id=run_now.id,
            agent_name="repairer",
            agent_role="repair",
            status="running",
            stage="repair_execution",
            input_artifact_ids=[],
            started_at=datetime.now(UTC),
        )
    )

    # --- QUEUE: a queued job, an auto repair task, an approval repair task, a queued run -----------
    ch_q = await _chapter(s, book, 2)
    run_q = await _run(s, book, ch_q, status="queued", stage=None)
    s.add(
        Job(
            kind="draft",
            status="queued",
            token_budget=1000,
            book_id=book.id,
            chapter_id=ch_q.id,
            chapter_no=2,
            scene_no=1,
        )
    )
    s.add(
        RepairTask(
            production_run_id=run_q.id,
            chapter_id=ch_q.id,
            scene_no=1,
            repair_kind="prose_polish",
            authority_level="span_only",
            status="queued",
            instructions="tighten",
        )
    )
    s.add(
        RepairTask(
            production_run_id=run_q.id,
            chapter_id=ch_q.id,
            scene_no=2,
            repair_kind="scene_structural",
            authority_level="chapter_structural",
            status="queued",
            instructions="restructure",
        )
    )

    # --- WAITING ON HUMAN: a waiting run (not blocked), a waiting task, a proposed issue -----------
    ch_w = await _chapter(s, book, 3)
    run_w = await _run(s, book, ch_w, status="waiting_for_human", stage="waiting_for_scene_drafts")
    s.add(
        AgentEvent(
            production_run_id=run_w.id,
            event_type="assembly_refused",
            stage="waiting_for_scene_drafts",
            message="Chapter assembly refused: 1 of 2 sequence scenes have no prose.",
            payload_json={"reason": "missing_scene_prose"},
        )
    )
    s.add(
        RepairTask(
            production_run_id=run_w.id,
            chapter_id=ch_w.id,
            scene_no=1,
            repair_kind="prose_polish",
            authority_level="scene_local",
            status="waiting_for_human",
            instructions="fix",
            human_approved_at=datetime.now(UTC),
        )
    )
    s.add(
        Issue(
            production_run_id=run_w.id,
            chapter_id=ch_w.id,
            artifact_type="chapter_draft_qa",
            artifact_id=uuid.uuid4(),
            validator="dialogue",
            issue_kind="flat_dialogue",
            severity="warn",
            claim="Dialogue reads flat.",
            recommended_action="revise",
            status="proposed",
        )
    )

    # --- BLOCKED: a run parked in a structural-repair stage + a FAILED job -------------------------
    ch_b = await _chapter(s, book, 4)
    run_b = await _run(s, book, ch_b, status="waiting_for_human", stage="structural_repair_required")
    s.add(
        AgentEvent(
            production_run_id=run_b.id,
            event_type="structural_repair_required",
            stage="structural_repair_required",
            message="Chapter QA found structural blocking issues; prose repair is gated until they are fixed.",
            payload_json={"reason": "structural_blocking_issues"},
        )
    )
    s.add(
        Job(
            kind="draft",
            status="failed",
            token_budget=1000,
            book_id=book.id,
            chapter_id=ch_b.id,
            chapter_no=4,
            scene_no=3,
            last_error="LlmRateLimited: provider rate limit (429)",
        )
    )

    # --- COMPLETED: a completed run with a final_chapter artifact ----------------------------------
    ch_c = await _chapter(s, book, 5)
    run_c = await _run(s, book, ch_c, status="completed", stage="final_ready")
    s.add(
        Artifact(
            production_run_id=run_c.id,
            artifact_type="final_chapter",
            version=1,
            status="active",
            body={"final_chapter_status": "ready_for_review"},
            content_hash="abc",
        )
    )

    await s.flush()
    return book


async def test_pipeline_status_populates_every_section(db_factory):
    async with db_factory() as s:
        book = await _seed_pipeline(s)
        out = await pipeline_router.get_pipeline(book.id, s)

        # NOW ---------------------------------------------------------------------------------------
        assert len(out.now.jobs) == 1 and out.now.jobs[0].status == "running"
        assert out.now.jobs[0].scene_no == 1
        assert len(out.now.agent_runs) == 1 and out.now.agent_runs[0].stage == "repair_execution"
        assert len(out.now.runs) == 1
        now_run = out.now.runs[0]
        assert now_run.scenes_drafted == 1 and now_run.scenes_expected == 2
        assert now_run.reason == "Drafting scenes"
        assert out.now.drain_locked is False and out.now.repair_drain_locked is False

        # QUEUE -------------------------------------------------------------------------------------
        assert out.queue.serial is True
        assert "one at a time" in out.queue.note
        assert out.queue.jobs_queued >= 1
        assert out.queue.jobs and out.queue.jobs[0].position == 1
        assert len(out.queue.repair_tasks_auto) == 1 and out.queue.repair_tasks_auto[0].action_kind == "verify"
        assert len(out.queue.repair_tasks_approval) == 1
        approval = out.queue.repair_tasks_approval[0]
        assert approval.requires_human_approval is True and approval.action_kind == "approve_apply"
        assert len(out.queue.runs_queued) == 1

        # WAITING ON HUMAN --------------------------------------------------------------------------
        assert len(out.waiting_on_human.runs) == 1
        wr = out.waiting_on_human.runs[0]
        assert wr.action_kind == "draft_missing"
        assert wr.reason and "no prose" in wr.reason  # taken from the AgentEvent message
        assert wr.suggested_action == "Draft missing scenes"
        # the waiting_for_human task + the approval-gated queued task both surface here
        assert len(out.waiting_on_human.repair_tasks) == 2
        assert all(t.reason and t.suggested_action for t in out.waiting_on_human.repair_tasks)
        assert len(out.waiting_on_human.issues) == 1
        iss = out.waiting_on_human.issues[0]
        assert iss.action_kind == "decide_issue" and iss.status == "proposed" and iss.reason

        # BLOCKED -----------------------------------------------------------------------------------
        assert len(out.blocked.runs) == 1
        br = out.blocked.runs[0]
        assert br.current_stage == "structural_repair_required"
        assert br.action_kind == "align_scene_count"
        assert br.reason and "structural" in br.reason.lower()
        assert len(out.blocked.failed_jobs) == 1 and out.blocked.failed_jobs[0].last_error
        assert out.blocked.queue_paused is False

        # COMPLETED ---------------------------------------------------------------------------------
        assert len(out.completed.runs) == 1
        cr = out.completed.runs[0]
        assert cr.final_chapter_status == "ready_for_review"
        assert cr.status == "completed"

        # SWEEPER (section present; heartbeat exercised separately) ----------------------------------
        assert out.sweeper is not None
        assert out.sweeper.authority_ceiling  # config merged in


async def test_pipeline_status_unknown_book_404(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await pipeline_router.get_pipeline(uuid.uuid4(), s)
        assert exc.value.status_code == 404


async def test_sweeper_heartbeat_surfaces_in_status_and_endpoint(db_factory, monkeypatch):
    from dominion.workers import sweeper

    tick_at = datetime.now(UTC)
    monkeypatch.setattr(
        sweeper,
        "_heartbeat",
        {
            "last_tick_at": tick_at,
            "ran": True,
            "autonomy_enabled": True,
            "paused": False,
            "stale_runs_found": 2,
            "actions": [{"run_id": "r1", "kind": "repair_applied"}],
            "driving": ["r1", "r2"],
            "last_error": None,
        },
    )
    monkeypatch.setattr(sweeper, "_attempts", {"r1": 1})

    async with db_factory() as s:
        status = await sweeper.sweeper_status(s)
        assert status["ran"] is True
        assert status["driving"] == ["r1", "r2"]
        assert status["stale_runs_found"] == 2
        assert status["actions"] == [{"run_id": "r1", "kind": "repair_applied"}]
        assert status["attempts"] == {"r1": 1}
        assert status["interval_s"] == 120  # default config merged in

        book = Book(title="Realmwalkers")
        s.add(book)
        await s.flush()
        out = await pipeline_router.get_pipeline(book.id, s)
        assert out.sweeper.ran is True
        assert out.sweeper.driving == ["r1", "r2"]
        assert out.sweeper.stale_runs_found == 2
        assert out.sweeper.attempts == {"r1": 1}
        assert out.sweeper.last_tick_at is not None
