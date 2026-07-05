"""Unit tests for chapter-packet approval policy (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from dominion.shared.enums import PacketConfidence, PacketStatus
from dominion.shared.models import ChapterPacket
from dominion.workers.packet import approval_policy


def _packet(**kwargs: object) -> ChapterPacket:
    defaults = dict(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        status=PacketStatus.PROPOSED,
        confidence=PacketConfidence.GREEN,
        body={},
        open_questions={"items": []},
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return ChapterPacket(**defaults)  # type: ignore[arg-type]


def test_can_approve_blocked():
    p = _packet(status=PacketStatus.BLOCKED)
    refusal = approval_policy.can_approve(p)
    assert refusal is not None
    assert "blocked" in refusal.detail


def test_can_approve_open_questions():
    p = _packet(open_questions={"items": ["who is the traitor?"]})
    refusal = approval_policy.can_approve(p)
    assert refusal is not None
    assert "open questions" in refusal.detail


def test_can_approve_clean_proposed():
    assert approval_policy.can_approve(_packet()) is None


def test_red_confidence_is_advisory_not_a_gate():
    # Confidence comes from LLM signals (author self-assessment / QA verdict floor). It is shown to the
    # human, never a gate: a red, repair-laden packet is approvable (approve-with-repairs).
    p = _packet(
        confidence=PacketConfidence.RED,
        qa_warnings={
            "residual_risks": [],
            "issues": [{"kind": "leak", "detail": "x", "severity": "repair", "blocks_drafting": False}],
            "violations": [{"kind": "roster_double_bucketed", "severity": "repair", "blocks_drafting": False}],
        },
    )
    assert approval_policy.can_approve(p) is None
    out = approval_policy.enrich_packet_out(p)
    assert out.can_approve is True


def test_can_derive_scene_packets_requires_approved():
    p = _packet(status=PacketStatus.PROPOSED)
    refusal = approval_policy.can_derive_scene_packets(p)
    assert refusal is not None
    assert approval_policy.can_derive_scene_packets(None) is not None
    assert approval_policy.can_derive_scene_packets(_packet(status=PacketStatus.APPROVED)) is None


def test_enrich_packet_out_can_approve():
    p = _packet()
    out = approval_policy.enrich_packet_out(p)
    assert out.can_approve is True
    assert out.approval_state == "approvable"
    assert out.approval_blockers == []

    blocked = _packet(status=PacketStatus.BLOCKED)
    out2 = approval_policy.enrich_packet_out(blocked)
    assert out2.can_approve is False
    assert out2.approval_state == "blocked"
    assert out2.approval_blockers


def test_enrich_packet_out_approved_is_never_silent():
    # The old DTO shipped can_approve=False with EMPTY blockers for an approved packet — a greyed
    # Approve button with no reason. Every non-approvable state must now carry one.
    out = approval_policy.enrich_packet_out(_packet(status=PacketStatus.APPROVED))
    assert out.can_approve is False
    assert out.approval_state == "already_approved"
    assert out.approval_blockers and "already approved" in out.approval_blockers[0]


def test_enrich_packet_out_open_questions_state():
    out = approval_policy.enrich_packet_out(_packet(open_questions={"items": ["who is the traitor?"]}))
    assert out.can_approve is False
    assert out.approval_state == "open_questions"
    assert out.approval_blockers == ["resolve the packet's open questions first"]


def test_approve_gate_unchanged_for_approved_rows():
    # The endpoints' real gate must stay idempotent for re-approve: only the DTO reports
    # already_approved; can_approve() itself does not refuse an approved packet.
    assert approval_policy.can_approve(_packet(status=PacketStatus.APPROVED)) is None
