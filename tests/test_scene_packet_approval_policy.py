"""Unit tests for scene-packet approval policy (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import ScenePacket
from dominion.workers.context.types import ScenePacketRequiredError
from dominion.workers.scene_packet import approval_policy


def _sp(**kwargs: object) -> ScenePacket:
    defaults = dict(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        chapter_packet_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.PROPOSED,
        body={},
        qa_verdict=ScenePacketVerdict.APPROVE.value,
        qa_warnings={"residual_risks": [], "issues": []},
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return ScenePacket(**defaults)  # type: ignore[arg-type]


def test_advisory_qa_verdicts_do_not_gate_approval():
    # QA is advisory: REVISE_REQUIRED / BLOCK_DRAFTING verdicts (and repair-severity issues) leave the
    # packet approvable — approve-with-repairs; the repairs still gate final export.
    assert approval_policy.can_approve(_sp(qa_verdict=ScenePacketVerdict.REVISE_REQUIRED.value)) is None
    assert approval_policy.can_approve(_sp(qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value)) is None
    repair_laden = _sp(
        qa_verdict=ScenePacketVerdict.APPROVE_WARN.value,
        qa_warnings={"residual_risks": [], "issues": [{"kind": "leak", "detail": "x", "severity": "repair"}]},
    )
    assert approval_policy.can_approve(repair_laden) is None


def test_blocked_packet_cannot_approve():
    # Behavior-freeze: a BLOCKED packet (deterministic/author/infrastructure gate) still refuses.
    sp = _sp(status=ScenePacketStatus.BLOCKED)
    refusal = approval_policy.can_approve(sp)
    assert refusal is not None
    assert "blocked" in refusal.detail


def test_apply_qa_rerun_none_blocks():
    sp = _sp()
    approval_policy.apply_qa_rerun(sp, None)
    assert sp.status == ScenePacketStatus.BLOCKED
    assert sp.qa_verdict == ScenePacketVerdict.BLOCK_DRAFTING


def test_apply_qa_rerun_block_drafting_verdict_stays_proposed():
    # A usable BLOCK_DRAFTING verdict is advisory — recorded, never a status change.
    sp = _sp()
    approval_policy.apply_qa_rerun(
        sp, {"verdict": ScenePacketVerdict.BLOCK_DRAFTING, "residual_risks": [], "issues": []}
    )
    assert sp.status == ScenePacketStatus.PROPOSED
    assert sp.qa_verdict == ScenePacketVerdict.BLOCK_DRAFTING


def test_apply_qa_rerun_releases_legacy_qa_block_but_not_validation_block():
    # A row blocked by the OLD policy (QA verdict held the block) is released on re-run; a block from
    # a deterministic gate (validation/author) is never released by QA.
    legacy_qa_blocked = _sp(
        status=ScenePacketStatus.BLOCKED,
        qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value,
        qa_warnings={
            "residual_risks": [],
            "blocked_reason": "scene packet QA blocked drafting",
            "blocker_source": "qa",
        },
    )
    approval_policy.apply_qa_rerun(
        legacy_qa_blocked, {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []}
    )
    assert legacy_qa_blocked.status == ScenePacketStatus.PROPOSED

    validation_blocked = _sp(
        status=ScenePacketStatus.BLOCKED,
        qa_warnings={
            "residual_risks": [],
            "blocked_reason": "deterministic validation failed: no scene_no",
            "blocker_source": "validation",
        },
    )
    approval_policy.apply_qa_rerun(
        validation_blocked, {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []}
    )
    assert validation_blocked.status == ScenePacketStatus.BLOCKED
    assert validation_blocked.qa_warnings["blocker_source"] == "validation"
    assert validation_blocked.qa_warnings["blocked_reason"] == "deterministic validation failed: no scene_no"


def test_assert_draft_ready_stale():
    sp = _sp(status=ScenePacketStatus.STALE, stale_reason="upstream changed")
    with pytest.raises(ScenePacketRequiredError, match="stale"):
        approval_policy.assert_draft_ready(sp)


def test_assert_draft_ready_unapproved():
    sp = _sp(status=ScenePacketStatus.PROPOSED)
    with pytest.raises(ScenePacketRequiredError, match="not approved"):
        approval_policy.assert_draft_ready(sp)


def test_enrich_scene_packet_out():
    sp = _sp()
    out = approval_policy.enrich_scene_packet_out(sp)
    assert out.can_approve is True
    # An advisory REVISE_REQUIRED verdict no longer disables approval.
    sp2 = _sp(qa_verdict=ScenePacketVerdict.REVISE_REQUIRED.value)
    out2 = approval_policy.enrich_scene_packet_out(sp2)
    assert out2.can_approve is True
    assert out2.approval_blockers == []
    # A BLOCKED row still surfaces its reason and disables approval.
    sp3 = _sp(status=ScenePacketStatus.BLOCKED, qa_warnings={"blocked_reason": "author returned thin body"})
    out3 = approval_policy.enrich_scene_packet_out(sp3)
    assert out3.can_approve is False
    assert out3.approval_blockers == ["author returned thin body"]
