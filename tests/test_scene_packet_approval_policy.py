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


def test_has_blocking_qa_verdicts():
    assert approval_policy.has_blocking_qa(_sp(qa_verdict=ScenePacketVerdict.REVISE_REQUIRED.value))
    assert approval_policy.has_blocking_qa(_sp(qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value))
    assert not approval_policy.has_blocking_qa(_sp(qa_verdict=ScenePacketVerdict.APPROVE.value))


def test_has_blocking_qa_block_issue():
    sp = _sp(
        qa_verdict=ScenePacketVerdict.APPROVE_WARN.value,
        qa_warnings={"issues": [{"severity": "block", "kind": "reveal", "detail": "too much"}]},
    )
    assert approval_policy.has_blocking_qa(sp)


def test_revise_required_proposed_after_derive_status():
    body = {
        "known_before_scene": {},
        "learned_during_scene": {},
        "word_budget": {"target": 100},
    }
    status, reason = approval_policy.status_after_author_qa(
        body,
        {"verdict": ScenePacketVerdict.REVISE_REQUIRED},
    )
    assert status == ScenePacketStatus.PROPOSED
    assert reason is None


def test_proposed_with_blocking_qa_cannot_approve():
    sp = _sp(qa_verdict=ScenePacketVerdict.REVISE_REQUIRED.value)
    refusal = approval_policy.can_approve(sp)
    assert refusal is not None
    assert "QA blocks drafting" in refusal.detail


def test_apply_qa_rerun_none_blocks():
    sp = _sp()
    approval_policy.apply_qa_rerun(sp, None)
    assert sp.status == ScenePacketStatus.BLOCKED
    assert sp.qa_verdict == ScenePacketVerdict.BLOCK_DRAFTING


def test_apply_qa_rerun_revise_stays_proposed():
    sp = _sp()
    approval_policy.apply_qa_rerun(
        sp,
        {"verdict": ScenePacketVerdict.REVISE_REQUIRED, "residual_risks": [], "issues": []},
    )
    assert sp.status == ScenePacketStatus.PROPOSED
    assert sp.qa_verdict == ScenePacketVerdict.REVISE_REQUIRED


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
    sp2 = _sp(qa_verdict=ScenePacketVerdict.REVISE_REQUIRED.value)
    out2 = approval_policy.enrich_scene_packet_out(sp2)
    assert out2.can_approve is False
    assert out2.approval_blockers


_VALID_BODY = {
    "known_before_scene": {},
    "learned_during_scene": {},
    "word_budget": {"target": 100},
}


def test_resolve_blocked_reason_priority():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        qa_warnings={"blocked_reason": "from qa_warnings"},
        body={"blocked_reason": "from body"},
    )
    assert approval_policy.resolve_blocked_reason(sp) == "from qa_warnings"


def test_resolve_blocked_reason_falls_back_to_body():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        qa_warnings={"residual_risks": [], "issues": []},
        body={**_VALID_BODY, "blocked_reason": "author merge failed"},
    )
    assert approval_policy.resolve_blocked_reason(sp) == "author merge failed"


def test_infer_blocker_source_blocked_with_approve_is_derive():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        body=_VALID_BODY,
        qa_verdict=ScenePacketVerdict.APPROVE.value,
        qa_warnings={"residual_risks": [], "issues": []},
    )
    assert approval_policy.infer_blocker_source(sp, "stale gate") == "derive"


def test_approval_blockers_returns_reason_for_blocked():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        qa_warnings={"blocked_reason": "section truncated"},
        body=_VALID_BODY,
    )
    blockers = approval_policy.approval_blockers(sp)
    assert blockers == ["section truncated"]


def test_apply_qa_rerun_preserves_author_blocker_on_approve():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        body={"blocked_reason": "author returned incomplete body"},
        qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value,
        qa_warnings={"blocked_reason": "author returned incomplete body"},
    )
    approval_policy.apply_qa_rerun(
        sp,
        {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []},
    )
    assert sp.status == ScenePacketStatus.BLOCKED
    assert sp.qa_verdict == ScenePacketVerdict.APPROVE
    assert sp.qa_warnings["blocked_reason"] == "author returned incomplete body"


def test_apply_qa_rerun_preserves_derive_blocker_on_approve():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        body={**_VALID_BODY, "blocked_reason": "derive gate held"},
        qa_verdict=ScenePacketVerdict.APPROVE.value,
        qa_warnings={"blocked_reason": "derive gate held"},
    )
    approval_policy.apply_qa_rerun(
        sp,
        {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []},
    )
    assert sp.qa_warnings["blocked_reason"] == "derive gate held"


def test_apply_qa_rerun_does_not_preserve_stale_qa_blocker_on_approve():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        body=_VALID_BODY,
        qa_verdict=ScenePacketVerdict.BLOCK_DRAFTING.value,
        qa_warnings={"blocked_reason": "old QA block", "residual_risks": [], "issues": []},
    )
    approval_policy.apply_qa_rerun(
        sp,
        {"verdict": ScenePacketVerdict.APPROVE, "residual_risks": [], "issues": []},
    )
    assert sp.status == ScenePacketStatus.BLOCKED
    assert sp.qa_verdict == ScenePacketVerdict.APPROVE
    assert sp.qa_warnings["blocked_reason"] == approval_policy._STALE_GATE_RECONCILIATION
    assert sp.qa_warnings["blocked_reason"] != "old QA block"


def test_apply_qa_rerun_sets_blocked_reason_on_block_drafting():
    sp = _sp(status=ScenePacketStatus.PROPOSED, body=_VALID_BODY)
    approval_policy.apply_qa_rerun(
        sp,
        {
            "verdict": ScenePacketVerdict.BLOCK_DRAFTING,
            "residual_risks": [],
            "issues": [{"severity": "block", "kind": "reveal", "detail": "too much"}],
        },
    )
    assert sp.status == ScenePacketStatus.BLOCKED
    assert "too much" in sp.qa_warnings["blocked_reason"]


def test_enrich_scene_packet_out_blocked_fields():
    sp = _sp(
        status=ScenePacketStatus.BLOCKED,
        body=_VALID_BODY,
        qa_verdict=ScenePacketVerdict.APPROVE.value,
        qa_warnings={"blocked_reason": "derive gate held"},
    )
    out = approval_policy.enrich_scene_packet_out(sp)
    assert out.blocked_reason == "derive gate held"
    assert out.blocker_source == "derive"
    assert out.approval_blockers == ["derive gate held"]
