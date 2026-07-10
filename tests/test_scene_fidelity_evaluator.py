"""Lane 3B — the evaluator facade + deterministic merger (DB-backed, fake adapters).

The merger's guarantees are what matter here and they are all deterministic (no live model): complete
hard-clause coverage, adapter_failed for a failed mode, blocked_by_dependency for an unevaluable
prerequisite (but NOT for a merely-lost one), positive satisfied anchors preserved, and one immutable
report Artifact with correct provenance. A fake AdapterRunner is injected so nothing calls an LLM.
"""

from __future__ import annotations

from conftest import seed_scene_packet

from dominion.shared.models import Beat, Book, Chapter, DraftAttempt, Scene
from dominion.workers.scene_fidelity import fidelity_contract_fingerprint
from dominion.workers.scene_fidelity.adapters import AdapterOutcome, RawFinding
from dominion.workers.scene_fidelity.evaluator import (
    REPORT_ARTIFACT_TYPE,
    evaluate_scene_fidelity,
    maybe_evaluate_scene_fidelity,
)
from dominion.workers.scene_fidelity.models import EvidenceAnchor

PROSE = 'Marcus stepped between Serra and the door. "Fine," she said.'


def _clause(cid, *, deps=None, kind="state_change"):
    return {
        "clause_id": cid,
        "enforcement": "hard",
        "statement": f"{cid} is preserved",
        "satisfaction_criterion": {"evidence_kind": kind, "statement": "shown on the page"},
        "depends_on_clause_ids": deps or [],
    }


def _req(rid, mode, clauses, *, policy="export_required"):
    return {"requirement_id": rid, "mode": mode, "post_draft_policy": policy, "clauses": clauses}


def _body(reqs):
    return {"fidelity_contract_version": 1, "fidelity_requirements": reqs}


def _runner(results=None, *, failed_modes=()):
    """Fake AdapterRunner. `results` maps clause_id -> (result, anchors); a clause absent from it is
    simply not reported (so the merger must fill not_evaluated). `failed_modes` marks whole modes failed."""
    results = results or {}
    failed = set(failed_modes)

    async def runner(mode, clauses, *, prose, scene_context, budget):
        if mode.value in failed:
            return AdapterOutcome(mode.value, [], "m", "m", False, "failed", "boom")
        findings = []
        for c in clauses:
            cid = c["clause_id"]
            if cid in results:
                res, anchors = results[cid]
                findings.append(RawFinding(clause_id=cid, result=res, evidence_anchors=anchors, explanation="e"))
        return AdapterOutcome(mode.value, findings, "m", "m", False, "ok")

    return runner


async def _seed(s, body, *, prose=PROSE):
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
    return scene, da, sp


def _results(artifact):
    return {e["clause_id"]: e["result"] for e in artifact.body["clause_evaluations"]}


async def test_every_active_clause_gets_exactly_one_evaluation(db_factory) -> None:
    body = _body([_req("req-1", "relationship_turn", [_clause("cl-1"), _clause("cl-2")])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner({"cl-1": ("lost", [])}),
        )
        results = _results(art)
    assert set(results) == {"cl-1", "cl-2"}  # complete coverage
    assert results["cl-1"] == "lost"
    assert results["cl-2"] == "not_evaluated"  # adapter said nothing -> merger fills it, never omits


async def test_adapter_failure_marks_every_owned_clause_failed(db_factory) -> None:
    body = _body([_req("req-1", "combat_blocking", [_clause("cl-1"), _clause("cl-2")])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner(failed_modes=["combat_blocking"]),
        )
        results = _results(art)
    assert results == {"cl-1": "adapter_failed", "cl-2": "adapter_failed"}


async def test_unevaluable_prerequisite_blocks_dependent(db_factory) -> None:
    body = _body([_req("req-1", "spatial_affordance", [_clause("cl-a"), _clause("cl-b", deps=["cl-a"])])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        # cl-a is indeterminate (operationally uncertain) -> cl-b cannot be evaluated.
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner({"cl-a": ("indeterminate", [])}),
        )
        results = _results(art)
    assert results["cl-a"] == "indeterminate"
    assert results["cl-b"] == "blocked_by_dependency"


async def test_lost_prerequisite_does_not_block_dependent(db_factory) -> None:
    body = _body([_req("req-1", "spatial_affordance", [_clause("cl-a"), _clause("cl-b", deps=["cl-a"])])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        # cl-a LOST is a real verdict, not uncertainty -> cl-b is still evaluated on its own (ADR 0012).
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner(
                {
                    "cl-a": ("lost", []),
                    "cl-b": ("satisfied", [EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="satisfaction")]),
                }
            ),
        )
        results = _results(art)
    assert results["cl-a"] == "lost"
    assert results["cl-b"] == "satisfied"


async def test_satisfied_finding_preserves_its_positive_anchor(db_factory) -> None:
    anchor = EvidenceAnchor(start=0, end=6, quote="Marcus", anchor_kind="satisfaction")
    body = _body([_req("req-1", "relationship_turn", [_clause("cl-1")])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner({"cl-1": ("satisfied", [anchor])}),
        )
        evaluation = art.body["clause_evaluations"][0]
    assert evaluation["result"] == "satisfied"
    assert evaluation["evidence_anchors"][0]["quote"] == "Marcus"
    assert evaluation["evidence_anchors"][0]["anchor_kind"] == "satisfaction"


async def test_report_artifact_carries_correct_provenance(db_factory) -> None:
    body = _body([_req("req-1", "relationship_turn", [_clause("cl-1")])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="post_draft",
            adapter_runner=_runner({"cl-1": ("lost", [])}),
        )
        assert art.artifact_type == REPORT_ARTIFACT_TYPE
        assert art.domain_table == "draft_attempts"
        assert art.domain_id == da.id
        assert art.body["draft_attempt_id"] == str(da.id)
        assert art.body["scene_id"] == str(scene.id)
        assert art.body["packet_contract_fingerprint"] == fidelity_contract_fingerprint(body)
        assert art.body["prose_hash"].startswith("sha256:")


async def test_maybe_evaluate_skips_an_inert_packet(db_factory) -> None:
    legacy_body = {"reader_state": {"pov": "Serra"}, "required_beats": ["Serra reaches the tower."]}
    async with db_factory() as s:
        scene, da, sp = await _seed(s, legacy_body)
        art = await maybe_evaluate_scene_fidelity(s, scene=scene, draft_attempt=da, packet=sp, trigger="post_draft")
    assert art is None
