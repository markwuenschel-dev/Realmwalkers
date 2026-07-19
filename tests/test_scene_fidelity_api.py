"""Lane 7 — SceneFidelity API endpoints (called directly with a db_factory session, the repo pattern).

Covers the deterministic author surfaces: packet accept/refine/replace with validation feedback, the
read-only scene fidelity status, and preview create/accept/reject + issue override (reason required).
"""

from __future__ import annotations

import pytest
from conftest import seed_scene_packet
from fastapi import HTTPException
from test_scene_fidelity_production import _issues, _setup

from dominion.api.routers import production as prod_router
from dominion.api.routers import scene_packets as sp_router
from dominion.api.routers import scenes as scenes_router
from dominion.shared.models import Beat, Book, Chapter, Scene
from dominion.shared.schemas import (
    FidelityAcceptIn,
    FidelityRequirementActionIn,
    IssueOverrideIn,
    RepairPreviewActionIn,
    RepairPreviewCreateIn,
)
from dominion.workers import production_fidelity

_REQ = {
    "requirement_id": "req-1",
    "mode": "relationship_turn",
    "post_draft_policy": "export_required",
    "clauses": [
        {
            "clause_id": "cl-1",
            "enforcement": "hard",
            "statement": "Serra's agency is preserved.",
            "satisfaction_criterion": {"evidence_kind": "state_change", "statement": "shown on the page"},
            "depends_on_clause_ids": [],
        }
    ],
}


async def _seed_packet(s, body):
    book = Book(title="F")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Serra")
    s.add(ch)
    await s.flush()
    beat = Beat(chapter_id=ch.id, scene_no=1, status="approved", beat_text="b")
    s.add(beat)
    await s.flush()
    return await seed_scene_packet(s, chapter=ch, beat=beat, body=body)


# --- packet author actions ------------------------------------------------------------------------


async def test_accept_suggestions_endpoint_mints_active_requirements(db_factory) -> None:
    async with db_factory() as s:
        sp = await _seed_packet(s, {"suggested_fidelity_requirements": [_REQ]})
        out = await sp_router.accept_fidelity_suggestions(sp.id, FidelityAcceptIn(), session=s)
        assert out.violations == []
        assert len(out.active_requirements) == 1
        assert out.active_requirements[0]["requirement_id"] != "req-1"  # server-minted identity
        assert out.suggested_requirements == []


async def test_accept_invalid_suggestion_is_422(db_factory) -> None:
    bad = {**_REQ, "clauses": [{**_REQ["clauses"][0], "satisfaction_criterion": None}]}
    async with db_factory() as s:
        sp = await _seed_packet(s, {"suggested_fidelity_requirements": [bad]})
        with pytest.raises(HTTPException) as ei:
            await sp_router.accept_fidelity_suggestions(sp.id, FidelityAcceptIn(), session=s)
    assert ei.value.status_code == 422


async def test_refine_preserves_identity_and_rejects_mode_change(db_factory) -> None:
    async with db_factory() as s:
        sp = await _seed_packet(s, {"fidelity_contract_version": 1, "fidelity_requirements": [_REQ]})
        refined = {**_REQ, "clauses": [{**_REQ["clauses"][0], "statement": "A clearer statement."}]}
        out = await sp_router.refine_fidelity_requirement(
            sp.id, FidelityRequirementActionIn(requirement_id="req-1", requirement=refined), session=s
        )
        assert out.violations == []
        assert out.active_requirements[0]["requirement_id"] == "req-1"
        assert out.active_requirements[0]["clauses"][0]["statement"] == "A clearer statement."
        with pytest.raises(HTTPException) as ei:
            await sp_router.refine_fidelity_requirement(
                sp.id,
                FidelityRequirementActionIn(requirement_id="req-1", requirement={**_REQ, "mode": "combat_blocking"}),
                session=s,
            )
    assert ei.value.status_code == 422


async def test_replace_mints_new_identity(db_factory) -> None:
    async with db_factory() as s:
        sp = await _seed_packet(s, {"fidelity_contract_version": 1, "fidelity_requirements": [_REQ]})
        out = await sp_router.replace_fidelity_requirement(
            sp.id, FidelityRequirementActionIn(requirement_id="req-1", requirement=_REQ), session=s
        )
        assert out.violations == []
        assert out.active_requirements[0]["requirement_id"] != "req-1"


# --- scene fidelity status ------------------------------------------------------------------------


async def test_scene_fidelity_endpoint_reports_a_current_report(db_factory) -> None:
    async with db_factory() as s:
        run, scene, sp, da = await _setup(s)  # a current LOST report
        out = await scenes_router.scene_fidelity(scene.id, session=s)
        assert out.has_report is True
        assert out.is_current is True
        assert [e.result for e in out.clause_evaluations] == ["lost"]


async def test_scene_fidelity_endpoint_is_inert_without_a_contract(db_factory) -> None:
    async with db_factory() as s:
        sp = await _seed_packet(s, {"reader_state": {}})
        scene = Scene(chapter_id=sp.chapter_id, scene_no=1, scene_packet_id=sp.id, prose="x")
        s.add(scene)
        await s.flush()
        out = await scenes_router.scene_fidelity(scene.id, session=s)
        assert out.has_report is False
        assert out.currentness_reason == "no_active_contract"


# --- preview + override ---------------------------------------------------------------------------


async def _issue(s):
    run, scene, sp, da = await _setup(s)
    await production_fidelity.triage_scene_fidelity_for_production(s, run=run)
    return run, scene, (await _issues(s, run))[0]


async def test_preview_create_and_accept_endpoints(db_factory) -> None:
    async with db_factory() as s:
        run, scene, issue = await _issue(s)
        preview = await prod_router.create_fidelity_preview(
            issue.id,
            RepairPreviewCreateIn(candidate_prose="Marcus let her choose.", rationale="restore agency"),
            session=s,
        )
        assert preview.status == "active"
        assert preview.body["candidate_prose"] == "Marcus let her choose."
        new_scene = await prod_router.accept_fidelity_preview(preview.id, RepairPreviewActionIn(), session=s)
        assert new_scene.version == scene.version + 1
        assert new_scene.prose == "Marcus let her choose."


async def test_override_endpoint_requires_a_reason(db_factory) -> None:
    async with db_factory() as s:
        run, scene, issue = await _issue(s)
        with pytest.raises(HTTPException) as ei:
            await prod_router.override_fidelity_issue(issue.id, IssueOverrideIn(reason="   "), session=s)
        assert ei.value.status_code == 422
        out = await prod_router.override_fidelity_issue(issue.id, IssueOverrideIn(reason="intentional beat"), session=s)
        assert out.status == "overridden"
