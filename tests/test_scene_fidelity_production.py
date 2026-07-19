"""Lane 5 — SceneFidelity production triage (DB-backed).

Materialization is idempotent and only for CURRENT repair-eligible findings; missing/stale/incomplete
evaluation is an operational hold that never clears an Issue; a current satisfied evaluation verifies its
Issue; every fidelity RepairTask is HUMAN_REQUIRED. Reports are produced by the real evaluator with a
fake adapter (no live model).
"""

from __future__ import annotations

from datetime import UTC, datetime

from conftest import seed_scene_packet
from sqlalchemy import select
from test_scene_fidelity_evaluator import _body, _clause, _req, _runner

from dominion.shared.enums import IssueStatus, RepairAuthorityLevel, RepairTaskStatus
from dominion.shared.models import Beat, Book, Chapter, Critique, DraftAttempt, Issue, ProductionRun, RepairTask, Scene
from dominion.workers import production_fidelity
from dominion.workers.scene_fidelity.evaluator import evaluate_scene_fidelity
from dominion.workers.scene_fidelity.models import EvidenceAnchor

PROSE = "Marcus sealed the door and she stayed."
_LOST = ("lost", [EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="contradiction")])
_SATISFIED = ("satisfied", [EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="satisfaction")])


def _serra_body():
    return _body([_req("req-1", "relationship_turn", [_clause("cl-1")])])


async def _setup(s, *, body=None, results=None, prose=PROSE, make_report=True):
    body = body or _serra_body()
    book = Book(title="F")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Serra")
    s.add(ch)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status="approved", beat_text="b")
    s.add(beat)
    await s.flush()
    sp = await seed_scene_packet(s, chapter=ch, beat=beat, body=body)
    scene = Scene(chapter_id=ch.id, scene_no=1, scene_packet_id=sp.id, prose=prose)
    s.add(scene)
    await s.flush()
    da = DraftAttempt(scene_id=scene.id, scene_packet_id=sp.id, stage="final_rendered", prose=prose)
    s.add(da)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=ch.id, status="running")
    s.add(run)
    await s.flush()
    if make_report:
        await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="post_draft",
            adapter_runner=_runner(results or {"cl-1": _LOST}),
        )
    return run, scene, sp, da


async def _issues(s, run):
    return (await s.execute(select(Issue).where(Issue.production_run_id == run.id))).scalars().all()


async def test_repair_eligible_materializes_a_human_required_issue(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        result = await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        assert len(result.created_issue_ids) == 1
        issue = (await _issues(s, run))[0]
        assert issue.validator == "scene_fidelity"
        assert issue.severity == "repair"
        assert issue.auto_repair_allowed is False
        assert issue.payload_json["clause_id"] == "cl-1"
        # a fidelity RepairTask is ALWAYS human-required (ADR 0018).
        task = (await s.execute(select(RepairTask).where(RepairTask.production_run_id == run.id))).scalar_one()
        assert task.authority_level == RepairAuthorityLevel.HUMAN_REQUIRED
        assert task.status == RepairTaskStatus.WAITING_FOR_HUMAN
        assert task.requires_human_approval is True
        # the projected Critique is persisted.
        critique = (await s.execute(select(Critique).where(Critique.reviewer == "scene_fidelity"))).scalar_one()
        assert critique.severity == "repair"


async def test_triage_is_idempotent(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        first = await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        second = await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        assert len(first.created_issue_ids) == 1
        assert len(second.created_issue_ids) == 0  # keyed by (run, fidelity_critique) — no duplicate
        assert len(await _issues(s, run)) == 1


async def test_missing_report_is_an_operational_hold(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s, make_report=False)
        result = await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        assert result.created_issue_ids == []
        assert any("no fidelity evaluation report" in h for h in result.operational_holds)


async def test_stale_report_is_a_hold_and_creates_no_issue(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        # Edit the packet's active contract AFTER the report — the fingerprint changes, so the report is
        # stale. A stale report is an operational hold, never a prose failure or an Issue (ADR 0010).
        sp.body = _body([_req("req-1", "relationship_turn", [_clause("cl-1", kind="dialogue")])])
        await s.flush()
        result = await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        assert result.created_issue_ids == []
        assert any("stale evaluation" in h for h in result.operational_holds)
        assert await _issues(s, run) == []


async def test_current_satisfied_verifies_a_prior_issue(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        issue = (await _issues(s, run))[0]
        # A new, clearly-later draft attempt whose fresh report shows the clause satisfied (explicit
        # created_at so it is unambiguously the current final attempt for this scene).
        da2 = DraftAttempt(
            scene_id=scene.id,
            scene_packet_id=sp.id,
            stage="final_rendered",
            prose=PROSE,
            created_at=datetime(2999, 1, 1, tzinfo=UTC),
        )
        s.add(da2)
        await s.flush()
        await evaluate_scene_fidelity(
            s, scene=scene, draft_attempt=da2, packet=sp, trigger="manual", adapter_runner=_runner({"cl-1": _SATISFIED})
        )
        await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        await s.refresh(issue)
        assert issue.status == IssueStatus.VERIFIED.value


async def test_operational_hold_does_not_clear_a_prior_issue(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)
        await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        issue = (await _issues(s, run))[0]
        # Materialization queues a human-required repair task, so the Issue is REPAIR_QUEUED (still open).
        assert issue.status == IssueStatus.REPAIR_QUEUED.value
        # Now make the evaluation stale — the prior Issue must stay unresolved (never cleared by a
        # missing/stale complaint, ADR 0020).
        sp.body = _body([_req("req-1", "relationship_turn", [_clause("cl-1", kind="dialogue")])])
        await s.flush()
        await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
        await s.refresh(issue)
        assert issue.status == IssueStatus.REPAIR_QUEUED.value
